from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from sentia_sidecar.anthropic_service import AnthropicRepositoryService, AnthropicServiceError
from sentia_sidecar.repository import build_repository_manifest
from sentia_sidecar.structural_index import StructuralIndex
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
    num_turns: int = 1,
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=120,
        duration_api_ms=90,
        is_error=is_error,
        num_turns=num_turns,
        session_id="session_test",
        total_cost_usd=0.002,
        usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
        result=None,
        structured_output=structured_output,
        errors=errors,
        api_error_status=api_error_status,
    )


async def test_answer_uses_bounded_manifest_selection_and_content_read(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# Fixture\nA deliberately recognizable project description.\n",
        encoding="utf-8",
    )
    (tmp_path / "service.py").write_text(
        "def run_fixture() -> str:\n    return 'implementation evidence'\n",
        encoding="utf-8",
    )
    manifest = build_repository_manifest(str(tmp_path))
    calls: list[tuple[str, ClaudeAgentOptions]] = []

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
        if "FILE-SELECTION REPORT" not in prompt:
            yield _result(
                {
                    "files": [
                        {
                            "path": "README.md",
                            "readMode": "full",
                            "reason": "Project description.",
                        },
                        {
                            "path": "service.py",
                            "readMode": "full",
                            "reason": "Implementation evidence.",
                        },
                    ],
                    "rationale": "Read the description and implementation.",
                },
                input_tokens=100,
                output_tokens=20,
            )
            return
        yield _result(
            {
                "answer": "This is a fixture repository backed by an implementation service.",
                "spokenAnswer": "This fixture repository is backed by an implementation service.",
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

    service = AnthropicRepositoryService(MODEL, query_function=fake_query)
    await service.connect("sk-ant-test", validate=False)

    with capture_logs() as logs:
        answer = await service.answer("What is this?", manifest)
        speech = await service.render_speech(answer.answer, tmp_path)

    assert len(calls) == 3
    selection_prompt, selection_options = calls[0]
    assert "JSON-lines manifest" in selection_prompt
    assert "recognizable project description" not in selection_prompt
    assert selection_options.allowed_tools == []
    assert selection_options.tools == []
    assert selection_options.mcp_servers == {}
    assert selection_options.permission_mode == "dontAsk"
    assert selection_options.setting_sources == []
    assert selection_options.strict_mcp_config is True
    assert selection_options.max_turns == 2
    assert selection_options.output_format is not None
    assert (
        selection_options.output_format["schema"]["$defs"]["FileChoice"]["additionalProperties"]
        is False
    )
    assert "Read" in selection_options.disallowed_tools
    assert "Bash" in selection_options.disallowed_tools
    assert "WebSearch" in selection_options.disallowed_tools
    answer_prompt, answer_options = calls[1]
    assert "FILE-SELECTION REPORT" in answer_prompt
    assert "recognizable project description" in answer_prompt
    assert "implementation evidence" in answer_prompt
    assert answer_options.allowed_tools == []
    assert answer_options.mcp_servers == {}
    assert answer_options.output_format is not None
    assert (
        answer_options.output_format["schema"]["$defs"]["EvidenceRange"]["additionalProperties"]
        is False
    )
    assert answer.selected_files == ["README.md", "service.py"]
    assert answer.spoken_answer == (
        "This fixture repository is backed by an implementation service."
    )
    assert answer.files_read == 2
    speech_prompt, speech_options = calls[2]
    assert answer.answer in speech_prompt
    assert speech_options.allowed_tools == []
    assert speech.spoken_answer == answer.answer
    assert answer.usage.input_tokens == 400
    assert answer.usage.output_tokens == 80
    assert answer.mode_reason == "x" * 240
    assert len(answer.evidence) == 2
    validated = next(item for item in logs if item["event"] == "repository_answer_validated")
    assert validated["runtime"] == "claude_agent_sdk"
    assert validated["selection_session_id"] == "session_test"
    assert validated["answer_session_id"] == "session_test"
    assert validated["selection_turns"] == 1
    assert validated["answer_turns"] == 1
    assert validated["selection_elapsed_ms"] >= 0
    assert validated["content_read_elapsed_ms"] >= 0
    assert validated["answer_elapsed_ms"] >= 0
    assert validated["total_elapsed_ms"] >= 0


async def test_flow_root_selection_returns_auditable_investigation_without_ai_keywords(
    tmp_path: Path,
) -> None:
    (tmp_path / "service.py").write_text(
        "def entry():\n    worker()\n\ndef worker():\n    return None\n",
        encoding="utf-8",
    )
    manifest = build_repository_manifest(str(tmp_path))
    index = StructuralIndex(
        manifest.root,
        manifest.repository_revision,
        manifest.entities,
        manifest.relationships,
    )
    entry = next(entity for entity in index.entities if entity.name == "entry")
    calls: list[str] = []

    async def fake_query(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[object]:
        calls.append(prompt)
        if "ROOT CANDIDATE CATALOG" not in prompt:
            yield _result(
                {
                    "files": [
                        {
                            "path": "service.py",
                            "readMode": "full",
                            "reason": "Contains the feature entry point.",
                        }
                    ],
                    "rationale": "Read the feature implementation.",
                },
                input_tokens=90,
                output_tokens=15,
            )
            return
        assert options.output_format is not None
        allowed_ids = options.output_format["schema"]["properties"]["rootEntityIds"]["items"][
            "enum"
        ]
        assert entry.entity_id in allowed_ids
        assert set(allowed_ids) <= {entity.entity_id for entity in index.entities}
        assert "ent_invented" not in allowed_ids
        yield _result(
            {
                "rootEntityIds": [entry.entity_id],
                "rationale": "entry starts the requested behavior.",
                "unresolvedConcepts": [],
            },
            input_tokens=110,
            output_tokens=10,
        )

    service = AnthropicRepositoryService(MODEL, query_function=fake_query)
    await service.connect("sk-ant-test", validate=False)

    result = await service.select_flow_roots(
        "How does entry reach worker?",
        manifest,
        index,
    )

    assert len(calls) == 2
    assert entry.entity_id in calls[1]
    assert result.selection.root_entity_ids == [entry.entity_id]
    assert result.investigation.selected_files == ["service.py"]
    assert result.investigation.candidate_identifiers == ["entry"]
    assert result.investigation.search_queries == ["How does entry reach worker?"]
    assert result.investigation.read_spans
    assert result.usage.input_tokens == 200
    assert result.usage.output_tokens == 25
    assert result.investigation.initial_anchor_entity_ids == [entry.entity_id]
    assert result.investigation.coverage_anchor_entity_ids == []
    assert result.investigation_usage.file_selection.input_tokens == 90
    assert result.investigation_usage.initial_anchor_selection.input_tokens == 110
    assert result.investigation_usage.coverage_pass.input_tokens == 0


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
    calls = 0

    async def fake_query(**_: Any) -> AsyncIterator[object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            yield _result(
                {
                    "files": [
                        {
                            "path": "README.md",
                            "readMode": "full",
                            "reason": "Fixture documentation.",
                        }
                    ],
                    "rationale": "Read the fixture.",
                }
            )
            return
        yield _result(
            {
                "answer": "Fixture",
                "spokenAnswer": "This is a fixture.",
                "evidence": [],
                "recommendedMode": "unsupported",
                "modeReason": "Invalid mode for the test.",
            }
        )

    service = AnthropicRepositoryService(MODEL, query_function=fake_query)
    await service.connect("sk-ant-test", validate=False)

    with capture_logs() as logs, pytest.raises(AnthropicServiceError) as raised:
        await service.answer("What is this?", manifest)

    failure = next(item for item in logs if item["event"] == "anthropic_processing_failed")
    assert failure["stage"] == "repository_answer"
    assert failure["validation_errors"][0]["location"] == "recommendedMode"
    assert failure["diagnostic_id"] in str(raised.value)


async def test_feature_trace_planning_timeout_reports_stage(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text("def generate():\n    return 'report'\n")
    manifest = build_repository_manifest(str(tmp_path))
    index = StructuralIndex(
        manifest.root, manifest.repository_revision, manifest.entities, manifest.relationships
    )

    async def fake_query(**_: Any) -> AsyncIterator[object]:
        raise TimeoutError()
        yield  # pragma: no cover

    service = AnthropicRepositoryService(MODEL, query_function=fake_query)
    await service.connect("sk-ant-test", validate=False)
    with capture_logs() as logs, pytest.raises(AnthropicServiceError) as raised:
        await service.trace_feature("Generate a report", manifest, index)

    assert raised.value.status_code == 504
    assert "timed out waiting for Claude during feature trace planning" in str(raised.value)
    failure = next(item for item in logs if item["event"] == "anthropic_request_timeout")
    assert failure["stage"] == "feature_trace_planning"
    assert failure["diagnostic_id"] in str(raised.value)


async def test_trace_limits_and_usage_survive_failed_query(tmp_path: Path) -> None:
    closed = False

    async def fake_query(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[object]:
        nonlocal closed
        assert options.max_turns == 4
        assert options.effort == "low"
        assert options.max_budget_usd == 0.50
        assert options.env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "4096"
        try:
            yield AssistantMessage(
                content=[],
                model=MODEL,
                message_id="message-test",
                usage={
                    "input_tokens": 20,
                    "cache_read_input_tokens": 500,
                    "cache_creation_input_tokens": 100,
                    "output_tokens": 30,
                },
            )
            raise TimeoutError()
        finally:
            closed = True

    service = AnthropicRepositoryService(MODEL, query_function=fake_query)
    await service.connect("sk-ant-test", validate=False)
    with capture_logs() as logs, pytest.raises(TimeoutError):
        await service._run_structured_turn(
            prompt="private source",
            system_prompt="private instructions",
            response_schema={},
            workspace_path=tmp_path,
            diagnostic_id="test-timeout",
            stage="feature_trace_planning",
        )
    assert closed
    usage = next(item for item in logs if item["event"] == "feature_trace_message_usage")
    assert usage["cache_read_input_tokens"] == 500
    assert usage["cache_creation_input_tokens"] == 100
    assert usage["diagnostic_id"] == "test-timeout"
    assert "private source" not in str(logs)


async def test_trace_usage_snapshots_and_schema_rejections_are_not_duplicated(tmp_path: Path) -> None:
    async def fake_query(**_: Any) -> AsyncIterator[object]:
        for output_tokens in [1, 1, 30]:
            yield AssistantMessage(
                content=[ToolUseBlock(id="call-1", name="StructuredOutput", input={"private": "code"})],
                model=MODEL,
                message_id="message-1",
                usage={"input_tokens": 20, "output_tokens": output_tokens},
            )
        for _ in range(2):
            yield UserMessage(content=[ToolResultBlock(
                tool_use_id="call-1", is_error=True, content="private validation details"
            )])
        yield _result({}, input_tokens=50, output_tokens=40, num_turns=3)

    service = AnthropicRepositoryService(MODEL, query_function=fake_query)
    await service.connect("sk-ant-test", validate=False)
    with capture_logs() as logs:
        result = await service._run_structured_turn(
            prompt="private source",
            system_prompt="private instructions",
            response_schema={},
            workspace_path=tmp_path,
            diagnostic_id="test-usage",
            stage="feature_trace_review",
        )

    snapshots = [item for item in logs if item["event"] == "feature_trace_message_usage"]
    assert [item["output_tokens"] for item in snapshots] == [1, 30]
    assert all(item["usage_kind"] == "snapshot" for item in snapshots)
    rejections = [item for item in logs if item["event"] == "feature_trace_structured_output_rejected"]
    assert len(rejections) == 1
    finished = next(item for item in logs if item["event"] == "feature_trace_request_finished")
    assert finished["rejected_attempts"] == 1
    assert finished["output_tokens"] == 40
    assert result.usage == {"input_tokens": 50, "output_tokens": 40}
    assert "private" not in str(logs)


@pytest.mark.parametrize("stage", ["feature_trace_planning", "feature_trace_review"])
async def test_trace_rejects_success_without_structured_output(tmp_path: Path, stage: str) -> None:
    async def fake_query(**_: Any) -> AsyncIterator[object]:
        yield _result(None)

    service = AnthropicRepositoryService(MODEL, query_function=fake_query)
    await service.connect("sk-ant-test", validate=False)
    with pytest.raises(AnthropicServiceError, match="without structured output") as raised:
        await service._run_structured_turn(
            prompt="test",
            system_prompt="test",
            response_schema={},
            workspace_path=tmp_path,
            diagnostic_id="test-missing-output",
            stage=stage,
        )
    assert raised.value.status_code == 502
    assert stage.replace("_", " ") in str(raised.value)


def test_agent_usage_includes_cached_input() -> None:
    from sentia_sidecar.anthropic_service import _agent_usage

    usage = _agent_usage(
        {
            "input_tokens": 20,
            "cache_read_input_tokens": 500,
            "cache_creation_input_tokens": 100,
            "output_tokens": 30,
        }
    )
    assert usage.input_tokens == 620
    assert usage.output_tokens == 30


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
    assert "Claude Agent SDK could not complete repository file selection" in str(raised.value)
    assert "Turn limit reached" in str(raised.value)


async def test_unknown_selection_falls_back_to_eligible_repository_evidence(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("# Fixture\n", encoding="utf-8")
    manifest = build_repository_manifest(str(tmp_path))
    calls = 0

    async def fake_query(**_: Any) -> AsyncIterator[object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            yield _result(
                {
                    "files": [
                        {
                            "path": "missing.ts",
                            "readMode": "full",
                            "reason": "Incorrect model selection.",
                        }
                    ],
                    "rationale": "Use the requested file.",
                }
            )
            return
        yield _result(
            {
                "answer": "The fixture is documented in README.md.",
                "spokenAnswer": "The fixture is documented in its read me file.",
                "evidence": [
                    {
                        "path": "README.md",
                        "startLine": 1,
                        "endLine": 1,
                        "label": "Fixture documentation",
                    }
                ],
                "recommendedMode": "brainstorm",
                "modeReason": "The user asked a question.",
            }
        )

    service = AnthropicRepositoryService(MODEL, query_function=fake_query)
    await service.connect("sk-ant-test", validate=False)

    answer = await service.answer("What is this?", manifest)

    assert answer.selected_files == ["README.md"]
    assert answer.files_read == 1
    assert answer.evidence[0].path == "README.md"


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
