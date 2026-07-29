from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog
from anthropic import (
    APIConnectionError,
    APIStatusError,
    AsyncAnthropic,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    ToolUseBlock,
    query,
)
from pydantic import ValidationError
from structlog.typing import FilteringBoundLogger

from sentia_sidecar.protocol import AgentProvider, RepositoryAnswer, TokenUsage
from sentia_sidecar.repository import RepositoryManifest
from sentia_sidecar.repository_agent_tools import (
    RepositoryAgentTools,
    repository_snapshot_summary,
)
from sentia_sidecar.repository_intelligence import (
    SPEECH_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    ModelAnswer,
    RepositoryIntelligenceError,
    SpeechAnswer,
    bounded_text,
    speech_render_prompt,
    validate_evidence,
)

logger = structlog.get_logger(__name__)

CLAUDE_REPOSITORY_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + """
You are running as a read-only repository investigation agent. Sentia has already built and cached
an eligible-file snapshot using the workspace ignore rules. Use only the provided
search_repository and read_repository_file tools. Start with focused searches over cached paths and
symbol outlines, including likely aliases (for example Claude/Anthropic or agent/LLM/provider).
Read the implementation files needed for the question; do not answer from filenames or outlines
alone. Every cited file must first be read through read_repository_file. Prefer outline mode while
orienting and full mode when the user asks what functions actually do. Stop once the evidence is
sufficient. Never attempt to edit files, invoke shell commands, access the network, or read paths
outside Sentia's eligible snapshot."""
)
SEARCH_TOOL = "mcp__sentia_repository__search_repository"
READ_TOOL = "mcp__sentia_repository__read_repository_file"

QueryFunction = Callable[..., AsyncIterator[Any]]
ToolsFactory = Callable[[RepositoryManifest], RepositoryAgentTools]


class AnthropicServiceError(RepositoryIntelligenceError):
    pass


