from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import math
import re
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from sentia_sidecar.repository import RepositoryManifest
from sentia_sidecar.turn_detection import CompletionScorer, LocalEOTScorer, OpenAITurnDetector

FLUX_MODELS = {"flux-general-en", "flux-general-multi"}
OPENAI_TRANSCRIPTION_MODELS = {"gpt-live-transcribe"}
OPENAI_REALTIME_MODEL = "gpt-realtime"
FLUX_EVENTS = {"StartOfTurn", "Update", "EagerEndOfTurn", "TurnResumed", "EndOfTurn"}
SUPPORTED_ENDPOINT_SCHEMES = {"ws", "wss", "http", "https"}
MAX_KEYTERMS = 100
DEFAULT_TURN_SILENCE_MS = 1_500
CODE_CONTEXT_WORDS = {
    "call",
    "class",
    "explain",
    "file",
    "find",
    "fix",
    "function",
    "implement",
    "method",
    "open",
    "run",
    "show",
    "test",
    "use",
}


class VoiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceOptions:
    model: str = "flux-general-en"
    endpoint: str = "wss://api.deepgram.com"
    language_hints: tuple[str, ...] = ()
    eot_threshold: float = 0.8
    eager_eot_threshold: float = 0.5
    eot_timeout_ms: int = DEFAULT_TURN_SILENCE_MS
    encoding: str | None = None
    sample_rate: int | None = None

    def __post_init__(self) -> None:
        if self.model not in FLUX_MODELS:
            raise VoiceError(f"Unsupported Flux model: {self.model}")
        if self.model == "flux-general-en" and self.language_hints:
            raise VoiceError("language_hint is only valid with flux-general-multi")
        if not 0.5 <= self.eot_threshold <= 0.9:
            raise VoiceError("eot_threshold must be between 0.5 and 0.9")
        if not 0.3 <= self.eager_eot_threshold <= 0.9:
            raise VoiceError("eager_eot_threshold must be between 0.3 and 0.9")
        if not 500 <= self.eot_timeout_ms <= 60_000:
            raise VoiceError("eot_timeout_ms must be between 500 and 60000")
        if (self.encoding is None) != (self.sample_rate is None):
            raise VoiceError("encoding and sample_rate must be provided together")
        if self.encoding not in {None, "linear16"}:
            raise VoiceError(f"Unsupported voice encoding: {self.encoding}")
        if self.sample_rate is not None and self.sample_rate <= 0:
            raise VoiceError("sample_rate must be positive")


@dataclass(frozen=True)
class OpenAIVoiceOptions:
    model: str = "gpt-live-transcribe"
    endpoint: str = "wss://api.openai.com/v1/realtime?model=gpt-realtime"
    sample_rate: int = 24_000
    silence_duration_ms: int = DEFAULT_TURN_SILENCE_MS
    speech_rms_threshold: int = 350
    semantic_eot_enabled: bool = True
    semantic_silence_ms: int = 600
    eot_threshold: float = 0.03
    eot_inference_timeout_ms: int = 350

    def __post_init__(self) -> None:
        if self.model not in OPENAI_TRANSCRIPTION_MODELS:
            raise VoiceError(f"Unsupported OpenAI transcription model: {self.model}")
        if self.sample_rate != 24_000:
            raise VoiceError("OpenAI live transcription requires 24 kHz PCM audio")
        if not 500 <= self.silence_duration_ms <= 60_000:
            raise VoiceError("OpenAI silence_duration_ms must be between 500 and 60000")
        if not 1 <= self.speech_rms_threshold <= 32_767:
            raise VoiceError("OpenAI speech_rms_threshold must be between 1 and 32767")
        if not 100 <= self.semantic_silence_ms <= 60_000:
            raise VoiceError("OpenAI semantic_silence_ms must be between 100 and 60000")
        if not math.isfinite(self.eot_threshold) or not 0 <= self.eot_threshold <= 1:
            raise VoiceError("OpenAI eot_threshold must be between 0 and 1")
        if not 1 <= self.eot_inference_timeout_ms <= 60_000:
            raise VoiceError("OpenAI EOT inference timeout must be between 1 and 60000")


