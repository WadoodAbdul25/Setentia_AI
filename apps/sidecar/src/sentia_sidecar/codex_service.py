from __future__ import annotations

import asyncio
import copy
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast
from uuid import uuid4

import structlog
from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox, TurnResult
from openai_codex.types import JsonObject
from pydantic import BaseModel, ValidationError

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

CONTENT_RETRIEVAL_TIMEOUT_SECONDS = 15.0
DEFAULT_MODEL_LABEL = "codex-account-default"

logger = structlog.get_logger(__name__)


class CodexRepositoryService:
    provider = AgentProvider.CODEX

    def __init__(self, model: str | None = None) -> None:
        self._requested_model = model
        self.model = model or DEFAULT_MODEL_LABEL
        self._client: AsyncCodex | None = None
        self._isolated_cwd: tempfile.TemporaryDirectory[str] | None = None
        self._startup_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()

    async def answer(
        self,
        question: str,
        manifest: RepositoryManifest,
    ) -> RepositoryAnswer:
        async with self._request_lock:
            return await self._answer_locked(question, manifest)

    async def render_speech(
        self,
        answer: str,
        workspace_path: Path,
    ) -> SpeechAnswer:
        del workspace_path
        diagnostic_id = f"sentia_{uuid4().hex[:12]}"
        async with self._request_lock:
            try:
                codex, _ = await self._codex_client()
                result = await self._run_structured(
                    codex,
                    instructions=SPEECH_SYSTEM_PROMPT,
                    prompt=speech_render_prompt(answer),
                    response_model=SpeechAnswer,
                )
                return _parse_structured_result(
                    result,
                    SpeechAnswer,
                    stage="speech_render",
                    diagnostic_id=diagnostic_id,
                )
            except RepositoryIntelligenceError:
                raise
            except Exception as error:
                await self.close()
                raise RepositoryIntelligenceError(
                    "Codex could not render the answer for speech. "
                    f"Diagnostic ID: {diagnostic_id}.",
                    502,
                ) from error

    async def stream_speech(
        self,
        answer: str,
        workspace_path: Path,
    ) -> AsyncIterator[str]:
        del workspace_path
        diagnostic_id = f"sentia_{uuid4().hex[:12]}"
        async with self._request_lock:
            try:
                codex, _ = await self._codex_client()
                thread = await codex.thread_start(
                    approval_mode=ApprovalMode.deny_all,
                    base_instructions=SPEECH_SYSTEM_PROMPT,
                    cwd=None,
                    ephemeral=True,
                    model=self._requested_model,
                    sandbox=Sandbox.read_only,
                )
                turn = await thread.turn(
                    speech_stream_prompt(answer),
                    approval_mode=ApprovalMode.deny_all,
                    model=self._requested_model,
                    sandbox=Sandbox.read_only,
                )
                emitted = 0
                streamed = False
                async for notification in turn.stream():
                    payload = notification.payload
                    if notification.method == "item/agentMessage/delta":
                        delta = getattr(payload, "delta", None)
                        if not isinstance(delta, str) or not delta:
                            continue
                        remaining = 3_000 - emitted
                        if remaining <= 0:
                            await turn.interrupt()
                            break
                        chunk = delta[:remaining]
                        emitted += len(chunk)
                        streamed = True
                        yield chunk
                        if len(chunk) < len(delta):
                            await turn.interrupt()
                            break
                        continue
                    if notification.method == "item/completed" and not streamed:
                        item = getattr(payload, "item", None)
                        root = getattr(item, "root", item)
                        text = getattr(root, "text", None)
                        if isinstance(text, str) and text:
                            chunk = text[:3_000]
                            emitted = len(chunk)
                            streamed = True
                            yield chunk
                        continue
                    if notification.method == "turn/completed":
                        completed_turn = getattr(payload, "turn", None)
                        status = getattr(completed_turn, "status", None)
                        status_value = getattr(status, "value", status)
                        if status_value == "failed":
                            error = getattr(completed_turn, "error", None)
                            message = getattr(error, "message", None)
                            raise RepositoryIntelligenceError(
                                message
                                or "Codex could not stream the speech rendering. "
                                f"Diagnostic ID: {diagnostic_id}.",
                                502,
                            )
                if emitted == 0:
                    raise RepositoryIntelligenceError(
                        "Codex returned an empty speech rendering. "
                        f"Diagnostic ID: {diagnostic_id}.",
                        502,
                    )
            except RepositoryIntelligenceError:
                raise
            except Exception as error:
                await self.close()
                raise RepositoryIntelligenceError(
                    "Codex could not stream the answer for speech. "
                    f"Diagnostic ID: {diagnostic_id}.",
                    502,
                ) from error

    async def close(self) -> None:
        async with self._startup_lock:
            client = self._client
            isolated_cwd = self._isolated_cwd
            self._client = None
            self._isolated_cwd = None
            if client is not None:
                await client.close()
            if isolated_cwd is not None:
                isolated_cwd.cleanup()

    async def _answer_locked(
        self,
        question: str,
        manifest: RepositoryManifest,
    ) -> RepositoryAnswer:
        diagnostic_id = f"sentia_{uuid4().hex[:12]}"
        stage = "file_selection"
        total_started = time.perf_counter()
        request_logger = logger.bind(
            diagnostic_id=diagnostic_id,
            provider=self.provider,
            model=self.model,
        )
        request_logger.info(
            "repository_question_started",
            manifest_files=manifest.files_scanned,
            manifest_chars=len(manifest.prompt),
            manifest_cache_source=manifest.cache_source,
            question_chars=len(question.strip()),
        )

        try:
            codex, client_reused = await self._codex_client()
            selection_started = time.perf_counter()
            selection_result = await self._run_structured(
                codex,
                instructions=SELECTION_SYSTEM_PROMPT,
                prompt=(
                    f"QUESTION:\n{question.strip()}\n\n"
                    f"{manifest.prompt}\n\n"
                    f"Select no more than {MAX_SELECTED_FILES} files. Return a short "
                    "selection report with a read mode and reason for each file."
                ),
                response_model=FileSelection,
            )
            selection_elapsed_ms = _elapsed_ms(selection_started)
            selection = _parse_structured_result(
                selection_result,
                FileSelection,
                stage=stage,
                diagnostic_id=diagnostic_id,
            )
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
                client_reused=client_reused,
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
                raise RepositoryIntelligenceError(
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
            answer_result = await self._run_structured(
                codex,
                instructions=SYSTEM_PROMPT,
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
                response_model=ModelAnswer,
            )
            answer_elapsed_ms = _elapsed_ms(answer_started)
            draft = _parse_structured_result(
                answer_result,
                ModelAnswer,
                stage=stage,
                diagnostic_id=diagnostic_id,
            )
        except RepositoryIntelligenceError as error:
            _log_processing_error(request_logger, stage, error)
            raise
        except RepositoryError as error:
            _log_processing_error(request_logger, stage, error)
            raise
        except Exception as error:
            await self.close()
            _log_processing_error(request_logger, stage, error)
            raise RepositoryIntelligenceError(
                f"The Codex request failed. Diagnostic ID: {diagnostic_id}.",
                502,
            ) from error

        answer_text = draft.answer.strip()
        if not answer_text:
            raise RepositoryIntelligenceError(
                f"Codex returned an empty repository answer. Diagnostic ID: {diagnostic_id}.",
                502,
            )
        evidence = validate_evidence(draft.evidence, repository)
        usage = _combined_usage(selection_result, answer_result)
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
            client_reused=client_reused,
        )
        return result

    async def _codex_client(self) -> tuple[AsyncCodex, bool]:
        if self._client is not None:
            return self._client, True
        async with self._startup_lock:
            if self._client is not None:
                return self._client, True
            started = time.perf_counter()
            isolated_cwd = tempfile.TemporaryDirectory(prefix="sentia-codex-")
            client = AsyncCodex(CodexConfig(cwd=isolated_cwd.name))
            try:
                await client.__aenter__()
            except Exception:
                isolated_cwd.cleanup()
                raise
            self._isolated_cwd = isolated_cwd
            self._client = client
            logger.info("codex_client_started", elapsed_ms=_elapsed_ms(started))
            return client, False

    async def _run_structured(
        self,
        codex: AsyncCodex,
        *,
        instructions: str,
        prompt: str,
        response_model: type[BaseModel],
    ) -> TurnResult:
        thread = await codex.thread_start(
            approval_mode=ApprovalMode.deny_all,
            base_instructions=instructions,
            cwd=None,
            ephemeral=True,
            model=self._requested_model,
            sandbox=Sandbox.read_only,
        )
        return await thread.run(
            prompt,
            approval_mode=ApprovalMode.deny_all,
            model=self._requested_model,
            output_schema=_strict_output_schema(response_model),
            sandbox=Sandbox.read_only,
        )


