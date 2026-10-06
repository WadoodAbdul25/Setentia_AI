"""Incremental display text and independently citation-checked speech passages."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_core import from_json

from sentia_sidecar.protocol import EvidenceRange, RepositoryAnswer
from sentia_sidecar.repository import ReadMode, RepositoryContext

MAX_STREAM_CHARACTERS = 80_000
PROGRESS_MESSAGES = {
    "file_selection": "Searching the files.",
    "content_read": "Checking the code.",
    "repository_answer": "Putting the answer together.",
    "validation": "Almost done—checking the references.",
}


class AnswerActivity(BaseModel):
    """A safe, factual work-log entry, never an SDK payload or private reasoning."""

    id: str
    stage: str
    status: Literal["working", "completed"] = "working"
    message: str
    details: list[str] = Field(default_factory=list)


class AnswerStreamEvent(BaseModel):
    type: Literal["progress", "activity", "delta", "passage", "complete"]
    text: str = ""
    stage: str | None = None
    evidence: list[EvidenceRange] = Field(default_factory=list)
    answer: RepositoryAnswer | None = None
    activity: AnswerActivity | None = None


AnswerEventCallback = Callable[[AnswerStreamEvent], Awaitable[None]]
TextSnapshotCallback = Callable[[str], Awaitable[None]]


async def emit_progress(callback: AnswerEventCallback | None, stage: str) -> None:
    if callback is not None:
        await callback(
            AnswerStreamEvent(type="progress", stage=stage, text=PROGRESS_MESSAGES[stage])
        )
        await emit_activity(callback, stage, PROGRESS_MESSAGES[stage])


async def emit_activity(
    callback: AnswerEventCallback | None,
    stage: str,
    message: str,
    *,
    status: Literal["working", "completed"] = "working",
    details: list[str] | None = None,
) -> None:
    if callback is not None:
        await callback(
            AnswerStreamEvent(
                type="activity",
                activity=AnswerActivity(
                    id=stage, stage=stage, status=status, message=message, details=details or []
                ),
            )
        )


async def emit_selection_activity(
    callback: AnswerEventCallback | None, requested: list[tuple[str, ReadMode]]
) -> None:
    file_word = "file" if len(requested) == 1 else "files"
    await emit_activity(
        callback,
        "file_selection",
        f"Selected {len(requested)} {file_word} to inspect. Reading the evidence next.",
        status="completed",
        details=[
            f"{path} — {'outline requested' if mode == 'outline' else 'contents requested'}"
            for path, mode in requested
        ],
    )


async def emit_read_activity(
    callback: AnswerEventCallback | None, repository: RepositoryContext
) -> None:
    details = list(repository.selected_files)
    if repository.truncated_files:
        count = len(repository.truncated_files)
        file_word = "file" if count == 1 else "files"
        details.append(
            f"{count} {file_word} supplied as outlines or shortened excerpts, "
            "not complete files: " + ", ".join(repository.truncated_files)
        )
    if repository.omitted_files:
        details.append(
            "Not included in the answer's evidence: " + ", ".join(repository.omitted_files)
        )
    file_word = "file" if repository.files_read == 1 else "files"
    await emit_activity(
        callback,
        "content_read",
        f"Read evidence from {repository.files_read} {file_word}. "
        "Using it to answer your question.",
        status="completed",
        details=details,
    )


class SDKActivityReporter:
    """Translate native lifecycle signals, without displaying their raw content."""

    def __init__(self, callback: AnswerEventCallback, provider: str, stage: str) -> None:
        self.callback = callback
        self.provider = provider
        self.stage = stage
        self.last_signal = -1

    async def report(
        self, signal: Literal["started", "generating", "formatting", "finished"]
    ) -> None:
        rank = ("started", "generating", "formatting", "finished").index(signal)
        if rank <= self.last_signal:
            return
        self.last_signal = rank
        selecting = self.stage == "file_selection"
        started = (
            "choosing relevant files" if selecting else "drafting the answer from file evidence"
        )
        generating = "choosing which files to inspect" if selecting else "working on the answer"
        formatting = "the file list" if selecting else "the answer and its references"
        finished = "the file-selection step" if selecting else "drafting the answer"
        messages = {
            "started": f"{self.provider} started {started}.",
            "generating": f"{self.provider} is {generating}.",
            "formatting": f"{self.provider} is preparing {formatting}.",
            "finished": f"{self.provider} finished {finished}. Checking the result next.",
        }
        await emit_activity(
            self.callback,
            self.stage,
            messages[signal],
            status="completed" if signal == "finished" else "working",
        )


async def stream_answer_task(
    run: Callable[[AnswerEventCallback], Awaitable[RepositoryAnswer]],
) -> AsyncIterator[AnswerStreamEvent]:
    queue: asyncio.Queue[AnswerStreamEvent | Exception] = asyncio.Queue(maxsize=8)

    async def emit(event: AnswerStreamEvent) -> None:
        await queue.put(event)

    async def produce() -> None:
        try:
            result = await run(emit)
            await emit(AnswerStreamEvent(type="complete", answer=result))
        except Exception as error:
            await queue.put(error)

    task = asyncio.create_task(produce())
    try:
        while True:
            event = await queue.get()
            if isinstance(event, Exception):
                raise event
            yield event
            if event.type == "complete":
                return
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class AnswerStreamAccumulator:
    """Never expose JSON metadata, reasoning, or incomplete/unchecked speech passages."""

    def __init__(self, repository: RepositoryContext, emit: AnswerEventCallback) -> None:
        self.repository = repository
        self.emit = emit
        self.text = ""
        self.speech_offset = 0
        self.passages = 0

    async def update(self, snapshot: str) -> None:
        if len(snapshot) > MAX_STREAM_CHARACTERS:
            raise ValueError("Repository answer stream exceeded its size limit")
        try:
            parsed = from_json(snapshot, allow_partial="trailing-strings")
        except ValueError:
            return
        candidate = parsed.get("answer") if isinstance(parsed, dict) else None
        if not isinstance(candidate, str):
            return
        await self.update_text(candidate)

    async def update_text(self, text: str) -> None:
        if len(text) > MAX_STREAM_CHARACTERS or not text.startswith(self.text):
            raise ValueError("Repository answer stream changed previously emitted text")
        delta = text[len(self.text) :]
        self.text = text
        if delta:
            await self.emit(AnswerStreamEvent(type="delta", text=delta))
        # A Markdown paragraph must be complete, including any fenced code.
        while True:
            accepted_end: int | None = None
            for match in re.finditer(r"\n\s*\n", self.text[self.speech_offset :]):
                end = self.speech_offset + match.end()
                passage = self.text[self.speech_offset : end]
                if passage.count("```") % 2 or passage.count("~~~") % 2:
                    continue
                evidence = self._citations(passage)
                if evidence:
                    await self.emit(
                        AnswerStreamEvent(type="passage", text=passage, evidence=evidence)
                    )
                    self.passages += 1
                    accepted_end = end
                    break
            if accepted_end is None:
                break
            self.speech_offset = accepted_end

    def _citations(self, text: str) -> list[EvidenceRange]:
        found = re.findall(r"([\w./-]+\.[\w]+):(\d+)(?:-(\d+))?", text)
        if not found:
            return []
        evidence: list[EvidenceRange] = []
        for path, start_text, end_text in found:
            start, end = int(start_text), int(end_text or start_text)
            maximum = self.repository.included_ranges.get(path)
            displayed = (
                self.repository.included_lines.get(path)
                if self.repository.included_lines is not None
                else None
            )
            if (
                maximum is None
                or start < 1
                or end < start
                or end > maximum
                or (
                    displayed is not None
                    and any(line not in displayed for line in range(start, end + 1))
                )
            ):
                return []
            item = EvidenceRange(path=path, start_line=start, end_line=end, label="Source")
            if item not in evidence:
                evidence.append(item)
        return evidence

    async def finish(self, answer: RepositoryAnswer) -> None:
        # Result-only SDKs still work; they simply cannot provide early passages.
        final_text = answer.answer
        if self.text and self.text.strip() != final_text.strip():
            raise ValueError("Final repository answer differs from its streamed draft")
        if not self.text:
            await self.update_text(final_text)
        tail = self.text[self.speech_offset :].strip()
        if tail:
            # Full-answer validation has now completed, so uncited caveats may be narrated too.
            await self.emit(AnswerStreamEvent(type="passage", text=tail, evidence=answer.evidence))
            self.speech_offset = len(self.text)
