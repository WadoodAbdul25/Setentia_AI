from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sentia_sidecar.app import create_app
from sentia_sidecar.repository import RepositoryFile, RepositoryManifest
from sentia_sidecar.settings import Settings
from sentia_sidecar.voice import (
    FluxConnection,
    OpenAIRealtimeTranscriptionConnection,
    OpenAIVoiceOptions,
    RepositoryTranscriptCorrector,
    VoiceError,
    VoiceOptions,
    build_flux_url,
    build_openai_realtime_url,
    build_vocabulary,
    select_keyterms,
)


class FakeOpenAISocket:
    def __init__(self, messages: list[str] | None = None) -> None:
        self.sent: list[str] = []
        self.messages = messages or []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True

    async def recv(self) -> str:
        return self.messages.pop(0)

    def __aiter__(self) -> AsyncIterator[str]:
        async def iterate() -> AsyncIterator[str]:
            for message in self.messages:
                yield message

        return iterate()


async def test_openai_realtime_transcription_uses_pcm_and_preserves_deltas() -> None:
    socket = FakeOpenAISocket(
        [
            json.dumps({"type": "session.created"}),
            json.dumps({"type": "session.updated"}),
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.delta",
                    "item_id": "item-1",
                    "delta": "open the ",
                }
            ),
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": "item-1",
                    "transcript": "open the AuthMiddleware",
                }
            ),
        ]
    )
    connection = OpenAIRealtimeTranscriptionConnection(socket)  # type: ignore[arg-type]

    await connection.configure(
        OpenAIVoiceOptions(),
        ("AuthMiddleware", "Generic<T>"),
    )
    await connection.send_audio(b"pcm")
    await connection.close_stream()
    messages = [message async for message in connection.messages()]

    session = json.loads(socket.sent[0])
    assert session["session"]["type"] == "realtime"
    assert session["session"]["model"] == "gpt-realtime"
    assert session["session"]["output_modalities"] == ["text"]
    assert session["session"]["audio"]["input"]["transcription"]["model"] == ("gpt-live-transcribe")
    assert session["session"]["audio"]["input"]["transcription"]["delay"] == "low"
    assert session["session"]["audio"]["input"]["format"]["rate"] == 24_000
    assert session["session"]["audio"]["input"]["transcription"]["keywords"] == ["AuthMiddleware"]
    assert session["session"]["audio"]["input"]["turn_detection"] is None
    audio = json.loads(socket.sent[1])
    assert audio["type"] == "input_audio_buffer.append"
    commit = json.loads(socket.sent[2])
    assert commit["type"] == "input_audio_buffer.commit"
    assert messages[-1]["event"] == "EndOfTurn"
    assert messages[-1]["transcript"] == "open the AuthMiddleware"
    assert socket.closed is True


async def test_openai_realtime_transcription_uses_bounded_keywords_without_prompt() -> None:
    socket = FakeOpenAISocket([json.dumps({"type": "session.updated"})])
    connection = OpenAIRealtimeTranscriptionConnection(socket)  # type: ignore[arg-type]
    keyterms = tuple(f"RepositorySymbol{index:03d}" + "x" * 70 for index in range(100))

    await connection.configure(OpenAIVoiceOptions(), keyterms)

    session = json.loads(socket.sent[0])
    transcription = session["session"]["audio"]["input"]["transcription"]
    assert len(transcription["keywords"]) == 100
    assert "prompt" not in transcription


async def test_openai_realtime_transcription_preserves_provider_errors() -> None:
    socket = FakeOpenAISocket(
        [
            json.dumps(
                {
                    "type": "error",
                    "error": {
                        "code": "invalid_request_error",
                        "message": "The transcription session is invalid.",
                    },
                }
            )
        ]
    )
    connection = OpenAIRealtimeTranscriptionConnection(socket)  # type: ignore[arg-type]

    messages = [message async for message in connection.messages()]

    assert messages == [
        {
            "type": "Error",
            "description": (
                "OpenAI transcription error (invalid_request_error): "
                "The transcription session is invalid."
            ),
        }
    ]