class AnthropicRepositoryService:
    provider = AgentProvider.CLAUDE

    def __init__(
        self,
        model: str,
        *,
        query_function: QueryFunction = query,
        tools_factory: ToolsFactory = RepositoryAgentTools,
    ) -> None:
        self.model = model
        self._api_key: str | None = None
        self._query = query_function
        self._tools_factory = tools_factory

    @property
    def connected(self) -> bool:
        return self._api_key is not None

    async def connect(self, api_key: str, *, validate: bool = True) -> None:
        normalized = api_key.strip()
        if not normalized:
            raise AnthropicServiceError("Enter an Anthropic API key.", 400)
        if validate:
            client = AsyncAnthropic(api_key=normalized)
            try:
                await client.models.list(limit=1)
            except Exception as error:
                raise _friendly_error(error) from error
            finally:
                await client.close()
        self._api_key = normalized

    async def disconnect(self) -> None:
        self._api_key = None

    async def answer(
        self,
        question: str,
        manifest: RepositoryManifest,
    ) -> RepositoryAnswer:
        if self._api_key is None:
            raise AnthropicServiceError("Connect an Anthropic API key before asking Sentia.", 409)
        diagnostic_id = f"sentia_{uuid4().hex[:12]}"
        stage = "agent_scan"
        total_started = time.perf_counter()
        request_logger = logger.bind(diagnostic_id=diagnostic_id, model=self.model)
        repository_tools = self._tools_factory(manifest)
        request_logger.info(
            "repository_question_started",
            manifest_files=manifest.files_scanned,
            manifest_chars=len(manifest.prompt),
            manifest_cache_source=manifest.cache_source,
            question_chars=len(question.strip()),
            runtime="claude_agent_sdk",
        )

        options = ClaudeAgentOptions(
            tools=[],
            allowed_tools=[SEARCH_TOOL, READ_TOOL],
            disallowed_tools=[
                "Read",
                "Glob",
                "Grep",
                "Edit",
                "Write",
                "Bash",
                "NotebookEdit",
                "WebFetch",
                "WebSearch",
                "Task",
            ],
            system_prompt=CLAUDE_REPOSITORY_SYSTEM_PROMPT,
            mcp_servers={"sentia_repository": repository_tools.server},
            strict_mcp_config=True,
            permission_mode="dontAsk",
            cwd=manifest.root,
            env={"ANTHROPIC_API_KEY": self._api_key},
            max_turns=10,
            model=self.model,
            setting_sources=[],
            output_format={
                "type": "json_schema",
                "schema": ModelAnswer.model_json_schema(by_alias=True),
            },
        )
        result_message: ResultMessage | None = None
        agent_started = time.perf_counter()
        try:
            async for message in self._query(
                prompt=_repository_agent_prompt(question, manifest),
                options=options,
            ):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, ToolUseBlock):
                            request_logger.info(
                                "repository_agent_tool_started",
                                tool=block.name,
                            )
                elif isinstance(message, ResultMessage):
                    result_message = message
        except AnthropicServiceError as error:
            _log_processing_error(request_logger, stage, error)
            raise
        except Exception as error:
            if result_message is not None and result_message.is_error:
                result_error = _agent_result_error(result_message, diagnostic_id)
                _log_processing_error(request_logger, stage, result_error)
                raise result_error from error
            _log_processing_error(request_logger, stage, error)
            raise _friendly_error(
                error,
                diagnostic_id=diagnostic_id,
                stage=stage,
            ) from error

        if result_message is None:
            missing_result = AnthropicServiceError(
                f"Claude Agent SDK ended without a result. Diagnostic ID: {diagnostic_id}."
            )
            _log_processing_error(request_logger, stage, missing_result)
            raise missing_result
        if result_message.is_error or result_message.subtype != "success":
            result_error = _agent_result_error(result_message, diagnostic_id)
            _log_processing_error(request_logger, stage, result_error)
            raise result_error

        stage = "structured_output"
        try:
            draft = ModelAnswer.model_validate(result_message.structured_output)
            repository = repository_tools.build_evidence_context()
        except (ValidationError, ValueError) as error:
            _log_processing_error(request_logger, stage, error)
            raise _friendly_error(
                error,
                diagnostic_id=diagnostic_id,
                stage=stage,
            ) from error

        evidence = validate_evidence(draft.evidence, repository)
        answer_text = draft.answer.strip()
        if not answer_text:
            empty_answer_error = AnthropicServiceError(
                f"Claude returned an empty repository answer. Diagnostic ID: {diagnostic_id}.",
                502,
            )
            _log_processing_error(request_logger, "repository_answer", empty_answer_error)
            raise empty_answer_error
        usage = _agent_usage(result_message.usage)
        result = RepositoryAnswer(
            answer=answer_text,
            evidence=evidence,
            recommended_mode=draft.recommended_mode,
            mode_reason=bounded_text(
                draft.mode_reason,
                fallback="Sentia selected the mode from the user's request.",
                limit=240,
            ),
            model=self.model,
            files_scanned=repository.files_scanned,
            files_read=repository.files_read,
            selected_files=list(repository.selected_files),
            usage=usage,
        )
        request_logger.info(
            "repository_answer_validated",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            evidence_received=len(draft.evidence),
            evidence_accepted=len(evidence),
            agent_elapsed_ms=_elapsed_ms(agent_started),
            total_elapsed_ms=_elapsed_ms(total_started),
            session_id=result_message.session_id,
            turns=result_message.num_turns,
            cost_usd=result_message.total_cost_usd,
            runtime="claude_agent_sdk",
        )
        return result

    async def render_speech(
        self,
        answer: str,
        workspace_path: Path,
    ) -> SpeechAnswer:
        diagnostic_id = f"sentia_{uuid4().hex[:12]}"
        speech, _ = await self._render_speech(answer, workspace_path, diagnostic_id)
        return speech

    async def _render_speech(
        self,
        answer: str,
        workspace_path: Path,
        diagnostic_id: str,
    ) -> tuple[SpeechAnswer, ResultMessage]:
        if self._api_key is None:
            raise AnthropicServiceError("Connect an Anthropic API key before asking Sentia.", 409)
        options = ClaudeAgentOptions(
            tools=[],
            allowed_tools=[],
            disallowed_tools=[
                "Read",
                "Glob",
                "Grep",
                "Edit",
                "Write",
                "Bash",
                "NotebookEdit",
                "WebFetch",
                "WebSearch",
                "Task",
            ],
            system_prompt=SPEECH_SYSTEM_PROMPT,
            mcp_servers={},
            strict_mcp_config=True,
            permission_mode="dontAsk",
            cwd=workspace_path,
            env={"ANTHROPIC_API_KEY": self._api_key},
            max_turns=1,
            model=self.model,
            setting_sources=[],
            output_format={
                "type": "json_schema",
                "schema": SpeechAnswer.model_json_schema(by_alias=True),
            },
        )
        result_message: ResultMessage | None = None
        try:
            async for message in self._query(
                prompt=speech_render_prompt(answer),
                options=options,
            ):
                if isinstance(message, ResultMessage):
                    result_message = message
        except Exception as error:
            if result_message is not None and result_message.is_error:
                raise AnthropicServiceError(
                    "Claude could not render the validated answer for speech. "
                    f"Diagnostic ID: {diagnostic_id}."
                ) from error
            raise
        if result_message is None:
            raise AnthropicServiceError(
                f"Claude ended without a speech rendering. Diagnostic ID: {diagnostic_id}."
            )
        if result_message.is_error or result_message.subtype != "success":
            raise AnthropicServiceError(
                "Claude could not render the validated answer for speech. "
                f"Diagnostic ID: {diagnostic_id}."
            )
        try:
            return (
                SpeechAnswer.model_validate(result_message.structured_output),
                result_message,
            )
        except ValidationError as error:
            raise _friendly_error(
                error,
                diagnostic_id=diagnostic_id,
                stage="speech_render",
            ) from error


def _repository_agent_prompt(question: str, manifest: RepositoryManifest) -> str:
    return (
        f"QUESTION:\n{question.strip()}\n\n"
        f"CACHED SNAPSHOT SUMMARY:\n{repository_snapshot_summary(manifest)}\n\n"
        "Investigate this question autonomously. Search the cached snapshot, read only the "
        "smallest useful set of files, then return the structured answer. Choose "
        "recommendedMode=brainstorm for explanation, exploration, questions, or open-ended "
        "ideation. Choose recommendedMode=build only when the user asks to plan, implement, fix, "
        "add, remove, refactor, or test a concrete change."
    )