def _parse_structured_result[ModelT: BaseModel](
    result: TurnResult,
    response_model: type[ModelT],
    *,
    stage: str,
    diagnostic_id: str,
) -> ModelT:
    if not result.final_response:
        raise RepositoryIntelligenceError(
            f"Codex returned an empty {stage.replace('_', ' ')}. Diagnostic ID: {diagnostic_id}.",
            502,
        )
    try:
        return response_model.model_validate_json(result.final_response)
    except ValidationError as error:
        raise RepositoryIntelligenceError(
            f"Sentia could not validate Codex's {stage.replace('_', ' ')}. "
            f"Diagnostic ID: {diagnostic_id}.",
            502,
        ) from error


def _combined_usage(*results: TurnResult) -> TokenUsage:
    input_tokens = 0
    output_tokens = 0
    for result in results:
        if result.usage is None:
            continue
        input_tokens += result.usage.last.input_tokens
        output_tokens += result.usage.last.output_tokens
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)


def _strict_output_schema(response_model: type[BaseModel]) -> JsonObject:
    schema = copy.deepcopy(response_model.model_json_schema(by_alias=True))

    def make_strict(value: object) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                value["additionalProperties"] = False
                value["required"] = list(properties)
            value.pop("default", None)
            for child in value.values():
                make_strict(child)
        elif isinstance(value, list):
            for child in value:
                make_strict(child)

    make_strict(schema)
    return cast(JsonObject, schema)


def _log_processing_error(
    request_logger: structlog.typing.FilteringBoundLogger,
    stage: str,
    error: Exception,
) -> None:
    request_logger.error(
        "codex_processing_failed",
        stage=stage,
        error_type=type(error).__name__,
        error_message=_safe_error_message(error),
        status_code=(error.status_code if isinstance(error, RepositoryIntelligenceError) else None),
    )


def _safe_error_message(error: Exception) -> str | None:
    message = " ".join(str(error).split())
    return message[:500] or None


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1_000, 2)