async def test_openai_realtime_setup_surfaces_rejection_before_audio_starts() -> None:
    socket = FakeOpenAISocket(
        [
            json.dumps(
                {
                    "type": "error",
                    "error": {
                        "code": "model_not_found",
                        "message": "The requested model is unavailable.",
                    },
                }
            )
        ]
    )
    connection = OpenAIRealtimeTranscriptionConnection(socket)  # type: ignore[arg-type]

    with pytest.raises(VoiceError, match="model_not_found"):
        await connection.configure(OpenAIVoiceOptions(), ())


def fixture_repository(tmp_path: Path) -> RepositoryManifest:
    return RepositoryManifest(
        root=tmp_path,
        prompt="fixture",
        files={
            "src/AuthMiddleware.ts": RepositoryFile(
                path="src/AuthMiddleware.ts",
                size=120,
                priority=1,
                role="source",
                structure="imports=[none]; top-level symbols=[class:AuthMiddleware@4]",
            ),
            "src/SessionStore.ts": RepositoryFile(
                path="src/SessionStore.ts",
                size=90,
                priority=2,
                role="source",
                structure="imports=[none]; top-level symbols=[class:SessionStore@3]",
            ),
        },
        description_paths=(),
        files_scanned=2,
    )


def test_flux_url_uses_v2_and_container_audio_autodetection() -> None:
    url = build_flux_url(
        VoiceOptions(
            model="flux-general-multi",
            endpoint="https://api.eu.deepgram.com",
            language_hints=("en", "es"),
        )
    )

    assert url.startswith("wss://api.eu.deepgram.com/v2/listen?")
    assert "model=flux-general-multi" in url
    assert "language_hint=en" in url
    assert "language_hint=es" in url
    assert "encoding=" not in url
    assert "sample_rate=" not in url


def test_flux_url_declares_raw_linear16_audio() -> None:
    url = build_flux_url(VoiceOptions(encoding="linear16", sample_rate=16_000))

    assert "encoding=linear16" in url
    assert "sample_rate=16000" in url


def test_openai_url_opens_gpt_realtime_session() -> None:
    options = OpenAIVoiceOptions(endpoint="https://api.openai.com")

    url = build_openai_realtime_url(options)

    assert url == "wss://api.openai.com/v1/realtime?model=gpt-realtime"
    assert options.model == "gpt-live-transcribe"


def test_repository_vocabulary_corrects_high_confidence_active_symbol(tmp_path: Path) -> None:
    vocabulary = build_vocabulary(fixture_repository(tmp_path), "src/AuthMiddleware.ts")
    corrected = RepositoryTranscriptCorrector(vocabulary).correct("open the author middleware")

    assert corrected.text == "open the AuthMiddleware"
    assert corrected.corrections[0].replacement == "AuthMiddleware"
    assert corrected.corrections[0].applied is True
    assert select_keyterms(vocabulary)[0] in {"AuthMiddleware", "AuthMiddleware.ts"}


def test_repository_vocabulary_preserves_ordinary_language_without_context(tmp_path: Path) -> None:
    vocabulary = build_vocabulary(fixture_repository(tmp_path))
    corrected = RepositoryTranscriptCorrector(vocabulary).correct("the session store")

    assert corrected.text == "the session store"
    assert any(candidate.replacement == "SessionStore" for candidate in corrected.corrections)
    assert all(candidate.applied is False for candidate in corrected.corrections)