@dataclass(frozen=True)
class VocabularyEntry:
    canonical: str
    spoken: str
    kind: str
    path: str
    active: bool


@dataclass(frozen=True)
class Correction:
    original: str
    replacement: str
    confidence: float
    kind: str
    path: str
    applied: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "original": self.original,
            "replacement": self.replacement,
            "confidence": round(self.confidence, 3),
            "kind": self.kind,
            "path": self.path,
            "applied": self.applied,
        }


@dataclass(frozen=True)
class CorrectedTranscript:
    text: str
    corrections: tuple[Correction, ...]


def _spoken_form(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = re.sub(r"[_.$/\\-]+", " ", value)
    return " ".join(re.findall(r"[A-Za-z0-9]+", value)).lower()


def _structure_symbols(structure: str) -> list[tuple[str, str]]:
    return [
        (match.group("name"), match.group("kind"))
        for match in re.finditer(
            r"(?P<kind>ClassDef|FunctionDef|AsyncFunctionDef|class|function|const|let|var|"
            r"interface|type):(?P<name>[A-Za-z_$][\w$]*)@\d+",
            structure,
        )
    ]


def build_vocabulary(
    repository: RepositoryManifest,
    active_file: str | None = None,
) -> tuple[VocabularyEntry, ...]:
    normalized_active = active_file.replace("\\", "/") if active_file else None
    entries: dict[tuple[str, str], VocabularyEntry] = {}
    for item in repository.files.values():
        active = item.path == normalized_active
        path = Path(item.path)
        file_name = path.name
        stem = path.stem
        for canonical, kind in ((file_name, "file"), (stem, "file")):
            spoken = _spoken_form(canonical)
            if len(spoken) >= 3:
                entries[(canonical, item.path)] = VocabularyEntry(
                    canonical=canonical,
                    spoken=spoken,
                    kind=kind,
                    path=item.path,
                    active=active,
                )
        for canonical, kind in _structure_symbols(item.structure):
            spoken = _spoken_form(canonical)
            if len(spoken) >= 3:
                entries[(canonical, item.path)] = VocabularyEntry(
                    canonical=canonical,
                    spoken=spoken,
                    kind=kind,
                    path=item.path,
                    active=active,
                )
    return tuple(
        sorted(
            entries.values(),
            key=lambda entry: (
                not entry.active,
                entry.kind != "file",
                entry.path.lower(),
                entry.canonical.lower(),
            ),
        )
    )


def select_keyterms(vocabulary: tuple[VocabularyEntry, ...]) -> tuple[str, ...]:
    selected: list[str] = []
    seen: set[str] = set()
    for entry in vocabulary:
        normalized = entry.canonical.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        selected.append(entry.canonical)
        if len(selected) == MAX_KEYTERMS:
            break
    return tuple(selected)


class RepositoryTranscriptCorrector:
    def __init__(self, vocabulary: tuple[VocabularyEntry, ...]) -> None:
        self.vocabulary = vocabulary

    def correct(self, transcript: str) -> CorrectedTranscript:
        words = list(re.finditer(r"[A-Za-z0-9_$.-]+", transcript))
        if not words or not self.vocabulary:
            return CorrectedTranscript(transcript, ())

        candidates: list[tuple[float, int, int, VocabularyEntry, str, bool]] = []
        for start in range(len(words)):
            for width in range(1, min(5, len(words) - start) + 1):
                end = start + width
                original = transcript[words[start].start() : words[end - 1].end()]
                spoken = _spoken_form(original)
                if len(spoken) < 3:
                    continue
                for entry in self.vocabulary:
                    token_delta = abs(len(spoken.split()) - len(entry.spoken.split()))
                    if token_delta > 1:
                        continue
                    similarity = SequenceMatcher(None, spoken, entry.spoken).ratio()
                    nearby_words = {
                        match.group().casefold() for match in words[max(0, start - 3) : end + 2]
                    }
                    has_code_context = bool(nearby_words.intersection(CODE_CONTEXT_WORDS))
                    context_boost = 0.12 if entry.active else 0.1 if has_code_context else 0.0
                    exact_boost = 0.08 if spoken == entry.spoken else 0.0
                    score = min(1.0, similarity * 0.8 + context_boost + exact_boost)
                    if score >= 0.72 and original != entry.canonical:
                        candidates.append(
                            (score, start, end, entry, original, entry.active or has_code_context)
                        )

        chosen: list[tuple[float, int, int, VocabularyEntry, str, bool]] = []
        occupied: set[int] = set()
        for candidate in sorted(candidates, key=lambda item: (-item[0], -(item[2] - item[1]))):
            _, start, end, _, _, _ = candidate
            if occupied.intersection(range(start, end)):
                continue
            chosen.append(candidate)
            occupied.update(range(start, end))

        corrections: list[Correction] = []
        replacements: list[tuple[int, int, str]] = []
        for score, start, end, entry, original, contextual in sorted(
            chosen, key=lambda item: item[1]
        ):
            applied = score >= 0.84 and contextual
            corrections.append(
                Correction(
                    original=original,
                    replacement=entry.canonical,
                    confidence=score,
                    kind=entry.kind,
                    path=entry.path,
                    applied=applied,
                )
            )
            if applied:
                replacements.append((words[start].start(), words[end - 1].end(), entry.canonical))

        corrected = transcript
        for start, end, replacement in reversed(replacements):
            corrected = corrected[:start] + replacement + corrected[end:]
        return CorrectedTranscript(corrected, tuple(corrections))


def build_flux_url(options: VoiceOptions) -> str:
    endpoint = options.endpoint.strip().rstrip("/")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in SUPPORTED_ENDPOINT_SCHEMES or not parsed.netloc:
        raise VoiceError("Deepgram endpoint must be an absolute ws:// or wss:// URL")
    scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme, parsed.scheme)
    base_path = parsed.path.rstrip("/")
    path = base_path if base_path.endswith("/v2/listen") else f"{base_path}/v2/listen"
    query: list[tuple[str, str]] = [
        ("model", options.model),
        ("eot_threshold", str(options.eot_threshold)),
        ("eager_eot_threshold", str(options.eager_eot_threshold)),
        ("eot_timeout_ms", str(options.eot_timeout_ms)),
    ]
    if options.model == "flux-general-multi":
        query.extend(("language_hint", hint) for hint in options.language_hints if hint)
    if options.encoding is not None and options.sample_rate is not None:
        query.extend(
            (
                ("encoding", options.encoding),
                ("sample_rate", str(options.sample_rate)),
            )
        )
    return urlunsplit((scheme, parsed.netloc, path, urlencode(query), ""))


