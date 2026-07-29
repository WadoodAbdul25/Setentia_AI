from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit

from websockets.asyncio.client import ClientConnection, connect

from sentia_sidecar.repository import RepositoryManifest

FLUX_MODELS = {"flux-general-en", "flux-general-multi"}
FLUX_EVENTS = {"StartOfTurn", "Update", "EagerEndOfTurn", "TurnResumed", "EndOfTurn"}
SUPPORTED_ENDPOINT_SCHEMES = {"ws", "wss", "http", "https"}
MAX_KEYTERMS = 100
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
    eot_timeout_ms: int = 4_000
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
