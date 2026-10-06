from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sentia_sidecar.answer_stream import (
    AnswerStreamAccumulator,
    AnswerStreamEvent,
    SDKActivityReporter,
    emit_read_activity,
    emit_selection_activity,
    stream_answer_task,
)
from sentia_sidecar.protocol import RepositoryAnswer, TokenUsage, WorkingMode
from sentia_sidecar.repository import build_repository_manifest, build_selected_repository_context


@pytest.fixture
def stream_context(tmp_path: Path) -> Any:
    (tmp_path / "main.py").write_text("def start():\n    return True\n", encoding="utf8")
    manifest = build_repository_manifest(str(tmp_path))
    return build_selected_repository_context(manifest, [("main.py", "full")], question="Explain")


def completed(text: str) -> RepositoryAnswer:
    return RepositoryAnswer(
        answer=text,
        spoken_answer="The entry returns true.",
        evidence=[],
        recommended_mode=WorkingMode.BRAINSTORM,
        mode_reason="Explanation",
        model="fixture",
        files_scanned=1,
        files_read=1,
        selected_files=["main.py"],
        usage=TokenUsage(input_tokens=1, output_tokens=1),
    )


async def test_json_stream_only_emits_answer_text_and_checked_complete_paragraphs(
    stream_context: Any,
) -> None:
    seen: list[AnswerStreamEvent] = []

    async def emit(event: AnswerStreamEvent) -> None:
        seen.append(event)

    accumulator = AnswerStreamAccumulator(stream_context, emit)
    text = "The entry returns true (main.py:1-2).\n\nAnother fact is coming"
    output = json.dumps(
        {"answer": text, "spokenAnswer": "not display text", "modeReason": "private metadata"}
    )
    for end in range(1, len(output) + 1):
        await accumulator.update(output[:end])
    assert "".join(event.text for event in seen if event.type == "delta") == text
    passages = [event for event in seen if event.type == "passage"]
    assert len(passages) == 1
    assert passages[0].evidence[0].path == "main.py"
    assert "Another fact" not in passages[0].text
    await accumulator.finish(completed(text))
    assert [event.text for event in seen if event.type == "passage"][-1] == "Another fact is coming"


@pytest.mark.parametrize(
    "text",
    [
        "Unfinished text",
        "No citation here.\n\n",
        "Bad reference (elsewhere.py:1).\n\n",
        "Wrong line (main.py:500).\n\n",
        "Mixed (main.py:1) and (elsewhere.py:1).\n\n",
        "main.py:1\n```python\ndef start():\n\n",
    ],
)
async def test_unchecked_or_incomplete_passages_are_not_spoken(
    stream_context: Any, text: str
) -> None:
    seen: list[AnswerStreamEvent] = []

    async def emit(event: AnswerStreamEvent) -> None:
        seen.append(event)

    accumulator = AnswerStreamAccumulator(stream_context, emit)
    await accumulator.update(json.dumps({"answer": text}))
    assert not any(event.type == "passage" for event in seen)


async def test_unicode_escape_boundaries_and_repeated_snapshots_do_not_duplicate_text(
    stream_context: Any,
) -> None:
    seen: list[AnswerStreamEvent] = []

    async def emit(event: AnswerStreamEvent) -> None:
        seen.append(event)

    accumulator = AnswerStreamAccumulator(stream_context, emit)
    output = json.dumps({"answer": 'Hello 👋. A "quote" and a backslash \\.'})
    for end in range(1, len(output) + 1):
        await accumulator.update(output[:end])
    await accumulator.update(output)
    assert (
        "".join(event.text for event in seen if event.type == "delta")
        == json.loads(output)["answer"]
    )


async def test_final_answer_cannot_silently_replace_streamed_claims(stream_context: Any) -> None:
    async def emit(event: AnswerStreamEvent) -> None:
        pass

    accumulator = AnswerStreamAccumulator(stream_context, emit)
    await accumulator.update(json.dumps({"answer": "First answer"}))
    with pytest.raises(ValueError, match="differs"):
        await accumulator.finish(completed("Different answer"))


async def test_stream_generator_cancels_producer_when_consumer_disconnects() -> None:
    cancelled = asyncio.Event()

    async def run(emit: Any) -> RepositoryAnswer:
        try:
            await emit(AnswerStreamEvent(type="delta", text="First"))
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        raise AssertionError("unreachable")

    stream = stream_answer_task(run)
    assert (await anext(stream)).text == "First"
    await stream.aclose()
    assert cancelled.is_set()


async def test_work_log_distinguishes_selection_from_included_evidence(stream_context: Any) -> None:
    seen: list[AnswerStreamEvent] = []

    async def emit(event: AnswerStreamEvent) -> None:
        seen.append(event)

    await emit_selection_activity(emit, [("main.py", "outline"), ("missing.py", "full")])
    await emit_read_activity(
        emit,
        replace(stream_context, truncated_files=("main.py",), omitted_files=("missing.py",)),
    )
    selection, read = (event.activity for event in seen)
    assert selection is not None and read is not None
    assert "Selected 2 files" in selection.message
    assert "main.py — outline requested" in selection.details
    assert "Read evidence from 1 file." in read.message
    assert any("not complete files: main.py" in detail for detail in read.details)
    assert any("Not included" in detail and "missing.py" in detail for detail in read.details)


async def test_sdk_lifecycle_updates_are_deduplicated_and_do_not_regress() -> None:
    seen: list[AnswerStreamEvent] = []

    async def emit(event: AnswerStreamEvent) -> None:
        seen.append(event)

    reporter = SDKActivityReporter(emit, "Claude", "repository_answer")
    await reporter.report("started")
    await reporter.report("formatting")
    for _ in range(100):
        await reporter.report("generating")
        await reporter.report("formatting")
    await reporter.report("finished")
    assert len(seen) == 3
    assert seen[-1].activity is not None
    assert seen[-1].activity.status == "completed"
