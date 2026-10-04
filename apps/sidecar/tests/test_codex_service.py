from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sentia_sidecar.codex_service as service_module
from openai_codex import ApprovalMode, Sandbox
from sentia_sidecar.codex_service import CodexRepositoryService
from sentia_sidecar.repository import (
    MAX_CONTEXT_CHARS,
    RepositoryManifest,
    build_repository_manifest,
)
from sentia_sidecar.repository_intelligence import RepositoryIntelligenceError
from sentia_sidecar.structural_index import StructuralIndex
from structlog.testing import capture_logs


@pytest.mark.parametrize("codex_bin", [None, "/installed/codex"])
@pytest.mark.parametrize("explicit_bin", [None, "/extension/codex"])
async def test_codex_service_reuses_one_client_for_bounded_structured_turns(
    tmp_path: Path,
    monkeypatch: Any,
    codex_bin: str | None,
    explicit_bin: str | None,
) -> None:
    monkeypatch.delenv("SENTIA_CODEX_BIN", raising=False)
    if explicit_bin:
        monkeypatch.setenv("SENTIA_CODEX_BIN", explicit_bin)
    monkeypatch.setattr(service_module.shutil, "which", lambda _: codex_bin)
    (tmp_path / "README.md").write_text("# Fixture\nA small project.\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "def start_app() -> str:\n    return 'ready'\n",
        encoding="utf-8",
    )
    manifest = build_repository_manifest(str(tmp_path))
    selection = json.dumps(
        {
            "files": [
                {
                    "path": "main.py",
                    "readMode": "outline",
                    "reason": "This is the application entry point.",
                }
            ],
            "rationale": "Use implementation evidence.",
        }
    )
    answer = json.dumps(
        {
            "answer": "The application starts in main.py.",
            "spokenAnswer": "The application starts in the main module.",
            "evidence": [
                {
                    "path": "main.py",
                    "startLine": 1,
                    "endLine": 1,
                    "label": "Application entry point",
                }
            ],
            "recommendedMode": "brainstorm",
            "modeReason": "The user asked for an explanation.",
        }
    )
    speech = json.dumps({"spokenAnswer": "The application starts in the main module."})
    results = [
        _turn_result(selection, input_tokens=100, output_tokens=20),
        _turn_result(answer, input_tokens=200, output_tokens=40),
        _turn_result(speech, input_tokens=80, output_tokens=15),
        _turn_result(selection, input_tokens=100, output_tokens=20),
        _turn_result(answer, input_tokens=200, output_tokens=40),
    ]
    calls: list[dict[str, Any]] = []

    class FakeThread:
        async def run(self, prompt: str, **kwargs: Any) -> Any:
            calls.append({"kind": "run", "prompt": prompt, **kwargs})
            return results.pop(0)

    class FakeCodex:
        def __init__(self, config: Any) -> None:
            calls.append({"kind": "client", "config": config})

        async def __aenter__(self) -> FakeCodex:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def close(self) -> None:
            calls.append({"kind": "close"})

        async def thread_start(self, **kwargs: Any) -> FakeThread:
            calls.append({"kind": "thread", **kwargs})
            return FakeThread()

    monkeypatch.setattr(service_module, "AsyncCodex", FakeCodex)
    service = CodexRepositoryService("codex-test")

    with capture_logs() as logs:
        first = await service.answer("Where does this application start?", manifest)
        spoken = await service.render_speech(first.answer, tmp_path)
        second = await service.answer("Where does this application start?", manifest)
        await service.close()

    client_calls = [call for call in calls if call["kind"] == "client"]
    thread_calls = [call for call in calls if call["kind"] == "thread"]
    run_calls = [call for call in calls if call["kind"] == "run"]
    assert len(client_calls) == 1
    assert client_calls[0]["config"].codex_bin == (explicit_bin or codex_bin)
    assert len(thread_calls) == 5
    assert len(run_calls) == 5
    assert len([call for call in calls if call["kind"] == "close"]) == 1
    assert all(call["ephemeral"] is True for call in thread_calls)
    assert all(call["approval_mode"] == ApprovalMode.deny_all for call in thread_calls)
    assert all(call["sandbox"] == Sandbox.read_only for call in thread_calls)
    assert all(call["sandbox"] == Sandbox.read_only for call in run_calls)
    assert run_calls[0]["output_schema"]["$defs"]["FileChoice"]["additionalProperties"] is False
    assert set(run_calls[0]["output_schema"]["required"]) == {"files", "rationale"}
    assert run_calls[1]["output_schema"]["$defs"]["EvidenceRange"]["additionalProperties"] is False
    assert set(run_calls[1]["output_schema"]["$defs"]["EvidenceRange"]["required"]) == {
        "path",
        "startLine",
        "endLine",
        "label",
    }
    assert "def start_app" not in run_calls[0]["prompt"]
    assert "def start_app" in run_calls[1]["prompt"]
    assert "The application starts in main.py." in run_calls[2]["prompt"]
    assert first.model == "codex-test"
    assert first.spoken_answer == "The application starts in the main module."
    assert first.selected_files == ["main.py"]
    assert first.evidence[0].path == "main.py"
    assert spoken.spoken_answer == "The application starts in the main module."
    assert first.usage.input_tokens == 300
    assert first.usage.output_tokens == 60
    assert second.answer == first.answer
    validated = [item for item in logs if item["event"] == "repository_answer_validated"]
    assert validated[0]["client_reused"] is False
    assert validated[1]["client_reused"] is True
    assert validated[1]["selection_elapsed_ms"] >= 0
    assert validated[1]["answer_elapsed_ms"] >= 0


