from __future__ import annotations

import asyncio
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
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    query,
)
from pydantic import ValidationError
from structlog.typing import FilteringBoundLogger

from sentia_sidecar.protocol import AgentProvider, RepositoryAnswer, TokenUsage
from sentia_sidecar.repository import (
    MAX_SELECTED_FILES,
    RepositoryError,
    RepositoryManifest,
    build_selected_repository_context,
    expand_repository_selection,
)
from sentia_sidecar.repository_intelligence import (
    SELECTION_SYSTEM_PROMPT,
    SPEECH_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    FileSelection,
    ModelAnswer,
    RepositoryIntelligenceError,
    SpeechAnswer,
    bounded_text,
    speech_render_prompt,
    speech_stream_prompt,
    validate_evidence,
)

logger = structlog.get_logger(__name__)

CONTENT_RETRIEVAL_TIMEOUT_SECONDS = 15.0

QueryFunction = Callable[..., AsyncIterator[Any]]


class AnthropicServiceError(RepositoryIntelligenceError):
    pass


class AnthropicRepositoryService:
    provider = AgentProvider.CLAUDE

    def __init__(
        self,
        model: str,
        *,
        query_function: QueryFunction = query,
    ) -> None:
        self.model = model
        self._api_key: str | None = None
        self._query = query_function

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
        stage = "file_selection"
        total_started = time.perf_counter()
        request_logger = logger.bind(diagnostic_id=diagnostic_id, model=self.model)
        request_logger.info(
            "repository_question_started",
            manifest_files=manifest.files_scanned,
            manifest_chars=len(manifest.prompt),
            manifest_cache_source=manifest.cache_source,
            question_chars=len(question.strip()),
            runtime="claude_agent_sdk",
        )

        try:
            selection_started = time.perf_counter()
            selection_result = await self._run_structured_turn(
                prompt=(
                    f"QUESTION:\n{question.strip()}\n\n"
                    f"{manifest.prompt}\n\n"
                    f"Select no more than {MAX_SELECTED_FILES} files. Return a short "
                    "selection report with a read mode and reason for each file."
                ),
                system_prompt=SELECTION_SYSTEM_PROMPT,
                response_schema=FileSelection.model_json_schema(by_alias=True),
                workspace_path=manifest.root,
                diagnostic_id=diagnostic_id,
                stage=stage,
            )
            selection = FileSelection.model_validate(selection_result.structured_output)
            selection_elapsed_ms = _elapsed_ms(selection_started)
            requested = expand_repository_selection(
                manifest,
                [(choice.path, choice.read_mode) for choice in selection.files],
                question,
            )
            request_logger.info(
                "shortlist_agent_completed",
                selected_count=len(selection.files),
                selected_files=[choice.path for choice in selection.files],
                expanded_count=len(requested),
                expanded_files=[path for path, _ in requested],
                read_modes=[choice.read_mode for choice in selection.files],
                elapsed_ms=selection_elapsed_ms,
                session_id=selection_result.session_id,
                turns=selection_result.num_turns,
                runtime="claude_agent_sdk",
            )

            stage = "content_read"
            read_started = time.perf_counter()
            try:
                async with asyncio.timeout(CONTENT_RETRIEVAL_TIMEOUT_SECONDS):
                    repository = await asyncio.to_thread(
                        build_selected_repository_context,
                        manifest,
                        requested,
                    )
            except TimeoutError as error:
                raise AnthropicServiceError(
                    "Sentia's content reader exceeded 15 seconds. Try a narrower question.",
                    504,
                ) from error
            content_read_elapsed_ms = _elapsed_ms(read_started)
            request_logger.info(
                "content_reader_completed",
                evidence_chars=len(repository.prompt),
                files_read=repository.files_read,
                selected_files=list(repository.selected_files),
                elapsed_ms=content_read_elapsed_ms,
                timeout_seconds=CONTENT_RETRIEVAL_TIMEOUT_SECONDS,
            )

            selection_report = "\n".join(
                f"- {choice.path} ({choice.read_mode}): {choice.reason}"
                for choice in selection.files
                if choice.path in manifest.files
            )
            stage = "repository_answer"
            answer_started = time.perf_counter()
            answer_result = await self._run_structured_turn(
                prompt=(
                    f"QUESTION:\n{question.strip()}\n\n"
                    f"FILE-SELECTION REPORT:\n{selection.rationale}\n"
                    f"{selection_report}\n\n"
                    f"{repository.prompt}\n\n"
                    "Return one coherent answer plus the smallest useful set of citations. "
                    "Choose recommendedMode=brainstorm for explanation, exploration, "
                    "questions, or open-ended ideation. Choose recommendedMode=build when "
                    "the user asks to plan, implement, fix, add, remove, refactor, or "
                    "test a concrete change. Briefly explain that routing choice in "
                    "modeReason."
                ),
                system_prompt=SYSTEM_PROMPT,
                response_schema=ModelAnswer.model_json_schema(by_alias=True),
                workspace_path=manifest.root,
                diagnostic_id=diagnostic_id,
                stage=stage,
            )
            draft = ModelAnswer.model_validate(answer_result.structured_output)
            answer_elapsed_ms = _elapsed_ms(answer_started)
        except ValidationError as error:
            _log_processing_error(request_logger, stage, error)
            raise _friendly_error(
                error,
                diagnostic_id=diagnostic_id,
                stage=stage,
            ) from error
        except AnthropicServiceError as error:
            _log_processing_error(request_logger, stage, error)
            raise
        except RepositoryError as error:
            _log_processing_error(request_logger, stage, error)
            raise
        except Exception as error:
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
        usage = _combined_agent_usage(selection_result, answer_result)
        result = RepositoryAnswer(
            answer=answer_text,
            spoken_answer=draft.spoken_answer.strip(),
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
            selection_elapsed_ms=selection_elapsed_ms,
            content_read_elapsed_ms=content_read_elapsed_ms,
            answer_elapsed_ms=answer_elapsed_ms,
            total_elapsed_ms=_elapsed_ms(total_started),
            selection_session_id=selection_result.session_id,
            answer_session_id=answer_result.session_id,
            selection_turns=selection_result.num_turns,
            answer_turns=answer_result.num_turns,
            cost_usd=(selection_result.total_cost_usd or 0) + (answer_result.total_cost_usd or 0),
            runtime="claude_agent_sdk",
        )
        return result

    async def _run_structured_turn(
        self,
        *,
        prompt: str,
        system_prompt: str,
        response_schema: dict[str, Any],
        workspace_path: Path,
        diagnostic_id: str,
        stage: str,
    ) -> ResultMessage:
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
            system_prompt=system_prompt,
            mcp_servers={},
            strict_mcp_config=True,
            permission_mode="dontAsk",
            cwd=workspace_path,
            env={"ANTHROPIC_API_KEY": self._api_key},
            max_turns=2,
            model=self.model,
            setting_sources=[],
            output_format={"type": "json_schema", "schema": response_schema},
        )
        result_message: ResultMessage | None = None
        try:
            async for message in self._query(prompt=prompt, options=options):
                if isinstance(message, ResultMessage):
                    result_message = message
        except Exception as error:
            if result_message is not None and result_message.is_error:
                raise _agent_result_error(result_message, diagnostic_id, stage) from error
            raise
        if result_message is None:
            raise AnthropicServiceError(
                "Claude Agent SDK ended without a result during "
                f"{stage.replace('_', ' ')}. Diagnostic ID: {diagnostic_id}."
            )
        if result_message.is_error or result_message.subtype != "success":
            raise _agent_result_error(result_message, diagnostic_id, stage)
        return result_message

    async def render_speech(
        self,
        answer: str,
        workspace_path: Path,
    ) -> SpeechAnswer:
        diagnostic_id = f"sentia_{uuid4().hex[:12]}"
        speech, _ = await self._render_speech(answer, workspace_path, diagnostic_id)
        return speech

    async def stream_speech(
        self,
        answer: str,
        workspace_path: Path,
    ) -> AsyncIterator[str]:
        if self._api_key is None:
            raise AnthropicServiceError("Connect an Anthropic API key before asking Sentia.", 409)
        diagnostic_id = f"sentia_{uuid4().hex[:12]}"
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
            include_partial_messages=True,
        )
        emitted = 0
        result_message: ResultMessage | None = None
        try:
            async for message in self._query(
                prompt=speech_stream_prompt(answer),
                options=options,
            ):
                if isinstance(message, StreamEvent):
                    delta = _speech_text_delta(message)
                    if not delta:
                        continue
                    remaining = 3_000 - emitted
                    if remaining <= 0:
                        break
                    chunk = delta[:remaining]
                    emitted += len(chunk)
                    yield chunk
                    if len(chunk) < len(delta):
                        break
                    continue
                if isinstance(message, ResultMessage):
                    result_message = message
        except Exception as error:
            raise AnthropicServiceError(
                "Claude could not stream the validated answer for speech. "
                f"Diagnostic ID: {diagnostic_id}."
            ) from error
        if result_message is None and emitted < 3_000:
            raise AnthropicServiceError(
                f"Claude ended without a speech rendering. Diagnostic ID: {diagnostic_id}."
            )
        if result_message is not None and (
            result_message.is_error or result_message.subtype != "success"
        ):
            raise AnthropicServiceError(
                "Claude could not stream the validated answer for speech. "
                f"Diagnostic ID: {diagnostic_id}."
            )
        if emitted == 0 and result_message is not None:
            fallback = result_message.result
            if isinstance(fallback, str) and fallback.strip():
                yield fallback[:3_000]
                return
            raise AnthropicServiceError(
                f"Claude returned an empty speech rendering. Diagnostic ID: {diagnostic_id}."
            )

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


def _speech_text_delta(message: StreamEvent) -> str | None:
    event = message.event
    if event.get("type") != "content_block_delta":
        return None
    delta = event.get("delta")
    if not isinstance(delta, dict) or delta.get("type") != "text_delta":
        return None
    text = delta.get("text")
    return text if isinstance(text, str) and text else None


def _agent_result_error(
    message: ResultMessage,
    diagnostic_id: str,
    stage: str,
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
        "Claude Agent SDK could not complete repository "
        f"{stage.replace('_', ' ')}.{suffix} "
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


def _combined_agent_usage(*messages: ResultMessage) -> TokenUsage:
    usages = [_agent_usage(message.usage) for message in messages]
    return TokenUsage(
        input_tokens=sum(usage.input_tokens for usage in usages),
        output_tokens=sum(usage.output_tokens for usage in usages),
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
