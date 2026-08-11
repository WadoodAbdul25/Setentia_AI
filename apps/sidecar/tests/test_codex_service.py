from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import sentia_sidecar.codex_service as service_module
from openai_codex import ApprovalMode, Sandbox
from sentia_sidecar.codex_service import CodexRepositoryService
from sentia_sidecar.repository import build_repository_manifest
from structlog.testing import capture_logs


async def test_codex_service_reuses_one_client_for_bounded_structured_turns(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
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
