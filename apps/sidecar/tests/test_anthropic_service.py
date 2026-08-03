from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, StreamEvent
from sentia_sidecar.anthropic_service import (
    READ_TOOL,
    SEARCH_TOOL,
    AnthropicRepositoryService,
    AnthropicServiceError,
)
from sentia_sidecar.repository import RepositoryManifest, build_repository_manifest
from sentia_sidecar.repository_agent_tools import RepositoryAgentTools
from structlog.testing import capture_logs

MODEL = "claude-haiku-4-5-20251001"


def _result(
    structured_output: object,
    *,
    input_tokens: int = 300,
    output_tokens: int = 60,
    subtype: str = "success",
    is_error: bool = False,
    errors: list[str] | None = None,
    api_error_status: int | None = None,
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=120,
        duration_api_ms=90,
        is_error=is_error,
        num_turns=3,
        session_id="session_test",
        total_cost_usd=0.002,
        usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
        result=None,
        structured_output=structured_output,
        errors=errors,
        api_error_status=api_error_status,
    )


async def test_answer_uses_read_only_agent_sdk_and_cached_snapshot(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# Fixture\nA deliberately recognizable project description.\n",
        encoding="utf-8",
    )
    (tmp_path / "service.py").write_text(
        "def run_fixture() -> str:\n    return 'implementation evidence'\n",
        encoding="utf-8",
    )
    manifest = build_repository_manifest(str(tmp_path))
    created_tools: list[RepositoryAgentTools] = []
    calls: list[tuple[str, ClaudeAgentOptions]] = []

    def tools_factory(value: RepositoryManifest) -> RepositoryAgentTools:
        tools = RepositoryAgentTools(value)
        created_tools.append(tools)
        return tools

    async def fake_query(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[object]:
        calls.append((prompt, options))
        if "DISPLAY ANSWER TO RENDER" in prompt:
            yield _result(
                {
                    "spokenAnswer": (
                        "This is a fixture repository backed by an implementation service."
                    )
                },
                input_tokens=80,
                output_tokens=20,
            )
            return
        await created_tools[-1].search("fixture service", 10)
        await created_tools[-1].read("README.md", "full")
        await created_tools[-1].read("service.py", "full")
        yield _result(
            {
                "answer": "This is a fixture repository backed by an implementation service.",
                "evidence": [
                    {
                        "path": "README.md",
                        "startLine": 1,
                        "endLine": 2,
                        "label": "Project README",
                    },
                    {
                        "path": "service.py",
                        "startLine": 1,
                        "endLine": 2,
                        "label": "Fixture service",
                    },
                ],
                "recommendedMode": "brainstorm",
                "modeReason": "x" * 300,
            }
        )

    service = AnthropicRepositoryService(
        MODEL,
        query_function=fake_query,
        tools_factory=tools_factory,
    )
    await service.connect("sk-ant-test", validate=False)

    with capture_logs() as logs:
        answer = await service.answer("What is this?", manifest)
        speech = await service.render_speech(answer.answer, tmp_path)

    assert len(calls) == 2
    prompt, options = calls[0]
    assert "CACHED SNAPSHOT SUMMARY" in prompt
    assert "recognizable project description" not in prompt
    assert options.allowed_tools == [SEARCH_TOOL, READ_TOOL]
    assert options.tools == []
    assert options.permission_mode == "dontAsk"
    assert options.setting_sources == []
    assert options.strict_mcp_config is True
    assert options.output_format is not None
    assert (
        options.output_format["schema"]["$defs"]["EvidenceRange"]["additionalProperties"] is False
    )
    assert "Read" in options.disallowed_tools
    assert "Bash" in options.disallowed_tools
    assert "WebSearch" in options.disallowed_tools
    assert answer.selected_files == ["README.md", "service.py"]
    assert answer.files_read == 2
    speech_prompt, speech_options = calls[1]
    assert answer.answer in speech_prompt
    assert speech_options.allowed_tools == []
    assert speech.spoken_answer == answer.answer
    assert answer.usage.input_tokens == 300
    assert answer.usage.output_tokens == 60
    assert answer.mode_reason == "x" * 240
    assert len(answer.evidence) == 2
    validated = next(item for item in logs if item["event"] == "repository_answer_validated")
    assert validated["runtime"] == "claude_agent_sdk"
    assert validated["session_id"] == "session_test"
    assert validated["turns"] == 3
    assert validated["agent_elapsed_ms"] >= 0
    assert validated["total_elapsed_ms"] >= 0


async def test_anthropic_speech_stream_yields_partial_text(tmp_path: Path) -> None:
    calls: list[ClaudeAgentOptions] = []

    async def fake_query(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[object]:
        assert "Return only the spoken narration as plain text" in prompt
        calls.append(options)
        yield StreamEvent(
            uuid="event-1",
            session_id="session-1",
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "First idea. "},
            },
        )
        yield StreamEvent(
            uuid="event-2",
            session_id="session-1",
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Second idea."},
            },
        )
        yield _result(None)

    service = AnthropicRepositoryService(MODEL, query_function=fake_query)
    await service.connect("sk-ant-test", validate=False)

    deltas = [delta async for delta in service.stream_speech("Display answer.", tmp_path)]

    assert deltas == ["First idea. ", "Second idea."]
    assert calls[0].include_partial_messages is True
    assert calls[0].output_format is None