def build_openai_realtime_url(options: OpenAIVoiceOptions) -> str:
    endpoint = options.endpoint.strip().rstrip("/")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in SUPPORTED_ENDPOINT_SCHEMES or not parsed.netloc:
        raise VoiceError("OpenAI endpoint must be an absolute ws:// or wss:// URL")
    scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme, parsed.scheme)
    base_path = parsed.path.rstrip("/")
    path = base_path if base_path.endswith("/v1/realtime") else f"{base_path}/v1/realtime"
    return urlunsplit(
        (
            scheme,
            parsed.netloc,
            path,
            urlencode({"model": OPENAI_REALTIME_MODEL}),
            "",
        )
    )


class FluxConnection(Protocol):
    async def send_audio(self, audio: bytes) -> None: ...

    async def close_stream(self) -> None: ...

    async def close(self) -> None: ...

    def messages(self) -> AsyncIterator[dict[str, Any]]: ...


class FluxGateway(Protocol):
    async def connect(
        self,
        api_key: str,
        options: VoiceOptions,
        keyterms: tuple[str, ...],
    ) -> FluxConnection: ...


class OpenAIVoiceGateway(Protocol):
    async def connect(
        self,
        api_key: str,
        options: OpenAIVoiceOptions,
        keyterms: tuple[str, ...],
    ) -> FluxConnection: ...