class FakeFluxConnection:
    def __init__(self) -> None:
        self.audio_received = asyncio.Event()
        self.closed = False

    async def send_audio(self, audio: bytes) -> None:
        assert audio == b"webm-audio"
        self.audio_received.set()

    async def close_stream(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        await self.audio_received.wait()
        yield {
            "type": "TurnInfo",
            "event": "EndOfTurn",
            "turn_index": 0,
            "transcript": "open the author middleware",
            "end_of_turn_confidence": 0.92,
        }


class FakeFluxGateway:
    def __init__(self) -> None:
        self.connection = FakeFluxConnection()
        self.options: VoiceOptions | None = None
        self.keyterms: tuple[str, ...] = ()

    async def connect(
        self,
        api_key: str,
        options: VoiceOptions,
        keyterms: tuple[str, ...],
    ) -> FluxConnection:
        assert api_key == "deepgram-test-key-long-enough"  # pragma: allowlist secret
        self.options = options
        self.keyterms = keyterms
        return self.connection


class FakeOpenAIGateway:
    def __init__(self) -> None:
        self.connection = FakeFluxConnection()
        self.options: OpenAIVoiceOptions | None = None
        self.keyterms: tuple[str, ...] = ()

    async def connect(
        self,
        api_key: str,
        options: OpenAIVoiceOptions,
        keyterms: tuple[str, ...],
    ) -> FluxConnection:
        assert api_key == "openai-test-key-long-enough"  # pragma: allowlist secret
        self.options = options
        self.keyterms = keyterms
        return self.connection


def test_voice_websocket_streams_corrected_flux_turn(
    settings: Settings,
    auth_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    source = tmp_path / "AuthMiddleware.ts"
    source.write_text("export class AuthMiddleware {}\n", encoding="utf-8")
    gateway = FakeFluxGateway()
    with TestClient(create_app(settings, flux_gateway=gateway)) as client:
        connected = client.put(
            "/api/v1/auth/deepgram",
            headers=auth_headers,
            json={"apiKey": "deepgram-test-key-long-enough"},  # pragma: allowlist secret
        )
        assert connected.json()["connected"] is True

        with client.websocket_connect(
            "/api/v1/voice/deepgram/transcribe"
            f"?sessionId=voice-1&workspacePath={tmp_path}"
            "&activeFile=AuthMiddleware.ts&model=flux-general-en",
            headers=auth_headers,
        ) as websocket:
            assert websocket.receive_json()["state"] == "connecting"
            listening = websocket.receive_json()
            assert listening.get("state") == "listening", listening
            websocket.send_bytes(b"webm-audio")
            transcript = websocket.receive_json()
            assert transcript["type"] == "voice.transcript"
            assert transcript["payload"]["isFinal"] is True
            assert transcript["payload"]["correctedTranscript"] == "open the AuthMiddleware"
            assert websocket.receive_json()["state"] == "closed"

    assert gateway.options is not None
    assert gateway.options.model == "flux-general-en"
    assert "AuthMiddleware" in gateway.keyterms
    assert gateway.connection.closed is True


def test_openai_voice_route_does_not_enter_deepgram_pipeline(
    settings: Settings,
    auth_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    (tmp_path / "sample.py").write_text("def sample():\n    return True\n", encoding="utf-8")
    deepgram_gateway = FakeFluxGateway()
    openai_gateway = FakeOpenAIGateway()
    with TestClient(
        create_app(
            settings,
            flux_gateway=deepgram_gateway,
            openai_voice_gateway=openai_gateway,
        )
    ) as client:
        connected = client.put(
            "/api/v1/auth/openai-voice",
            headers=auth_headers,
            json={"apiKey": "openai-test-key-long-enough"},  # pragma: allowlist secret
        )
        assert connected.json()["connected"] is True

        with client.websocket_connect(
            "/api/v1/voice/openai/transcribe"
            f"?sessionId=voice-openai&workspacePath={tmp_path}"
            "&model=gpt-live-transcribe&endpoint=wss://api.openai.com"
            "&encoding=linear16&sampleRate=24000",
            headers=auth_headers,
        ) as websocket:
            assert websocket.receive_json()["state"] == "connecting"
            listening = websocket.receive_json()
            assert listening.get("state") == "listening", listening
            websocket.send_text(json.dumps({"type": "voice.cancel"}))
            assert websocket.receive_json()["state"] == "closed"

    assert openai_gateway.options is not None
    assert openai_gateway.options.model == "gpt-live-transcribe"
    assert deepgram_gateway.options is None
    assert openai_gateway.connection.closed is True