async def test_invalid_agent_structured_output_has_correlated_diagnostics(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("# Fixture\n", encoding="utf-8")
    manifest = build_repository_manifest(str(tmp_path))
    created_tools: list[RepositoryAgentTools] = []

    def tools_factory(value: RepositoryManifest) -> RepositoryAgentTools:
        tools = RepositoryAgentTools(value)
        created_tools.append(tools)
        return tools

    async def fake_query(**_: Any) -> AsyncIterator[object]:
        await created_tools[-1].read("README.md", "full")
        yield _result(
            {
                "answer": "Fixture",
                "evidence": [],
                "recommendedMode": "unsupported",
                "modeReason": "Invalid mode for the test.",
            }
        )

    service = AnthropicRepositoryService(
        MODEL,
        query_function=fake_query,
        tools_factory=tools_factory,
    )
    await service.connect("sk-ant-test", validate=False)

    with capture_logs() as logs, pytest.raises(AnthropicServiceError) as raised:
        await service.answer("What is this?", manifest)

    failure = next(item for item in logs if item["event"] == "anthropic_processing_failed")
    assert failure["stage"] == "structured_output"
    assert failure["validation_errors"][0]["location"] == "recommendedMode"
    assert failure["diagnostic_id"] in str(raised.value)


async def test_agent_error_result_is_reported_even_when_query_raises_afterward(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("# Fixture\n", encoding="utf-8")
    manifest = build_repository_manifest(str(tmp_path))

    async def fake_query(**_: Any) -> AsyncIterator[object]:
        yield _result(
            None,
            subtype="error_max_turns",
            is_error=True,
            errors=["Turn limit reached."],
        )
        raise RuntimeError("SDK closes a failed one-shot query after yielding its result")

    service = AnthropicRepositoryService(MODEL, query_function=fake_query)
    await service.connect("sk-ant-test", validate=False)

    with pytest.raises(AnthropicServiceError) as raised:
        await service.answer("What is this?", manifest)

    assert raised.value.status_code == 422
    assert "Claude Agent SDK could not complete the repository scan" in str(raised.value)
    assert "Turn limit reached" in str(raised.value)


async def test_agent_must_read_repository_evidence_before_answering(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Fixture\n", encoding="utf-8")
    manifest = build_repository_manifest(str(tmp_path))

    async def fake_query(**_: Any) -> AsyncIterator[object]:
        yield _result(
            {
                "answer": "An unsupported answer.",
                "evidence": [],
                "recommendedMode": "brainstorm",
                "modeReason": "The user asked a question.",
            }
        )

    service = AnthropicRepositoryService(MODEL, query_function=fake_query)
    await service.connect("sk-ant-test", validate=False)

    with pytest.raises(AnthropicServiceError) as raised:
        await service.answer("What is this?", manifest)

    assert "structured output" in str(raised.value)


@respx.mock
async def test_connect_validation_reports_anthropic_api_error() -> None:
    route = respx.get("https://api.anthropic.com/v1/models").mock(
        return_value=httpx.Response(
            400,
            headers={"request-id": "req_test_123"},
            json={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "Invalid request",
                },
            },
        )
    )
    service = AnthropicRepositoryService(MODEL)

    with pytest.raises(AnthropicServiceError) as raised:
        await service.connect("sk-ant-test", validate=True)

    assert route.called
    assert raised.value.status_code == 400
    assert "rejected Sentia's request (400)" in str(raised.value)
    assert "req_test_123" in str(raised.value)