class DeepgramFluxConnection:
    def __init__(self, socket: ClientConnection) -> None:
        self.socket = socket

    async def configure(self, options: VoiceOptions, keyterms: tuple[str, ...]) -> None:
        await self.socket.send(
            json.dumps(
                {
                    "type": "Configure",
                    "thresholds": {
                        "eager_eot_threshold": options.eager_eot_threshold,
                        "eot_threshold": options.eot_threshold,
                        "eot_timeout_ms": options.eot_timeout_ms,
                    },
                    "keyterms": list(keyterms[:MAX_KEYTERMS]),
                }
            )
        )

    async def send_audio(self, audio: bytes) -> None:
        await self.socket.send(audio)

    async def close_stream(self) -> None:
        await self.socket.send(json.dumps({"type": "CloseStream"}))

    async def close(self) -> None:
        await self.socket.close()

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        async for raw in self.socket:
            if not isinstance(raw, str):
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload


class DeepgramFluxGateway:
    async def connect(
        self,
        api_key: str,
        options: VoiceOptions,
        keyterms: tuple[str, ...],
    ) -> FluxConnection:
        try:
            socket = await connect(
                build_flux_url(options),
                additional_headers={"Authorization": f"Token {api_key}"},
                open_timeout=10,
                close_timeout=5,
                max_size=2**20,
            )
        except Exception as error:
            raise VoiceError(f"Could not connect to Deepgram Flux: {error}") from error
        connection = DeepgramFluxConnection(socket)
        await connection.configure(options, keyterms)
        return connection


