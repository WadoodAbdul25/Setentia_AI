from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)
from claude_agent_sdk.types import StreamEvent
from sentia_sidecar.agent_runtime import (
    AgentAccess,
    AgentEventKind,
    AgentRouter,
    AgentRunRequest,
    ClaudeAgentAdapter,
    CodexAgentAdapter,
    _claude_options,
    _codex_event,
    _codex_permissions,
)
from sentia_sidecar.protocol import AgentProvider


def test_agent_request_requires_a_real_workspace(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Workspace does not exist"):
        AgentRunRequest(prompt="Explain this repository.", workspace_path=tmp_path / "missing")


def test_provider_permissions_are_read_only_by_default(tmp_path: Path) -> None:
    request = AgentRunRequest(prompt="Explain this repository.", workspace_path=tmp_path)

    options = _claude_options(request, "secret", "claude-test")
    sandbox, approval = _codex_permissions(request.access)

    assert options.allowed_tools == ["Read", "Glob", "Grep"]
    assert options.disallowed_tools == ["Edit", "Write", "Bash", "NotebookEdit"]
    assert options.permission_mode == "plan"
    assert options.setting_sources == ["project"]
    assert options.system_prompt == {"type": "preset", "preset": "claude_code"}
    assert options.include_partial_messages is True
    assert options.env == {"ANTHROPIC_API_KEY": "secret"}  # pragma: allowlist secret
    assert sandbox.value == "read-only"
    assert approval.value == "deny_all"


def test_provider_permissions_enable_bounded_workspace_writes(tmp_path: Path) -> None:
    request = AgentRunRequest(
        prompt="Implement the approved plan.",
        workspace_path=tmp_path,
        access=AgentAccess.WORKSPACE_WRITE,
    )

    options = _claude_options(request, None, "claude-test")
    sandbox, approval = _codex_permissions(request.access)

    assert options.permission_mode == "acceptEdits"
    assert "Edit" in options.allowed_tools
    assert "Bash" not in options.allowed_tools
    assert options.env == {}
    assert sandbox.value == "workspace-write"
    assert approval.value == "auto_review"


async def test_claude_adapter_normalizes_messages_without_thinking(tmp_path: Path) -> None:
    async def fake_query(**_: Any) -> AsyncIterator[Any]:
        yield SystemMessage(subtype="init", data={"session_id": "session-1"})
        yield AssistantMessage(
            content=[
                ThinkingBlock(thinking="hidden reasoning", signature="sig"),
                TextBlock(text="I am reading the entry point."),
                ToolUseBlock(id="tool-1", name="Read", input={"file_path": "src/main.py"}),
            ],
            model="claude-test",
            session_id="session-1",
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=8,
            is_error=False,
            num_turns=1,
            session_id="session-1",
            result="Done.",
            usage={"input_tokens": 20, "output_tokens": 5},
        )

    adapter = ClaudeAgentAdapter(
        api_key="test-key",  # pragma: allowlist secret
        default_model="claude-test",
        query_function=fake_query,
    )
    request = AgentRunRequest(prompt="Explain this repository.", workspace_path=tmp_path)

    events = [event async for event in adapter.run(request)]

    assert [event.kind for event in events] == [
        AgentEventKind.RUN_STARTED,
        AgentEventKind.SESSION_STARTED,
        AgentEventKind.ASSISTANT_TEXT,
        AgentEventKind.TOOL_STARTED,
        AgentEventKind.RUN_COMPLETED,
    ]
    assert all(event.message != "hidden reasoning" for event in events)
    assert events[3].tool_name == "Read"
    assert events[3].metadata == {"path": "src/main.py"}


async def test_claude_adapter_streams_partial_text_without_repeating_complete_text(
    tmp_path: Path,
) -> None:
    async def fake_query(**_: Any) -> AsyncIterator[Any]:
        yield StreamEvent(
            uuid="event-1",
            session_id="session-1",
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Hello"},
            },
        )
        yield AssistantMessage(
            content=[TextBlock(text="Hello")],
            model="claude-test",
            session_id="session-1",
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=8,
            is_error=False,
            num_turns=1,
            session_id="session-1",
            result="Hello",
        )

    adapter = ClaudeAgentAdapter(
        api_key="test-key", default_model="claude-test", query_function=fake_query
    )
    events = [
        event
        async for event in adapter.run(
            AgentRunRequest(prompt="Say hello.", workspace_path=tmp_path)
        )
    ]

    text_events = [event for event in events if event.kind == AgentEventKind.ASSISTANT_TEXT]
    assert [event.message for event in text_events] == ["Hello"]
    assert text_events[0].metadata == {"partial": True}


async def test_claude_adapter_requires_an_api_key(tmp_path: Path) -> None:
    adapter = ClaudeAgentAdapter(default_model="claude-test")

    events = [
        event
        async for event in adapter.run(
            AgentRunRequest(prompt="Explain this repository.", workspace_path=tmp_path)
        )
    ]

    assert [event.kind for event in events] == [
        AgentEventKind.RUN_STARTED,
        AgentEventKind.RUN_FAILED,
    ]
    assert events[-1].message == "Add an Anthropic API key before starting a Claude agent."


async def test_claude_adapter_does_not_duplicate_an_error_result(tmp_path: Path) -> None:
    async def fake_query(**_: Any) -> AsyncIterator[Any]:
        yield ResultMessage(
            subtype="error_max_turns",
            duration_ms=10,
            duration_api_ms=8,
            is_error=True,
            num_turns=2,
            session_id="session-1",
            result="Turn limit reached.",
        )
        raise RuntimeError("SDK raises after yielding an error result")

    adapter = ClaudeAgentAdapter(
        api_key="test-key", default_model="claude-test", query_function=fake_query
    )
    events = [
        event
        async for event in adapter.run(
            AgentRunRequest(prompt="Inspect this repository.", workspace_path=tmp_path)
        )
    ]

    failures = [event for event in events if event.kind == AgentEventKind.RUN_FAILED]
    assert len(failures) == 1
    assert failures[0].message == "Turn limit reached."


def test_codex_notifications_map_to_the_shared_event_contract() -> None:
    class Payload:
        def model_dump(self, **_: Any) -> dict[str, Any]:
            return {"delta": "Checking the repository."}

    class Notification:
        method = "item/agentMessage/delta"
        payload = Payload()

    event = _codex_event("run-1", "thread-1", Notification())

    assert event is not None
    assert event.provider == AgentProvider.CODEX
    assert event.kind == AgentEventKind.ASSISTANT_TEXT
    assert event.message == "Checking the repository."


def test_router_rejects_an_unconfigured_provider() -> None:
    router = AgentRouter([])

    with pytest.raises(ValueError, match="not configured"):
        router.adapter(AgentProvider.CODEX)


async def test_router_passes_the_extension_key_to_the_claude_adapter() -> None:
    claude = ClaudeAgentAdapter(default_model="claude-test")
    router = AgentRouter([claude])

    router.set_anthropic_api_key("  secret-from-vscode  ")

    availability = await claude.availability()
    assert availability.authentication == "api_key"


async def test_codex_rejects_limits_the_sdk_cannot_enforce(tmp_path: Path) -> None:
    request = AgentRunRequest(
        prompt="Explain this repository.",
        workspace_path=tmp_path,
        max_turns=4,
    )

    events = [event async for event in CodexAgentAdapter().run(request)]

    assert [event.kind for event in events] == [
        AgentEventKind.RUN_STARTED,
        AgentEventKind.RUN_FAILED,
    ]
    assert events[-1].message is not None
    assert "does not currently expose" in events[-1].message
