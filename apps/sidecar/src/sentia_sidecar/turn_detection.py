"""Reusable per-connection turn context, separate from microphone/session lifetime."""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

from sentia_sidecar.eot_model import EOTModel

logger = structlog.get_logger(__name__)


class CompletionScorer(Protocol):
    async def predict(self, messages: list[dict[str, str]]) -> float: ...


class LocalEOTScorer:
    """Share one model and serialize loading/inference outside the audio event loop."""

    def __init__(self, factory: Callable[[], EOTModel] = EOTModel) -> None:
        self.factory = factory
        self.model: EOTModel | None = None
        self.failed = False
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sentia-eot")
        self.warmup_task: asyncio.Future[Any] | None = None

    def _load(self) -> EOTModel:
        if self.failed:
            raise RuntimeError("EOT model is unavailable")
        if self.model is None:
            started = time.monotonic()
            try:
                self.model = self.factory()
            except Exception:
                self.failed = True
                raise
            logger.info("eot_model_ready", elapsed_ms=round((time.monotonic() - started) * 1000))
        return self.model

    def warmup(self) -> None:
        if self.warmup_task is not None:
            return
        self.warmup_task = asyncio.get_running_loop().run_in_executor(self.executor, self._load)

        def finished(task: asyncio.Future[Any]) -> None:
            if not task.cancelled() and (error := task.exception()) is not None:
                logger.warning("eot_model_unavailable", error_type=type(error).__name__)

        self.warmup_task.add_done_callback(finished)

    def _predict(self, messages: list[dict[str, str]]) -> float:
        return self._load().predict_eot_prob(messages)

    async def predict(self, messages: list[dict[str, str]]) -> float:
        return await asyncio.get_running_loop().run_in_executor(
            self.executor, self._predict, messages
        )

    def close(self) -> None:
        if self.warmup_task is not None:
            self.warmup_task.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)


@dataclass(frozen=True)
class CompletionDecision:
    generation: int
    revision: int
    probability: float | None
    complete: bool


class OpenAITurnDetector:
    """A turn reset clears the candidate, but preserves bounded conversation history."""

    def __init__(
        self,
        scorer: CompletionScorer,
        *,
        threshold: float = EOTModel.DEFAULT_THRESHOLD,
        inference_timeout: float = 0.35,
    ) -> None:
        self.scorer = scorer
        self.threshold = threshold
        self.inference_timeout = inference_timeout
        self.history: deque[dict[str, str]] = deque(maxlen=EOTModel.MAX_HISTORY - 1)
        self.transcript = ""
        self.transcript_updated_at = 0.0
        self.generation = 0
        self.revision = 0
        self.cached_decision: CompletionDecision | None = None

    def note_speech(self) -> None:
        self.revision += 1
        self.cached_decision = None

    def update_transcript(self, text: str) -> None:
        if self.transcript != text:
            self.transcript = text
            self.transcript_updated_at = asyncio.get_running_loop().time()
            self.revision += 1
            self.cached_decision = None

    def reset_turn(self) -> None:
        self.transcript = ""
        self.transcript_updated_at = 0.0
        self.generation += 1
        self.revision += 1
        self.cached_decision = None

    def add_context(self, role: str, text: str) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("Turn context supports user and assistant messages")
        if text.strip():
            self.history.append({"role": role, "content": text})
            self.revision += 1
            self.cached_decision = None

    async def evaluate_completion(self) -> CompletionDecision:
        if self.cached_decision is not None:
            return self.cached_decision
        generation, revision = self.generation, self.revision
        messages = [*self.history, {"role": "user", "content": self.transcript}]
        probability: float | None = None
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.inference_timeout):
                probability = await self.scorer.predict(messages)
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError("Invalid EOT completion score")
        except Exception as error:
            probability = None
            logger.warning("eot_scoring_fallback", error_type=type(error).__name__)
        decision = CompletionDecision(
            generation,
            revision,
            probability,
            probability is not None and probability >= self.threshold,
        )
        if (generation, revision) == (self.generation, self.revision):
            self.cached_decision = decision
        logger.debug(
            "eot_scored",
            probability=probability,
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
        return decision