class OpenAIRealtimeTranscriptionConnection:
    def __init__(self, socket: ClientConnection, scorer: CompletionScorer | None = None) -> None:
        self.socket = socket
        self.transcripts: dict[str, str] = {}
        self.turns: dict[str, int] = {}
        self.closing = False
        self.finish_requested = False
        self.completed_turn = False
        self.commit_in_flight = False
        self.force_commit_requested = False
        self.speech_started = False
        self.last_speech_at: float | None = None
        self.silence_duration_seconds = DEFAULT_TURN_SILENCE_MS / 1_000
        self.speech_rms_threshold = 350
        self.endpoint_task: asyncio.Task[None] | None = None
        self.scorer = scorer
        self.turn_detector: OpenAITurnDetector | None = None
        self.semantic_silence_seconds = 0.6
        self.transcript_stability_seconds = 0.15
        self.candidate_pending = False
        self.current_item_id: str | None = None
        self.pending_item_id: str | None = None
        self.pending_completion_probability: float | None = None
        self.current_completion_probability: float | None = None
        self.completed_items: deque[str] = deque(maxlen=100)
        self.next_turn_index = 0
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()

    async def configure(
        self,
        options: OpenAIVoiceOptions,
        keyterms: tuple[str, ...],
    ) -> None:
        self.silence_duration_seconds = options.silence_duration_ms / 1_000
        self.speech_rms_threshold = options.speech_rms_threshold
        self.semantic_silence_seconds = options.semantic_silence_ms / 1_000
        self.turn_detector = None
        if options.semantic_eot_enabled and self.scorer is not None:
            self.turn_detector = OpenAITurnDetector(
                self.scorer,
                threshold=options.eot_threshold,
                inference_timeout=options.eot_inference_timeout_ms / 1_000,
            )
        transcription: dict[str, Any] = {"model": options.model, "delay": "low"}
        selected = [
            term.strip()
            for term in keyterms
            if term.strip()
            and len(term.strip()) <= 100
            and not any(character in term for character in ("<", ">", "\r", "\n"))
        ][:MAX_KEYTERMS]
        if selected:
            transcription["keywords"] = selected
        await self.socket.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "type": "realtime",
                        "model": OPENAI_REALTIME_MODEL,
                        "output_modalities": ["text"],
                        "audio": {
                            "input": {
                                "format": {
                                    "type": "audio/pcm",
                                    "rate": options.sample_rate,
                                },
                                "transcription": transcription,
                                "turn_detection": None,
                            }
                        },
                    },
                }
            )
        )
        try:
            async with asyncio.timeout(10):
                while True:
                    raw = await self.socket.recv()
                    if not isinstance(raw, str):
                        continue
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    if payload.get("type") == "session.updated":
                        return
                    if payload.get("type") == "error":
                        error = payload.get("error")
                        description = error.get("message") if isinstance(error, dict) else None
                        code = error.get("code") if isinstance(error, dict) else None
                        detail = (
                            description
                            if isinstance(description, str)
                            else "OpenAI rejected the transcription session."
                        )
                        if isinstance(code, str) and code:
                            detail = f"OpenAI transcription error ({code}): {detail}"
                        raise VoiceError(detail)
        except TimeoutError as error:
            raise VoiceError(
                "OpenAI did not confirm the transcription session within 10 seconds."
            ) from error
        except ConnectionClosed as error:
            raise VoiceError(
                f"OpenAI closed the transcription session during setup: {error}"
            ) from error

    async def send_audio(self, audio: bytes) -> None:
        if self.closing:
            raise VoiceError("OpenAI voice connection is closed")
        if self.finish_requested:
            return
        # Observe resumed speech before a potentially backpressured socket write.
        if self._pcm16_rms(audio) >= self.speech_rms_threshold:
            self._note_speech_activity()
        try:
            await self.socket.send(
                json.dumps(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(audio).decode("ascii"),
                    }
                )
            )
        except ConnectionClosed as error:
            raise VoiceError(f"OpenAI voice connection closed: {error}") from error

    async def close_stream(self) -> None:
        self.finish_requested = True
        if self.completed_turn:
            self._cancel_endpoint_task()
            self.closing = True
            await self.socket.close()
            return
        if self.commit_in_flight:
            return
        await self.finish_turn()

    async def finish_turn(self) -> None:
        """Commit an explicit turn without ending a future persistent mic session."""
        if self.commit_in_flight:
            if self.speech_started:
                self.force_commit_requested = True
            return
        self._cancel_endpoint_task()
        await self._commit_audio_buffer()

    async def close(self) -> None:
        task = self.endpoint_task
        self._cancel_endpoint_task()
        self.closing = True
        await self.socket.close()
        if task is not None and task is not asyncio.current_task():
            await asyncio.gather(task, return_exceptions=True)

    def add_assistant_context(self, text: str) -> None:
        """The future persistent-session coordinator can supply the spoken answer."""
        if self.turn_detector is not None:
            self.turn_detector.add_context("assistant", text)

    @staticmethod
    def _pcm16_rms(audio: bytes) -> float:
        usable_bytes = len(audio) - (len(audio) % 2)
        if usable_bytes == 0:
            return 0
        samples = memoryview(audio)[:usable_bytes].cast("h")
        mean_square = sum(sample * sample for sample in samples) / len(samples)
        return float(mean_square**0.5)

    def _note_speech_activity(self) -> None:
        if self.closing or self.finish_requested:
            return
        if self.turn_detector is not None:
            if self.candidate_pending:
                self._emit_turn_event("TurnResumed")
            elif not self.speech_started:
                self._emit_turn_event("StartOfTurn")
            self.turn_detector.note_speech()
        self.candidate_pending = False
        self.speech_started = True
        self.completed_turn = False
        self.last_speech_at = asyncio.get_running_loop().time()
        if self.endpoint_task is None or self.endpoint_task.done():
            self.endpoint_task = asyncio.create_task(self._commit_after_silence())

    async def _commit_after_silence(self) -> None:
        try:
            while self.speech_started and self.last_speech_at is not None:
                if self.closing or self.finish_requested:
                    return
                # Speech for the next turn is still tracked while a final is pending.
                if self.commit_in_flight:
                    await asyncio.sleep(0.05)
                    continue
                if self.force_commit_requested:
                    await self._commit_audio_buffer()
                    return
                now = asyncio.get_running_loop().time()
                speech_at = self.last_speech_at
                elapsed = now - speech_at
                remaining = self.silence_duration_seconds - elapsed
                if remaining <= 0:
                    await self._commit_audio_buffer()
                    return
                detector = self.turn_detector
                if (
                    detector is not None
                    and detector.transcript.strip()
                    and elapsed >= self.semantic_silence_seconds
                    and now - detector.transcript_updated_at >= self.transcript_stability_seconds
                ):
                    if not self.candidate_pending:
                        self.candidate_pending = True
                        self._emit_turn_event("EagerEndOfTurn")
                    # Model loading/inference cannot push us past the silence deadline.
                    try:
                        async with asyncio.timeout(remaining):
                            decision = await detector.evaluate_completion()
                    except TimeoutError:
                        continue
                    if (
                        self.last_speech_at == speech_at
                        and (decision.generation, decision.revision)
                        == (detector.generation, detector.revision)
                        and decision.complete
                    ):
                        self.current_completion_probability = decision.probability
                        await self._commit_audio_buffer()
                        return
                # Polling also notices transcript revisions without restarting silence.
                await asyncio.sleep(min(0.05, remaining))
        except asyncio.CancelledError:
            return
        except Exception as error:
            await self._fail(f"OpenAI turn detection failed ({type(error).__name__}).")
        finally:
            if self.endpoint_task is asyncio.current_task():
                self.endpoint_task = None
                # Audio can arrive while the commit's socket write is awaiting I/O.
                # Do not strand that next turn when this timer finishes.
                if self.speech_started and not self.closing and not self.finish_requested:
                    self.endpoint_task = asyncio.create_task(self._commit_after_silence())

    async def _commit_audio_buffer(self) -> None:
        if self.closing or self.commit_in_flight or self.completed_turn:
            return
        self.commit_in_flight = True
        self.force_commit_requested = False
        self.pending_item_id = self.current_item_id
        self.pending_completion_probability = self.current_completion_probability
        self.current_item_id = None
        self.current_completion_probability = None
        self.speech_started = False
        self.last_speech_at = None
        self.candidate_pending = False
        if self.turn_detector is not None:
            self.turn_detector.reset_turn()
        try:
            await self.socket.send(json.dumps({"type": "input_audio_buffer.commit"}))
        except asyncio.CancelledError:
            self.commit_in_flight = False
            raise
        except ConnectionClosed as error:
            self.commit_in_flight = False
            raise VoiceError(f"OpenAI voice connection closed: {error}") from error

    def _cancel_endpoint_task(self) -> None:
        task = self.endpoint_task
        self.endpoint_task = None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    def _turn_index(self, item_id: str) -> int:
        if item_id not in self.turns:
            self.turns[item_id] = self.next_turn_index
            self.next_turn_index += 1
        return self.turns[item_id]

    def _emit_turn_event(self, event: str) -> None:
        self.incoming.put_nowait(
            json.dumps(
                {
                    "type": "TurnInfo",
                    "event": event,
                    "turn_index": self.turns.get(self.current_item_id or "", self.next_turn_index),
                    "transcript": self.turn_detector.transcript if self.turn_detector else "",
                    "end_of_turn_confidence": None,
                    "languages": [],
                }
            )
        )

    async def _fail(self, description: str) -> None:
        self.closing = True
        self._cancel_endpoint_task()
        self.incoming.put_nowait(json.dumps({"type": "error", "error": {"message": description}}))
        with contextlib.suppress(Exception):
            await self.socket.close()

    async def _received_messages(self) -> AsyncIterator[str]:
        async def read_socket() -> None:
            try:
                async for raw in self.socket:
                    if isinstance(raw, str):
                        self.incoming.put_nowait(raw)
                if not self.closing and getattr(self.socket, "close_code", None) is not None:
                    await self._fail(
                        "OpenAI voice connection closed before the stream was stopped."
                    )
            except ConnectionClosed as error:
                if not self.closing:
                    await self._fail(f"OpenAI voice connection closed: {error}")
            except Exception as error:
                if not self.closing:
                    await self._fail(f"OpenAI voice receiver failed ({type(error).__name__}).")
            finally:
                self.incoming.put_nowait(None)

        reader = asyncio.create_task(read_socket())
        try:
            while (raw := await self.incoming.get()) is not None:
                yield raw
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        try:
            async for raw in self._received_messages():
                if not isinstance(raw, str):
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                message_type = payload.get("type")
                if message_type == "TurnInfo":
                    yield payload
                    continue
                if message_type == "error":
                    error = payload.get("error")
                    description = error.get("message") if isinstance(error, dict) else None
                    code = error.get("code") if isinstance(error, dict) else None
                    detail = (
                        description
                        if isinstance(description, str)
                        else "OpenAI live transcription returned an error."
                    )
                    if isinstance(code, str) and code:
                        detail = f"OpenAI transcription error ({code}): {detail}"
                    yield {"type": "Error", "description": detail}
                    return
                if message_type == "input_audio_buffer.committed":
                    committed_id = payload.get("item_id")
                    if (
                        self.commit_in_flight
                        and isinstance(committed_id, str)
                        and committed_id not in self.completed_items
                        and self.pending_item_id in {None, committed_id}
                    ):
                        self.pending_item_id = committed_id
                    continue
                if message_type not in {
                    "conversation.item.input_audio_transcription.delta",
                    "conversation.item.input_audio_transcription.completed",
                }:
                    continue
                item_id = payload.get("item_id")
                if not isinstance(item_id, str):
                    item_id = f"turn-{len(self.turns)}"
                if item_id in self.completed_items:
                    continue
                if message_type.endswith(".delta"):
                    delta = payload.get("delta")
                    if not isinstance(delta, str):
                        continue
                    # Transcription may arrive after the microphone has gone
                    # quiet. Only incoming speech audio resets the silence clock.
                    transcript = self.transcripts.get(item_id, "") + delta
                    self.transcripts[item_id] = transcript
                    pending = self.commit_in_flight and (
                        item_id == self.pending_item_id
                        or (self.pending_item_id is None and not self.speech_started)
                    )
                    if not pending:
                        self.current_item_id = item_id
                        if self.turn_detector is not None:
                            self.turn_detector.update_transcript(transcript)
                    event = "Update"
                    confidence = None
                else:
                    completed_transcript = payload.get("transcript")
                    transcript = (
                        completed_transcript
                        if isinstance(completed_transcript, str)
                        else self.transcripts.get(item_id, "")
                    )
                    self.transcripts[item_id] = transcript
                    pending = self.commit_in_flight and (
                        self.pending_item_id is None or self.pending_item_id == item_id
                    )
                    confidence = self.pending_completion_probability if pending else None
                    if pending:
                        self.commit_in_flight = False
                        self.pending_item_id = None
                        self.pending_completion_probability = None
                    if self.current_item_id == item_id:
                        self.current_item_id = None
                        self.speech_started = False
                        self.last_speech_at = None
                        if self.turn_detector is not None:
                            self.turn_detector.reset_turn()
                    self.completed_turn = not self.speech_started and self.current_item_id is None
                    if self.completed_turn:
                        self._cancel_endpoint_task()
                    if self.turn_detector is not None:
                        self.turn_detector.add_context("user", transcript)
                    if len(self.completed_items) == self.completed_items.maxlen:
                        oldest = self.completed_items[0]
                        self.transcripts.pop(oldest, None)
                        self.turns.pop(oldest, None)
                    self.completed_items.append(item_id)
                    event = "EndOfTurn"
                yield {
                    "type": "TurnInfo",
                    "event": event,
                    "turn_index": self._turn_index(item_id),
                    "transcript": transcript,
                    "end_of_turn_confidence": confidence,
                    "languages": [],
                }
                if event == "EndOfTurn" and self.finish_requested:
                    self.closing = True
                    await self.socket.close()
                    return
        except ConnectionClosed as error:
            if not self.closing:
                yield {
                    "type": "Error",
                    "description": f"OpenAI voice connection closed: {error}",
                }


class OpenAIRealtimeTranscriptionGateway:
    def __init__(self, scorer: LocalEOTScorer | None = None) -> None:
        self.scorer = scorer or LocalEOTScorer()

    def close(self) -> None:
        self.scorer.close()

    async def connect(
        self,
        api_key: str,
        options: OpenAIVoiceOptions,
        keyterms: tuple[str, ...],
    ) -> FluxConnection:
        try:
            socket = await connect(
                build_openai_realtime_url(options),
                additional_headers={"Authorization": f"Bearer {api_key}"},
                open_timeout=10,
                close_timeout=5,
                max_size=2**20,
            )
        except Exception as error:
            raise VoiceError(f"Could not connect to OpenAI voice transcription: {error}") from error
        if options.semantic_eot_enabled:
            self.scorer.warmup()
        connection = OpenAIRealtimeTranscriptionConnection(socket, self.scorer)
        try:
            await connection.configure(options, keyterms)
        except BaseException:
            await connection.close()
            raise
        return connection
