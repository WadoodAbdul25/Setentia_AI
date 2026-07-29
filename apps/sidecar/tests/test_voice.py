from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sentia_sidecar.app import create_app
from sentia_sidecar.repository import RepositoryFile, RepositoryManifest
from sentia_sidecar.settings import Settings
from sentia_sidecar.voice import (
    FluxConnection,
    RepositoryTranscriptCorrector,
    VoiceOptions,
    build_flux_url,
    build_vocabulary,
    select_keyterms,
)


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
            json={"apiKey": "deepgram-test-key-long-enough"},
        )
        assert connected.json()["connected"] is True

        with client.websocket_connect(
            "/api/v1/voice/transcribe"
            f"?sessionId=voice-1&workspacePath={tmp_path}"
            "&activeFile=AuthMiddleware.ts&model=flux-general-en",
            headers=auth_headers,
        ) as websocket:
            assert websocket.receive_json()["state"] == "connecting"
            assert websocket.receive_json()["state"] == "listening"
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
