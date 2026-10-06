from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from claude_agent_sdk import ResultMessage, StreamEvent, SystemMessage
from sentia_sidecar.anthropic_service import AnthropicRepositoryService
from sentia_sidecar.codex_service import CodexRepositoryService
from sentia_sidecar.repository import build_repository_manifest

SELECTION = {
    "files": [{"path": "main.py", "readMode": "full", "reason": "Entry"}],
    "rationale": "Inspect the entry.",
}
ANSWER = {
    "answer": "The entry returns true (main.py:1-2).\n\nIt has no arguments (main.py:1).",
    "spokenAnswer": "The entry returns true and has no arguments.",
    "evidence": [{"path": "main.py", "startLine": 1, "endLine": 2, "label": "Entry"}],
    "recommendedMode": "brainstorm",
    "modeReason": "Explanation",
}


@pytest.fixture
def manifest(tmp_path: Path) -> Any:
    (tmp_path / "main.py").write_text("def start():\n    return True\n", encoding="utf8")
    return build_repository_manifest(str(tmp_path))


async def test_codex_answer_arrives_before_provider_finishes(
    manifest: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = asyncio.Event()
    release_selection = asyncio.Event()
    serialized = json.dumps(ANSWER)
    cut = serialized.index("It has no arguments")

    class Turn:
        id = "turn-answer"

        def __init__(self, selecting: bool = False) -> None:
            self.selecting = selecting

        async def stream(self) -> Any:
            yield SimpleNamespace(method="turn/started", payload=SimpleNamespace())
            if self.selecting:
                await release_selection.wait()
                yield SimpleNamespace(
                    method="item/completed",
                    payload=SimpleNamespace(
                        item=SimpleNamespace(text=json.dumps(SELECTION), phase="final_answer")
                    ),
                )
                yield SimpleNamespace(
                    method="turn/completed",
                    payload=SimpleNamespace(turn=SimpleNamespace(status="completed", error=None)),
                )
                return
            yield SimpleNamespace(
                method="item/started",
                payload=SimpleNamespace(
                    item=SimpleNamespace(
                        id="private", type="reasoning", text="never expose private reasoning"
                    )
                ),
            )
            yield SimpleNamespace(
                method="item/agentMessage/delta",
                payload=SimpleNamespace(
                    item_id="answer",
                    delta=serialized[:cut],
                ),
            )
            await release.wait()
            yield SimpleNamespace(
                method="item/agentMessage/delta",
                payload=SimpleNamespace(
                    item_id="answer",
                    delta=serialized[cut:],
                ),
            )
            yield SimpleNamespace(
                method="item/completed",
                payload=SimpleNamespace(
                    item=SimpleNamespace(text=serialized, phase="final_answer"),
                ),
            )
            yield SimpleNamespace(
                method="thread/tokenUsage/updated",
                payload=SimpleNamespace(
                    token_usage=SimpleNamespace(
                        last=SimpleNamespace(input_tokens=20, output_tokens=10)
                    ),
                ),
            )
            yield SimpleNamespace(
                method="turn/completed",
                payload=SimpleNamespace(
                    turn=SimpleNamespace(status="completed", error=None),
                ),
            )

        async def interrupt(self) -> None:
            release.set()

    class Thread:
        async def run(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("Both repository stages must use the SDK's event stream")

        async def turn(self, *args: Any, **kwargs: Any) -> Turn:
            assert "output_schema" in kwargs
            return Turn(selecting="files" in kwargs["output_schema"]["properties"])

    class Codex:
        def __init__(self, *args: Any) -> None:
            pass

        async def __aenter__(self) -> Codex:
            return self

        async def thread_start(self, **kwargs: Any) -> Thread:
            return Thread()

        async def close(self) -> None:
            pass

    monkeypatch.setattr("sentia_sidecar.codex_service.AsyncCodex", Codex)
    service = CodexRepositoryService("fixture")
    stream = service.stream_answer("Explain the entry", manifest)
    seen = []
    try:
        async with asyncio.timeout(2):
            while True:
                event = await anext(stream)
                seen.append(event)
                if event.activity and event.activity.message.startswith("Codex started choosing"):
                    assert not release_selection.is_set()
                    release_selection.set()
                if event.type == "passage":
                    break
        assert not release.is_set()
        assert "returns true" in seen[-1].text
        release.set()
        seen.extend([event async for event in stream])
        final = seen[-1].answer
        assert final is not None and final.answer == ANSWER["answer"]
        assert final.usage.input_tokens == 20
        assert release_selection.is_set()
        assert any(
            event.activity and "Read evidence from 1 file." in event.activity.message
            for event in seen
        )
        assert "private reasoning" not in json.dumps(
            [event.model_dump(mode="json") for event in seen]
        )
        assert "".join(event.text for event in seen if event.type == "delta") == final.answer
    finally:
        release.set()
        release_selection.set()
        await stream.aclose()
        await service.close()


async def test_claude_structured_tool_stream_arrives_before_final_result(manifest: Any) -> None:
    release = asyncio.Event()
    release_selection = asyncio.Event()
    serialized = json.dumps(ANSWER)
    cut = serialized.index("It has no arguments")

    def result(output: dict[str, Any]) -> ResultMessage:
        return ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=10,
            is_error=False,
            num_turns=1,
            session_id="fixture",
            structured_output=output,
        )

    async def query(**kwargs: Any) -> Any:
        if "FILE-SELECTION REPORT" not in kwargs["prompt"]:
            assert kwargs["options"].include_partial_messages
            yield SystemMessage(
                subtype="init", data={"session_id": "private-session", "api_key": "must-not-leak"}
            )
            await release_selection.wait()
            yield result(SELECTION)
            return
        assert kwargs["options"].include_partial_messages
        yield StreamEvent(
            uuid="start",
            session_id="fixture",
            event={
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "name": "StructuredOutput"},
            },
        )
        yield StreamEvent(
            uuid="first",
            session_id="fixture",
            event={
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": serialized[:cut]},
            },
        )
        await release.wait()
        yield StreamEvent(
            uuid="second",
            session_id="fixture",
            event={
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": serialized[cut:]},
            },
        )
        yield result(ANSWER)

    service = AnthropicRepositoryService("fixture", query_function=query)
    await service.connect("fixture-key", validate=False)
    stream = service.stream_answer("Explain the entry", manifest)
    seen = []
    try:
        async with asyncio.timeout(2):
            while True:
                event = await anext(stream)
                seen.append(event)
                if event.activity and event.activity.message.startswith("Claude started choosing"):
                    assert not release_selection.is_set()
                    release_selection.set()
                if event.type == "passage":
                    break
        assert not release.is_set()
        release.set()
        seen.extend([event async for event in stream])
        assert seen[-1].answer is not None
        assert seen[-1].answer.answer == ANSWER["answer"]
        assert release_selection.is_set()
        assert "must-not-leak" not in json.dumps([event.model_dump(mode="json") for event in seen])
        assert "".join(event.text for event in seen if event.type == "delta") == ANSWER["answer"]
    finally:
        release.set()
        release_selection.set()
        await stream.aclose()
