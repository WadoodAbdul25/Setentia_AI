from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from sentia_sidecar.voice import OpenAIRealtimeTranscriptionConnection, OpenAIVoiceOptions


class QueuedSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.close_code: int | None = None
        self.fail_commit = False
        self.push({"type": "session.updated"})

    def push(self, payload: dict[str, Any]) -> None:
        self.incoming.put_nowait(json.dumps(payload))

    async def recv(self) -> str:
        raw = await self.incoming.get()
        assert raw is not None
        return raw

    async def send(self, raw: str) -> None:
        payload = json.loads(raw)
        if self.fail_commit and payload["type"] == "input_audio_buffer.commit":
            raise RuntimeError("Socket failed")
        self.sent.append(payload)

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.close_code = 1000
            self.incoming.put_nowait(None)

    def __aiter__(self) -> AsyncIterator[str]:
        async def iterate() -> AsyncIterator[str]:
            while (raw := await self.incoming.get()) is not None:
                yield raw

        return iterate()

    @property
    def commits(self) -> int:
        return sum(payload["type"] == "input_audio_buffer.commit" for payload in self.sent)


class Scorer:
    def __init__(self, probability: float = 0.2) -> None:
        self.probability = probability
        self.calls: list[list[dict[str, str]]] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.failure = False

    async def predict(self, messages: list[dict[str, str]]) -> float:
        self.calls.append(messages)
        self.started.set()
        await self.release.wait()
        if self.failure:
            raise RuntimeError("Unavailable model")
        return self.probability


async def eventually(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(1):
        while not predicate():  # noqa: ASYNC110 -- poll state in a bounded test helper
            await asyncio.sleep(0.001)


def delta(socket: QueuedSocket, item_id: str, text: str) -> None:
    socket.push(
        {
            "type": "conversation.item.input_audio_transcription.delta",
            "item_id": item_id,
            "delta": text,
        }
    )


def final(socket: QueuedSocket, item_id: str, text: str) -> None:
    socket.push(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": item_id,
            "transcript": text,
        }
    )


SPEECH = (1000).to_bytes(2, "little", signed=True) * 2400


@pytest.fixture
async def pipeline() -> AsyncIterator[
    tuple[OpenAIRealtimeTranscriptionConnection, QueuedSocket, Scorer, list[dict[str, Any]]]
]:
    socket, scorer = QueuedSocket(), Scorer()
    connection = OpenAIRealtimeTranscriptionConnection(socket, scorer)  # type: ignore[arg-type]
    await connection.configure(OpenAIVoiceOptions(), ())
    events: list[dict[str, Any]] = []

    async def receive() -> None:
        async for event in connection.messages():
            events.append(event)

    receiver = asyncio.create_task(receive())
    try:
        yield connection, socket, scorer, events
    finally:
        scorer.release.set()
        await connection.close()
        await asyncio.wait_for(receiver, timeout=1)


async def prepare_pause(
    connection: OpenAIRealtimeTranscriptionConnection,
    socket: QueuedSocket,
    *,
    item_id: str = "first",
    text: str = "Explain the voice flow.",
    elapsed: float = 0.6,
) -> None:
    await connection.send_audio(SPEECH)
    delta(socket, item_id, text)
    assert connection.turn_detector is not None
    await eventually(lambda: connection.turn_detector.transcript == text)  # type: ignore[union-attr]
    connection.turn_detector.transcript_updated_at -= 0.2
    assert connection.last_speech_at is not None
    connection.last_speech_at -= elapsed


async def test_semantic_completion_commits_early_and_exposes_its_score(pipeline: Any) -> None:
    connection, socket, scorer, events = pipeline
    await prepare_pause(connection, socket)
    await eventually(lambda: socket.commits == 1)
    assert scorer.calls[-1][-1]["content"] == "Explain the voice flow."
    final(socket, "first", "Explain the voice flow.")
    await eventually(lambda: any(event.get("event") == "EndOfTurn" for event in events))
    completed = next(event for event in events if event.get("event") == "EndOfTurn")
    assert completed["end_of_turn_confidence"] == 0.2
    assert completed["turn_index"] == 0
    assert not socket.closed