async def test_codex_read_code_request_keeps_implementation_after_markdown_first_selection(
    markdown_first_manifest: RepositoryManifest,
    monkeypatch: Any,
) -> None:
    manifest = markdown_first_manifest
    documents = ["README.md", "PRODUCT_VISION_V2.md", "SESSIONS.md"]
    code = [path for path in manifest.files if path.endswith(".py")]
    prompts: list[str] = []
    instructions: list[str] = []

    class FakeThread:
        async def run(self, prompt: str, **kwargs: Any) -> Any:
            prompts.append(prompt)
            if "FILE-SELECTION REPORT" not in prompt:
                result = {
                    "files": [
                        {
                            "path": path,
                            "readMode": "full" if path in documents else "outline",
                            "reason": "Selected evidence.",
                        }
                        for path in documents + code
                    ],
                    "rationale": "Read descriptions followed by implementation.",
                }
            else:
                for index in range(5):
                    assert f"return 'implemented feature {index}'" in prompt
                result = {
                    "answer": "These features have implementation evidence; the vision is a plan.",
                    "spokenAnswer": "These features have code evidence. The vision is a plan.",
                    "evidence": [
                        {"path": path, "startLine": 1, "endLine": 2, "label": "Implementation"}
                        for path in code
                    ],
                    "recommendedMode": "brainstorm",
                    "modeReason": "The user asked to understand existing code.",
                }
            return _turn_result(json.dumps(result), input_tokens=100, output_tokens=20)

    class FakeCodex:
        def __init__(self, config: Any) -> None:
            pass

        async def __aenter__(self) -> FakeCodex:
            return self

        async def close(self) -> None:
            pass

        async def thread_start(self, **kwargs: Any) -> FakeThread:
            instructions.append(kwargs["base_instructions"])
            return FakeThread()

    monkeypatch.setattr(service_module, "AsyncCodex", FakeCodex)
    service = CodexRepositoryService("codex-test")

    with capture_logs() as logs:
        answer = await service.answer(
            "Read the code and tell me what features are present.", manifest
        )
    await service.close()

    assert len(prompts) == 2
    assert "not a full repository audit" in prompts[1]
    assert "documentation alone" in instructions[1]
    assert "FILE-SELECTION REPORT is a plan" in instructions[1]
    assert answer.selected_files[:5] == code
    assert answer.files_read == 8
    assert {item.path for item in answer.evidence} == set(code)
    reader = next(item for item in logs if item["event"] == "content_reader_completed")
    assert reader["evidence_chars"] <= MAX_CONTEXT_CHARS
    assert reader["omitted_files"] == []
    assert set(reader["truncated_files"]) == set(documents + code)


