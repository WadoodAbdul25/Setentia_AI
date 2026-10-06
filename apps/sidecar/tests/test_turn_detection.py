from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest
from sentia_sidecar.eot_model import EOTModel
from sentia_sidecar.turn_detection import LocalEOTScorer, OpenAITurnDetector


class FakeScorer:
    def __init__(self, score: float = 0.2) -> None:
        self.score = score
        self.calls: list[list[dict[str, str]]] = []

    async def predict(self, messages: list[dict[str, str]]) -> float:
        self.calls.append(messages)
        return self.score


async def test_detector_caches_one_check_per_revision_and_keeps_history_across_turns() -> None:
    scorer = FakeScorer()
    detector = OpenAITurnDetector(scorer)
    detector.update_transcript("Explain authentication.")
    first = await detector.evaluate_completion()
    assert first.complete
    assert await detector.evaluate_completion() == first
    assert len(scorer.calls) == 1
    detector.reset_turn()
    detector.add_context("user", "Explain authentication.")
    detector.add_context("assistant", "Authentication uses a token.")
    detector.update_transcript("Where is that checked?")
    next_turn = await detector.evaluate_completion()
    assert next_turn.generation == first.generation + 1
    assert scorer.calls[-1] == [
        {"role": "user", "content": "Explain authentication."},
        {"role": "assistant", "content": "Authentication uses a token."},
        {"role": "user", "content": "Where is that checked?"},
    ]


async def test_scoring_context_is_bounded() -> None:
    scorer = FakeScorer()
    detector = OpenAITurnDetector(scorer)
    for index in range(100):
        detector.add_context("user", str(index))
    detector.update_transcript("A follow-up")
    await detector.evaluate_completion()
    assert len(scorer.calls[-1]) == 10
    assert scorer.calls[-1][0]["content"] == "91"


async def test_resumed_speech_invalidates_an_in_flight_score() -> None:
    started, finish = asyncio.Event(), asyncio.Event()

    class SlowScorer:
        async def predict(self, messages: list[dict[str, str]]) -> float:
            started.set()
            await finish.wait()
            return 0.9

    detector = OpenAITurnDetector(SlowScorer())
    detector.update_transcript("Tell me")
    task = asyncio.create_task(detector.evaluate_completion())
    await started.wait()
    detector.note_speech()
    detector.update_transcript("Tell me how authentication works")
    finish.set()
    result = await task
    assert result.revision != detector.revision
    assert detector.cached_decision is None


@pytest.mark.parametrize("failure", ["timeout", "exception", "nan", "out_of_range"])
async def test_model_failure_returns_a_cached_fallback(failure: str) -> None:
    class FailedScorer:
        async def predict(self, messages: list[dict[str, str]]) -> float:
            if failure == "timeout":
                await asyncio.Event().wait()
            if failure == "exception":
                raise RuntimeError("private transcript should not be logged")
            return float("nan") if failure == "nan" else 2.0

    detector = OpenAITurnDetector(FailedScorer(), inference_timeout=0.01)
    detector.update_transcript("Explain this")
    decision = await detector.evaluate_completion()
    assert not decision.complete
    assert decision.probability is None
    assert detector.cached_decision == decision


async def test_local_scorer_loads_once_and_runs_off_the_audio_thread() -> None:
    calls: list[int] = []
    loads: list[int] = []
    started, finish = threading.Event(), threading.Event()

    class Model:
        def predict_eot_prob(self, messages: list[dict[str, Any]]) -> float:
            calls.append(threading.get_ident())
            started.set()
            assert finish.wait(timeout=2)
            return 0.2

    def factory() -> EOTModel:
        loads.append(threading.get_ident())
        return Model()  # type: ignore[return-value]

    scorer = LocalEOTScorer(factory)
    scorer.warmup()
    scorer.warmup()
    task = asyncio.create_task(scorer.predict([{"role": "user", "content": "Explain this"}]))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        # The model is blocked, but the event loop is available to receive mic frames.
        assert not task.done()
        finish.set()
        assert await task == 0.2
        assert await scorer.predict([{"role": "user", "content": "Next question"}]) == 0.2
        assert len(loads) == 1
        assert len(set(calls + loads)) == 1
        assert calls[0] != threading.get_ident()
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)
        scorer.close()