@pytest.mark.parametrize("failure", [False, True])
async def test_incomplete_or_failed_score_waits_for_silence_fallback(
    pipeline: Any, failure: bool
) -> None:
    connection, socket, scorer, _ = pipeline
    scorer.probability = 0.001
    scorer.failure = failure
    await prepare_pause(connection, socket, text="Can you explain...")
    await scorer.started.wait()
    await asyncio.sleep(0.01)
    assert socket.commits == 0
    connection.last_speech_at -= 1.5
    await eventually(lambda: socket.commits == 1)
    assert len(scorer.calls) == 1


async def test_resuming_speech_during_scoring_cancels_the_candidate(pipeline: Any) -> None:
    connection, socket, scorer, events = pipeline
    scorer.release.clear()
    await prepare_pause(connection, socket)
    await scorer.started.wait()
    await connection.send_audio(SPEECH)
    delta(socket, "first", " And the playback?")
    await eventually(lambda: connection.turn_detector.transcript.endswith("playback?"))
    scorer.release.set()
    await asyncio.sleep(0.07)
    assert socket.commits == 0
    assert any(event.get("event") == "TurnResumed" for event in events)


async def test_scoring_cannot_extend_the_silence_deadline(pipeline: Any) -> None:
    connection, socket, scorer, _ = pipeline
    scorer.release.clear()
    await prepare_pause(connection, socket, elapsed=1.3)
    await eventually(lambda: socket.commits == 1)
    assert len(scorer.calls) == 1


async def test_explicit_stop_bypasses_in_flight_scoring_and_commits_once(pipeline: Any) -> None:
    connection, socket, scorer, events = pipeline
    scorer.release.clear()
    await prepare_pause(connection, socket)
    await scorer.started.wait()
    await connection.close_stream()
    await connection.close_stream()
    assert socket.commits == 1
    scorer.release.set()
    final(socket, "first", "Explain the voice flow.")
    await eventually(lambda: socket.closed)
    assert any(event.get("event") == "EndOfTurn" for event in events)


async def test_next_turn_audio_survives_a_late_previous_final(pipeline: Any) -> None:
    connection, socket, _, events = pipeline
    await prepare_pause(connection, socket)
    await eventually(lambda: socket.commits == 1)
    await connection.send_audio(SPEECH)
    delta(socket, "second", "Where does that happen?")
    await eventually(lambda: connection.current_item_id == "second")
    assert connection.speech_started
    final(socket, "first", "Explain the voice flow.")
    await eventually(lambda: not connection.commit_in_flight)
    assert connection.speech_started
    assert connection.turn_detector.transcript == "Where does that happen?"
    assert connection.endpoint_task is not None
    connection.add_assistant_context("Turn detection happens in the sidecar.")
    connection.turn_detector.transcript_updated_at -= 0.2
    connection.last_speech_at -= 0.6
    await eventually(lambda: socket.commits == 2)
    final(socket, "second", "Where does that happen?")
    await eventually(lambda: sum(event.get("event") == "EndOfTurn" for event in events) == 2)
    finals = [event for event in events if event.get("event") == "EndOfTurn"]
    assert [event["turn_index"] for event in finals] == [0, 1]
    assert not socket.closed


async def test_duplicate_finals_and_late_old_deltas_do_not_replace_next_turn(pipeline: Any) -> None:
    connection, socket, _, events = pipeline
    await prepare_pause(connection, socket)
    await eventually(lambda: socket.commits == 1)
    final(socket, "first", "Explain the voice flow.")
    await eventually(lambda: connection.completed_turn)
    await connection.send_audio(SPEECH)
    delta(socket, "second", "A new question")
    final(socket, "first", "Explain the voice flow.")
    delta(socket, "first", " stale fragment")
    await eventually(lambda: connection.current_item_id == "second")
    await asyncio.sleep(0.01)
    assert connection.turn_detector.transcript == "A new question"
    assert sum(event.get("event") == "EndOfTurn" for event in events) == 1


async def test_endpoint_task_failure_is_delivered_to_the_voice_consumer(pipeline: Any) -> None:
    connection, socket, _, events = pipeline
    socket.fail_commit = True
    await prepare_pause(connection, socket)
    await eventually(lambda: any(event.get("type") == "Error" for event in events))
    assert socket.closed
    assert connection.closing
    assert connection.endpoint_task is None