async def test_codex_flow_coverage_reuses_root_thread_and_adds_valid_anchors(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    (tmp_path / "feature.py").write_text(
        "def entry():\n"
        "    worker()\n\n"
        "def worker():\n"
        "    outcome()\n\n"
        "def outcome():\n"
        "    return None\n",
        encoding="utf-8",
    )
    manifest = build_repository_manifest(str(tmp_path))
    index = StructuralIndex(
        manifest.root,
        manifest.repository_revision,
        manifest.entities,
        manifest.relationships,
    )
    entities = {
        entity.name: entity
        for entity in index.entities
        if entity.name in {"entry", "worker", "outcome"}
    }
    results = [
        _turn_result(
            json.dumps(
                {
                    "files": [
                        {
                            "path": "feature.py",
                            "readMode": "full",
                            "reason": "Contains the feature implementation.",
                        }
                    ],
                    "rationale": "Inspect the feature implementation.",
                }
            ),
            input_tokens=100,
            output_tokens=20,
        ),
        _turn_result(
            json.dumps(
                {
                    "rootEntityIds": [entities["worker"].entity_id],
                    "rationale": "The worker is the main operation.",
                    "unresolvedConcepts": ["entry and outcome"],
                }
            ),
            input_tokens=110,
            output_tokens=15,
        ),
        _turn_result(
            json.dumps(
                {
                    "coverageAssessment": {
                        "sufficient": False,
                        "reason": "The initial anchor misses entry and outcome landmarks.",
                    },
                    "additionalAnchorEntityIds": [
                        entities["entry"].entity_id,
                        entities["outcome"].entity_id,
                    ],
                    "unresolvedConcepts": [],
                    "rationale": "Add the feature entry and externally meaningful outcome.",
                }
            ),
            input_tokens=120,
            output_tokens=25,
        ),
    ]
    calls: list[dict[str, Any]] = []

    class FakeThread:
        async def run(self, prompt: str, **kwargs: Any) -> Any:
            calls.append({"kind": "run", "prompt": prompt, **kwargs})
            return results.pop(0)

    class FakeCodex:
        def __init__(self, config: Any) -> None:
            calls.append({"kind": "client", "config": config})

        async def __aenter__(self) -> FakeCodex:
            return self

        async def close(self) -> None:
            return None

        async def thread_start(self, **kwargs: Any) -> FakeThread:
            calls.append({"kind": "thread", **kwargs})
            return FakeThread()

    monkeypatch.setattr(service_module, "AsyncCodex", FakeCodex)
    service = CodexRepositoryService("codex-test")

    with capture_logs() as logs:
        result = await service.select_flow_roots("Show the feature flow", manifest, index)
    await service.close()

    thread_calls = [call for call in calls if call["kind"] == "thread"]
    run_calls = [call for call in calls if call["kind"] == "run"]
    assert len(thread_calls) == 2
    assert len(run_calls) == 3
    assert "Codex Flow Map investigator" in thread_calls[1]["base_instructions"]
    assert "INITIAL ANCHORS" in run_calls[2]["prompt"]
    assert "BOUNDED FEATURE-COVERAGE CANDIDATE CATALOG" in run_calls[2]["prompt"]
    assert result.investigation.initial_anchor_entity_ids == [entities["worker"].entity_id]
    assert result.investigation.coverage_anchor_entity_ids == [
        entities["entry"].entity_id,
        entities["outcome"].entity_id,
    ]
    assert result.selection.root_entity_ids == [
        entities["worker"].entity_id,
        entities["entry"].entity_id,
        entities["outcome"].entity_id,
    ]
    assert result.investigation_usage.file_selection.input_tokens == 100
    assert result.investigation_usage.initial_anchor_selection.input_tokens == 110
    assert result.investigation_usage.coverage_pass.input_tokens == 120
    assert result.usage.input_tokens == 330
    completed = [item for item in logs if item["event"] == "codex_flow_investigation_completed"]
    assert completed[0]["coverage_attempted"] is True
    assert completed[0]["coverage_elapsed_ms"] >= 0


async def test_codex_flow_coverage_malformed_output_falls_back_and_counts_usage(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    manifest, index, worker_id = _flow_manifest(tmp_path)
    results = [
        _flow_file_selection_result(),
        _flow_root_result(worker_id),
        _turn_result("not-json", input_tokens=70, output_tokens=5),
    ]

    class FakeThread:
        async def run(self, prompt: str, **kwargs: Any) -> Any:
            del prompt, kwargs
            return results.pop(0)

    class FakeCodex:
        def __init__(self, config: Any) -> None:
            del config

        async def __aenter__(self) -> FakeCodex:
            return self

        async def close(self) -> None:
            return None

        async def thread_start(self, **kwargs: Any) -> FakeThread:
            del kwargs
            return FakeThread()

    monkeypatch.setattr(service_module, "AsyncCodex", FakeCodex)
    service = CodexRepositoryService("codex-test")

    with capture_logs() as logs:
        result = await service.select_flow_roots("Show the feature flow", manifest, index)
    await service.close()

    assert result.selection.root_entity_ids == [worker_id]
    assert result.investigation.coverage_anchor_entity_ids == []
    assert result.investigation_usage.coverage_pass.input_tokens == 70
    assert result.usage.input_tokens == 280
    assert any(item["event"] == "codex_flow_feature_coverage_fallback" for item in logs)


async def test_codex_flow_coverage_timeout_falls_back_to_initial_anchors(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    manifest, index, worker_id = _flow_manifest(tmp_path)
    results = [_flow_file_selection_result(), _flow_root_result(worker_id)]

    class FakeThread:
        async def run(self, prompt: str, **kwargs: Any) -> Any:
            del kwargs
            if "ORIGINAL FEATURE REQUEST" in prompt:
                await asyncio.sleep(1)
                raise AssertionError("coverage timeout did not cancel the turn")
            return results.pop(0)

    class FakeCodex:
        def __init__(self, config: Any) -> None:
            del config

        async def __aenter__(self) -> FakeCodex:
            return self

        async def close(self) -> None:
            return None

        async def thread_start(self, **kwargs: Any) -> FakeThread:
            del kwargs
            return FakeThread()

    monkeypatch.setattr(service_module, "AsyncCodex", FakeCodex)
    monkeypatch.setattr(service_module, "FLOW_COVERAGE_TIMEOUT_SECONDS", 0.001)
    service = CodexRepositoryService("codex-test")

    result = await service.select_flow_roots("Show the feature flow", manifest, index)
    await service.close()

    assert result.selection.root_entity_ids == [worker_id]
    assert result.investigation_usage.coverage_pass.input_tokens == 0


async def test_codex_flow_failure_logs_underlying_error_with_diagnostic_id(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    manifest, index, _ = _flow_manifest(tmp_path)
    service = CodexRepositoryService("codex-test")

    async def fail_client() -> Any:
        raise RuntimeError("provider connection failed")

    monkeypatch.setattr(service, "_codex_client", fail_client)
    with capture_logs() as logs, pytest.raises(RepositoryIntelligenceError) as caught:
        await service.select_flow_roots("Show the feature flow", manifest, index)

    failures = [item for item in logs if item["event"] == "codex_processing_failed"]
    assert len(failures) == 1
    failure = failures[0]
    assert failure["diagnostic_id"] in str(caught.value)
    assert failure["stage"] == "flow_file_selection"
    assert failure["error_type"] == "RuntimeError"
    assert failure["error_message"] == "provider connection failed"
    assert caught.value.status_code == 502


def _flow_manifest(tmp_path: Path) -> tuple[Any, StructuralIndex, str]:
    (tmp_path / "feature.py").write_text(
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
    worker_id = next(entity.entity_id for entity in index.entities if entity.name == "worker")
    return manifest, index, worker_id


def _flow_file_selection_result() -> Any:
    return _turn_result(
        json.dumps(
            {
                "files": [
                    {
                        "path": "feature.py",
                        "readMode": "full",
                        "reason": "Contains the feature implementation.",
                    }
                ],
                "rationale": "Inspect the feature implementation.",
            }
        ),
        input_tokens=100,
        output_tokens=20,
    )


def _flow_root_result(worker_id: str) -> Any:
    return _turn_result(
        json.dumps(
            {
                "rootEntityIds": [worker_id],
                "rationale": "The worker is the main operation.",
                "unresolvedConcepts": [],
            }
        ),
        input_tokens=110,
        output_tokens=15,
    )


async def test_codex_speech_stream_yields_agent_message_deltas(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeTurn:
        async def stream(self) -> Any:
            yield SimpleNamespace(
                method="item/agentMessage/delta",
                payload=SimpleNamespace(delta="First idea. "),
            )
            yield SimpleNamespace(
                method="item/agentMessage/delta",
                payload=SimpleNamespace(delta="Second idea."),
            )
            yield SimpleNamespace(
                method="turn/completed",
                payload=SimpleNamespace(
                    turn=SimpleNamespace(status=SimpleNamespace(value="completed"))
                ),
            )

        async def interrupt(self) -> None:
            calls.append({"kind": "interrupt"})

    class FakeThread:
        async def turn(self, prompt: str, **kwargs: Any) -> FakeTurn:
            calls.append({"kind": "turn", "prompt": prompt, **kwargs})
            return FakeTurn()

    class FakeCodex:
        def __init__(self, config: Any) -> None:
            calls.append({"kind": "client", "config": config})

        async def __aenter__(self) -> FakeCodex:
            return self

        async def close(self) -> None:
            return None

        async def thread_start(self, **kwargs: Any) -> FakeThread:
            calls.append({"kind": "thread", **kwargs})
            return FakeThread()

    monkeypatch.setattr(service_module, "AsyncCodex", FakeCodex)
    service = CodexRepositoryService("codex-test")

    deltas = [delta async for delta in service.stream_speech("Display answer.", tmp_path)]
    await service.close()

    assert deltas == ["First idea. ", "Second idea."]
    turn_call = next(call for call in calls if call["kind"] == "turn")
    assert "Return only the spoken narration as plain text" in turn_call["prompt"]
    assert "output_schema" not in turn_call


def _turn_result(text: str, *, input_tokens: int, output_tokens: int) -> Any:
    usage = SimpleNamespace(
        last=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    )
    return SimpleNamespace(final_response=text, usage=usage)
