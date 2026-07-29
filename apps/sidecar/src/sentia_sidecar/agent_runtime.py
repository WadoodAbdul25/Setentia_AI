from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import structlog
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    query,
)
from claude_agent_sdk.types import StreamEvent
from openai_codex import (
    ApprovalMode,
    AsyncChatgptLoginHandle,
    AsyncCodex,
    CodexConfig,
    Sandbox,
)

from sentia_sidecar.protocol import AgentProvider

logger = structlog.get_logger(__name__)


class AgentAccess(StrEnum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"


class AgentEventKind(StrEnum):
    RUN_STARTED = "run_started"
    SESSION_STARTED = "session_started"
    ASSISTANT_TEXT = "assistant_text"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    USAGE = "usage"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"


@dataclass(frozen=True, slots=True)
class AgentRunRequest:
    prompt: str
    workspace_path: Path
    access: AgentAccess = AgentAccess.READ_ONLY
    model: str | None = None
    max_turns: int | None = None
    max_budget_usd: float | None = None

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("An agent prompt is required.")
        if not self.workspace_path.is_dir():
            raise ValueError(f"Workspace does not exist: {self.workspace_path}")
        if self.max_turns is not None and self.max_turns < 1:
            raise ValueError("max_turns must be at least one.")
        if self.max_budget_usd is not None and self.max_budget_usd <= 0:
            raise ValueError("max_budget_usd must be positive when provided.")


@dataclass(frozen=True, slots=True)
class AgentEvent:
    provider: AgentProvider
    run_id: str
    kind: AgentEventKind
    message: str | None = None
    session_id: str | None = None
    tool_name: str | None = None
    usage: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentAvailability:
    provider: AgentProvider
    installed: bool
    authentication: str
    detail: str


class CodingAgentAdapter(Protocol):
    provider: AgentProvider

    async def availability(self) -> AgentAvailability: ...

    def run(self, request: AgentRunRequest) -> AsyncIterator[AgentEvent]: ...


class CodexLogin(Protocol):
    async def start(self) -> str: ...

    async def close(self) -> None: ...


class AgentRouter:
    def __init__(self, adapters: list[CodingAgentAdapter]) -> None:
        self._adapters = {adapter.provider: adapter for adapter in adapters}

    def adapter(self, provider: AgentProvider) -> CodingAgentAdapter:
        try:
            return self._adapters[provider]
        except KeyError as error:
            raise ValueError(f"Agent provider is not configured: {provider}") from error

    async def availability(self) -> list[AgentAvailability]:
        return [await adapter.availability() for adapter in self._adapters.values()]

    def run(self, provider: AgentProvider, request: AgentRunRequest) -> AsyncIterator[AgentEvent]:
        return self.adapter(provider).run(request)

    def set_anthropic_api_key(self, api_key: str | None) -> None:
        adapter = self._adapters.get(AgentProvider.CLAUDE)
        if isinstance(adapter, ClaudeAgentAdapter):
            adapter.set_api_key(api_key)


class CodexLoginManager:
    def __init__(self) -> None:
        self._codex: AsyncCodex | None = None
        self._handle: AsyncChatgptLoginHandle | None = None
        self._wait_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> str:
        async with self._lock:
            await self._close_unlocked()
            codex = AsyncCodex()
            await codex.__aenter__()
            try:
                handle = await codex.login_chatgpt()
            except Exception:
                await codex.close()
                raise
            self._codex = codex
            self._handle = handle
            self._wait_task = asyncio.create_task(self._wait_for_completion(codex, handle))
            return handle.auth_url

    async def close(self) -> None:
        async with self._lock:
            await self._close_unlocked()

    async def _wait_for_completion(
        self, codex: AsyncCodex, handle: AsyncChatgptLoginHandle
    ) -> None:
        try:
            await handle.wait()
        except Exception as error:
            logger.warning("codex_login_ended", error_type=type(error).__name__)
        finally:
            await codex.close()
            async with self._lock:
                if self._codex is codex:
                    self._codex = None
                    self._handle = None
                    self._wait_task = None

    async def _close_unlocked(self) -> None:
        task = self._wait_task
        handle = self._handle
        codex = self._codex
        self._wait_task = None
        self._handle = None
        self._codex = None
        if handle is not None:
            with suppress(Exception):
                await handle.cancel()
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        if codex is not None:
            await codex.close()


class ClaudeAgentAdapter:
    provider = AgentProvider.CLAUDE

    def __init__(
        self,
        *,
        api_key: str | None = None,
        default_model: str = "claude-haiku-4-5-20251001",
        query_function: Callable[..., AsyncIterator[Any]] = query,
    ) -> None:
        self._api_key = api_key.strip() if api_key else None
        self._default_model = default_model
        self._query = query_function

    def set_api_key(self, api_key: str | None) -> None:
        self._api_key = api_key.strip() if api_key else None

    async def availability(self) -> AgentAvailability:
        api_key_available = bool(self._api_key or os.environ.get("ANTHROPIC_API_KEY"))
        return AgentAvailability(
            provider=self.provider,
            installed=True,
            authentication="api_key" if api_key_available else "missing",
            detail=(
                "Anthropic API key is available."
                if api_key_available
                else "Add an Anthropic API key to connect Claude Agent SDK."
            ),
        )

    def run(self, request: AgentRunRequest) -> AsyncIterator[AgentEvent]:
        return self._run(request)

    async def _run(self, request: AgentRunRequest) -> AsyncIterator[AgentEvent]:
        run_id = f"run_{uuid4().hex}"
        if not (self._api_key or os.environ.get("ANTHROPIC_API_KEY")):
            yield AgentEvent(self.provider, run_id, AgentEventKind.RUN_STARTED)
            yield AgentEvent(
                self.provider,
                run_id,
                AgentEventKind.RUN_FAILED,
                message="Add an Anthropic API key before starting a Claude agent.",
            )
            return
        options = _claude_options(request, self._api_key, self._default_model)
        result_received = False
        partial_text_received = False
        yield AgentEvent(self.provider, run_id, AgentEventKind.RUN_STARTED)
        logger.info(
            "agent_run_started",
            provider=self.provider,
            run_id=run_id,
            access=request.access,
            workspace=str(request.workspace_path.resolve()),
            max_turns=request.max_turns,
        )

        try:
            async for message in self._query(prompt=request.prompt, options=options):
                if isinstance(message, StreamEvent):
                    text_delta = _claude_text_delta(message)
                    if text_delta:
                        partial_text_received = True
                        yield AgentEvent(
                            self.provider,
                            run_id,
                            AgentEventKind.ASSISTANT_TEXT,
                            message=text_delta,
                            session_id=message.session_id,
                            metadata={"partial": True},
                        )
                    continue

                if isinstance(message, SystemMessage) and message.subtype == "init":
                    session_id = _optional_string(message.data.get("session_id"))
                    yield AgentEvent(
                        self.provider,
                        run_id,
                        AgentEventKind.SESSION_STARTED,
                        session_id=session_id,
                    )
                    continue

                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if (
                            isinstance(block, TextBlock)
                            and block.text
                            and not partial_text_received
                        ):
                            yield AgentEvent(
                                self.provider,
                                run_id,
                                AgentEventKind.ASSISTANT_TEXT,
                                message=block.text,
                                session_id=message.session_id,
                            )
                        elif isinstance(block, ToolUseBlock):
                            yield AgentEvent(
                                self.provider,
                                run_id,
                                AgentEventKind.TOOL_STARTED,
                                session_id=message.session_id,
                                tool_name=block.name,
                                metadata=_safe_tool_metadata(block.input),
                            )
                        elif isinstance(block, ToolResultBlock):
                            yield AgentEvent(
                                self.provider,
                                run_id,
                                AgentEventKind.TOOL_COMPLETED,
                                session_id=message.session_id,
                                metadata={
                                    "toolUseId": block.tool_use_id,
                                    "isError": bool(block.is_error),
                                },
                            )
                    partial_text_received = False
                    continue

                if isinstance(message, ResultMessage):
                    result_received = True
                    kind = (
                        AgentEventKind.RUN_FAILED
                        if message.is_error
                        else AgentEventKind.RUN_COMPLETED
                    )
                    yield AgentEvent(
                        self.provider,
                        run_id,
                        kind,
                        message=message.result or _first_error(message.errors),
                        session_id=message.session_id,
                        usage=message.usage,
                        metadata={
                            "subtype": message.subtype,
                            "turns": message.num_turns,
                            "costUsd": message.total_cost_usd,
                        },
                    )

            if not result_received:
                yield AgentEvent(
                    self.provider,
                    run_id,
                    AgentEventKind.RUN_FAILED,
                    message="Claude ended without a result message.",
                )
        except Exception as error:
            if result_received:
                logger.debug(
                    "agent_query_closed_after_result",
                    provider=self.provider,
                    run_id=run_id,
                    error_type=type(error).__name__,
                )
                return
            logger.exception(
                "agent_run_failed",
                provider=self.provider,
                run_id=run_id,
                error_type=type(error).__name__,
            )
            yield AgentEvent(
                self.provider,
                run_id,
                AgentEventKind.RUN_FAILED,
                message=_public_agent_error(error),
            )


class CodexAgentAdapter:
    provider = AgentProvider.CODEX

    def __init__(self, *, default_model: str | None = None) -> None:
        self._default_model = default_model

    async def availability(self) -> AgentAvailability:
        try:
            async with AsyncCodex() as codex:
                account = await codex.account(refresh_token=False)
        except Exception as error:
            return AgentAvailability(
                provider=self.provider,
                installed=True,
                authentication="missing_or_unavailable",
                detail=_public_agent_error(error),
            )
        account_data = account.model_dump(mode="json", by_alias=True)
        authenticated = account_data.get("account") is not None
        return AgentAvailability(
            provider=self.provider,
            installed=True,
            authentication="codex_login" if authenticated else "missing",
            detail=(
                "Codex login is available."
                if authenticated
                else "Run `codex login` before starting a Codex agent."
            ),
        )

    def run(self, request: AgentRunRequest) -> AsyncIterator[AgentEvent]:
        return self._run(request)

    async def _run(self, request: AgentRunRequest) -> AsyncIterator[AgentEvent]:
        run_id = f"run_{uuid4().hex}"
        workspace = str(request.workspace_path.resolve())
        sandbox, approval_mode = _codex_permissions(request.access)
        yield AgentEvent(self.provider, run_id, AgentEventKind.RUN_STARTED)
        if request.max_turns is not None or request.max_budget_usd is not None:
            yield AgentEvent(
                self.provider,
                run_id,
                AgentEventKind.RUN_FAILED,
                message=(
                    "The Codex Python SDK does not currently expose per-run turn or dollar "
                    "limits. Remove those limits and use Codex account spend controls."
                ),
            )
            return
        logger.info(
            "agent_run_started",
            provider=self.provider,
            run_id=run_id,
            access=request.access,
            workspace=workspace,
        )

        try:
            async with AsyncCodex(CodexConfig(cwd=workspace)) as codex:
                thread = await codex.thread_start(
                    approval_mode=approval_mode,
                    cwd=workspace,
                    model=request.model or self._default_model,
                    sandbox=sandbox,
                )
                yield AgentEvent(
                    self.provider,
                    run_id,
                    AgentEventKind.SESSION_STARTED,
                    session_id=thread.id,
                )
                turn = await thread.turn(
                    request.prompt,
                    approval_mode=approval_mode,
                    cwd=workspace,
                    model=request.model or self._default_model,
                    sandbox=sandbox,
                )
                async for notification in turn.stream():
                    event = _codex_event(run_id, thread.id, notification)
                    if event is not None:
                        yield event
        except Exception as error:
            logger.exception(
                "agent_run_failed",
                provider=self.provider,
                run_id=run_id,
                error_type=type(error).__name__,
            )
            yield AgentEvent(
                self.provider,
                run_id,
                AgentEventKind.RUN_FAILED,
                message=_public_agent_error(error),
            )


def create_agent_router(*, anthropic_api_key: str | None = None) -> AgentRouter:
    return AgentRouter(
        [
            ClaudeAgentAdapter(api_key=anthropic_api_key),
            CodexAgentAdapter(),
        ]
    )


def _claude_options(
    request: AgentRunRequest, api_key: str | None, default_model: str
) -> ClaudeAgentOptions:
    read_only = request.access == AgentAccess.READ_ONLY
    environment = {"ANTHROPIC_API_KEY": api_key} if api_key else {}
    return ClaudeAgentOptions(
        allowed_tools=(
            ["Read", "Glob", "Grep"]
            if read_only
            # A bare "Bash" entry auto-approves every shell command. Keep Bash
            # available to Claude, but require the permission broker to approve
            # non-read-only commands instead of granting blanket execution.
            else ["Read", "Glob", "Grep", "Edit", "Write"]
        ),
        disallowed_tools=(["Edit", "Write", "Bash", "NotebookEdit"] if read_only else []),
        permission_mode="plan" if read_only else "acceptEdits",
        cwd=request.workspace_path.resolve(),
        env=environment,
        max_turns=request.max_turns or 12,
        max_budget_usd=request.max_budget_usd,
        model=request.model or default_model,
        setting_sources=["project"],
        system_prompt={"type": "preset", "preset": "claude_code"},
        include_partial_messages=True,
    )


def _claude_text_delta(message: StreamEvent) -> str | None:
    event = message.event
    if event.get("type") != "content_block_delta":
        return None
    delta = event.get("delta")
    if not isinstance(delta, dict) or delta.get("type") != "text_delta":
        return None
    return _optional_string(delta.get("text"))


def _codex_permissions(access: AgentAccess) -> tuple[Sandbox, ApprovalMode]:
    if access == AgentAccess.READ_ONLY:
        return Sandbox.read_only, ApprovalMode.deny_all
    return Sandbox.workspace_write, ApprovalMode.auto_review


def _codex_event(run_id: str, session_id: str, notification: Any) -> AgentEvent | None:
    method = _optional_string(getattr(notification, "method", None))
    payload = getattr(notification, "payload", None)
    data = _model_dump(payload)

    if method == "item/agentMessage/delta":
        return AgentEvent(
            AgentProvider.CODEX,
            run_id,
            AgentEventKind.ASSISTANT_TEXT,
            message=_optional_string(data.get("delta")),
            session_id=session_id,
        )
    if method == "item/started":
        item = data.get("item")
        item_data = item if isinstance(item, dict) else {}
        return AgentEvent(
            AgentProvider.CODEX,
            run_id,
            AgentEventKind.TOOL_STARTED,
            session_id=session_id,
            tool_name=_codex_item_name(item_data),
            metadata=_safe_codex_item_metadata(item_data),
        )
    if method == "item/completed":
        item = data.get("item")
        item_data = item if isinstance(item, dict) else {}
        item_type = _optional_string(item_data.get("type"))
        if item_type in {"agentMessage", "agent_message"}:
            text = _optional_string(item_data.get("text"))
            if text:
                return AgentEvent(
                    AgentProvider.CODEX,
                    run_id,
                    AgentEventKind.ASSISTANT_TEXT,
                    message=text,
                    session_id=session_id,
                    metadata={"final": item_data.get("phase") == "final_answer"},
                )
        return AgentEvent(
            AgentProvider.CODEX,
            run_id,
            AgentEventKind.TOOL_COMPLETED,
            session_id=session_id,
            tool_name=_codex_item_name(item_data),
        )
    if method == "thread/tokenUsage/updated":
        usage = data.get("tokenUsage") or data.get("token_usage")
        return AgentEvent(
            AgentProvider.CODEX,
            run_id,
            AgentEventKind.USAGE,
            session_id=session_id,
            usage=usage if isinstance(usage, dict) else None,
        )
    if method == "turn/completed":
        turn = data.get("turn")
        turn_data = turn if isinstance(turn, dict) else {}
        status = _optional_string(turn_data.get("status"))
        error = turn_data.get("error")
        error_data = error if isinstance(error, dict) else {}
        failed = status == "failed"
        return AgentEvent(
            AgentProvider.CODEX,
            run_id,
            AgentEventKind.RUN_FAILED if failed else AgentEventKind.RUN_COMPLETED,
            message=_optional_string(error_data.get("message")) if failed else None,
            session_id=session_id,
            metadata={"status": status or "unknown"},
        )
    return None


def _model_dump(value: Any) -> dict[str, Any]:
    dump = getattr(value, "model_dump", None)
    if not callable(dump):
        return {}
    result = dump(mode="json", by_alias=True)
    return result if isinstance(result, dict) else {}


def _codex_item_name(item: Mapping[str, Any]) -> str:
    item_type = _optional_string(item.get("type")) or "activity"
    return {
        "commandExecution": "Bash",
        "command_execution": "Bash",
        "fileChange": "Edit",
        "file_change": "Edit",
        "mcpToolCall": "MCP",
        "mcp_tool_call": "MCP",
    }.get(item_type, item_type)


def _safe_codex_item_metadata(item: Mapping[str, Any]) -> dict[str, Any]:
    paths = item.get("changes")
    if isinstance(paths, list):
        safe_paths = [
            value.get("path")
            for value in paths
            if isinstance(value, dict) and isinstance(value.get("path"), str)
        ]
        return {"paths": safe_paths}
    return {}


def _safe_tool_metadata(tool_input: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("file_path", "path"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return {"path": value}
    return {}


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _first_error(errors: list[str] | None) -> str | None:
    return errors[0] if errors else None


def _public_agent_error(error: Exception) -> str:
    message = str(error).strip()
    if not message:
        return f"{type(error).__name__} while running the agent."
    return message[:1_000]