async def test_speech_during_commit_write_gets_its_own_endpoint_timer(
    pipeline: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, socket, _, _ = pipeline
    started, finish = asyncio.Event(), asyncio.Event()
    original_send = socket.send

    async def backpressured_send(raw: str) -> None:
        if json.loads(raw)["type"] == "input_audio_buffer.commit":
            started.set()
            await finish.wait()
        await original_send(raw)

    monkeypatch.setattr(socket, "send", backpressured_send)
    try:
        await prepare_pause(connection, socket)
        await started.wait()
        first_timer = connection.endpoint_task
        await connection.send_audio(SPEECH)
        delta(socket, "second", "A follow-up question")
        await eventually(lambda: connection.current_item_id == "second")
        finish.set()
        await eventually(lambda: connection.endpoint_task is not first_timer)
        assert connection.endpoint_task is not None
        assert connection.speech_started
    finally:
        finish.set()


async def test_late_old_commit_ack_does_not_reassign_the_new_pending_item(pipeline: Any) -> None:
    connection, socket, _, events = pipeline
    await prepare_pause(connection, socket)
    await eventually(lambda: socket.commits == 1)
    final(socket, "first", "Explain the voice flow.")
    await eventually(lambda: connection.completed_turn)
    await prepare_pause(connection, socket, item_id="second", text="A second question")
    await eventually(lambda: socket.commits == 2)
    socket.push({"type": "input_audio_buffer.committed", "item_id": "first"})
    final(socket, "second", "A second question")
    await eventually(lambda: sum(event.get("event") == "EndOfTurn" for event in events) == 2)
    assert not connection.commit_in_flight


async def test_disabled_semantics_keeps_the_silence_fallback() -> None:
    socket, scorer = QueuedSocket(), Scorer()
    connection = OpenAIRealtimeTranscriptionConnection(socket, scorer)  # type: ignore[arg-type]
    await connection.configure(OpenAIVoiceOptions(semantic_eot_enabled=False), ())
    try:
        assert connection.turn_detector is None
        await connection.send_audio(SPEECH)
        assert connection.last_speech_at is not None
        connection.last_speech_at -= 1.5
        assert connection.endpoint_task is not None
        await asyncio.wait_for(connection.endpoint_task, timeout=1)
        assert socket.commits == 1
        assert not scorer.calls
    finally:
        await connection.close()


async def test_explicit_finish_turn_does_not_end_the_connection(pipeline: Any) -> None:
    connection, socket, scorer, _ = pipeline
    await connection.send_audio(SPEECH)
    delta(socket, "first", "Finish this turn now")
    await eventually(lambda: connection.current_item_id == "first")
    await connection.finish_turn()
    assert socket.commits == 1
    assert not connection.finish_requested
    assert not scorer.calls
    final(socket, "first", "Finish this turn now")
    await eventually(lambda: connection.completed_turn)
    assert not socket.closed
    await connection.send_audio(SPEECH)
    assert connection.speech_started


async def test_explicit_finish_while_previous_final_is_pending_is_not_lost(pipeline: Any) -> None:
    connection, socket, _, _ = pipeline
    await prepare_pause(connection, socket)
    await eventually(lambda: socket.commits == 1)
    await connection.send_audio(SPEECH)
    delta(socket, "second", "Finish the follow-up now")
    await eventually(lambda: connection.current_item_id == "second")
    await connection.finish_turn()
    final(socket, "first", "Explain the voice flow.")
    await eventually(lambda: socket.commits == 2)
    assert not connection.finish_requested
    assert not socket.closed


async def test_unexpected_clean_provider_close_is_reported_not_left_waiting(pipeline: Any) -> None:
    connection, socket, _, events = pipeline
    await socket.close()
    await eventually(lambda: any(event.get("type") == "Error" for event in events))
    assert connection.closing


async def test_fallback_commit_maps_an_item_that_has_no_partial_transcript(pipeline: Any) -> None:
    connection, socket, _, events = pipeline
    await connection.send_audio(SPEECH)
    connection.last_speech_at -= 1.5
    await eventually(lambda: socket.commits == 1)
    socket.push({"type": "input_audio_buffer.committed", "item_id": "without-delta"})
    final(socket, "without-delta", "Delayed transcript")
    await eventually(lambda: connection.completed_turn)
    assert not connection.commit_in_flight
    assert (
        next(event for event in events if event.get("event") == "EndOfTurn")[
            "end_of_turn_confidence"
        ]
        is None
    )