def _agent_result_error(
    message: ResultMessage,
    diagnostic_id: str,
) -> AnthropicServiceError:
    detail = next((error.strip() for error in message.errors or [] if error.strip()), None)
    logger.warning(
        "claude_agent_result_error",
        diagnostic_id=diagnostic_id,
        subtype=message.subtype,
        stop_reason=message.stop_reason,
        terminal_reason=message.terminal_reason,
        api_error_status=message.api_error_status,
    )
    if message.api_error_status in {401, 403, 429}:
        status = message.api_error_status
    elif message.subtype in {"error_max_turns", "error_max_budget_usd"}:
        status = 422
    else:
        status = 502
    suffix = f" {detail[:180]}" if detail else ""
    return AnthropicServiceError(
        f"Claude Agent SDK could not complete the repository scan.{suffix} "
        f"Diagnostic ID: {diagnostic_id}.",
        status,
    )


def _agent_usage(usage: dict[str, Any] | None) -> TokenUsage:
    values = usage or {}
    input_tokens = values.get("input_tokens", 0)
    output_tokens = values.get("output_tokens", 0)
    return TokenUsage(
        input_tokens=input_tokens if isinstance(input_tokens, int) else 0,
        output_tokens=output_tokens if isinstance(output_tokens, int) else 0,
    )


def _log_processing_error(
    request_logger: FilteringBoundLogger,
    stage: str,
    error: Exception,
) -> None:
    validation_errors: list[dict[str, object]] | None = None
    if isinstance(error, ValidationError):
        validation_errors = [
            {
                "location": ".".join(str(part) for part in issue["loc"]),
                "type": issue["type"],
                "message": issue["msg"],
            }
            for issue in error.errors(include_url=False, include_context=False, include_input=False)
        ]
    request_logger.error(
        "anthropic_processing_failed",
        stage=stage,
        error_type=type(error).__name__,
        status_code=(
            error.status_code
            if isinstance(error, (AnthropicServiceError, APIStatusError))
            else None
        ),
        validation_errors=validation_errors,
    )


def _friendly_error(
    error: Exception,
    *,
    diagnostic_id: str | None = None,
    stage: str | None = None,
) -> AnthropicServiceError:
    if isinstance(error, AuthenticationError):
        return AnthropicServiceError("Anthropic rejected this API key.", 401)
    if isinstance(error, PermissionDeniedError):
        return AnthropicServiceError("This Anthropic key does not have the required access.", 403)
    if isinstance(error, RateLimitError):
        return AnthropicServiceError("Anthropic rate-limited this request. Try again shortly.", 429)
    if isinstance(error, APIConnectionError):
        return AnthropicServiceError(
            "Sentia could not reach Anthropic. Check your connection.", 503
        )
    if isinstance(error, APIStatusError):
        status_code = error.status_code
        request_id = error.request_id
        logger.warning(
            "anthropic_api_error",
            diagnostic_id=diagnostic_id,
            stage=stage,
            status_code=status_code,
            request_id=request_id,
            error_type=str(error.type) if error.type is not None else None,
        )
        reference = f" Anthropic request ID: {request_id}." if request_id else ""
        if error.status_code == 402:
            return AnthropicServiceError(
                f"Anthropic billing or credits need attention.{reference}", 402
            )
        if status_code == 400:
            return AnthropicServiceError(
                "Anthropic rejected Sentia's request (400). The selected model or request "
                f"format may be incompatible.{reference}",
                400,
            )
        if status_code == 404:
            return AnthropicServiceError(
                f"The selected Anthropic model is unavailable to this account.{reference}",
                404,
            )
        if status_code == 413:
            return AnthropicServiceError(
                f"The repository context is too large for Anthropic.{reference}", 413
            )
        if status_code == 529:
            return AnthropicServiceError(
                f"Anthropic is temporarily overloaded. Try again shortly.{reference}", 503
            )
        if status_code >= 500:
            return AnthropicServiceError(
                f"Anthropic is temporarily unavailable ({status_code}).{reference}", 503
            )
        return AnthropicServiceError(
            f"Anthropic could not complete the request ({status_code}).{reference}",
            status_code,
        )
    if isinstance(error, (ValidationError, ValueError)):
        resolved_stage = (stage or "response").replace("_", " ")
        reference = f" Diagnostic ID: {diagnostic_id}." if diagnostic_id else ""
        return AnthropicServiceError(
            f"Sentia could not validate Anthropic's {resolved_stage}.{reference}"
        )
    logger.error(
        "anthropic_request_error",
        diagnostic_id=diagnostic_id,
        stage=stage,
        error_type=type(error).__name__,
    )
    reference = f" Diagnostic ID: {diagnostic_id}." if diagnostic_id else ""
    return AnthropicServiceError(f"The Anthropic request failed.{reference}")


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1_000, 2)
