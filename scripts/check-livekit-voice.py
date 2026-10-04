"""Paid provider smoke test using dummy content, no repository data or speakers.

Run from the repository root: .venv/bin/python scripts/check-livekit-voice.py
Loads apps/sidecar/.env locally. Prints only stage names, timings, and audio size.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import structlog
from sentia_sidecar.livekit_voice import LiveKitVoiceTurn
from sentia_sidecar.protocol import RepositoryAnswer, TokenUsage, WorkingMode
from sentia_sidecar.settings import Settings
from sentia_sidecar.voice import (
    OpenAIRealtimeTranscriptionGateway,
    OpenAIVoiceOptions,
    RepositoryTranscriptCorrector,
)


async def check() -> bool:
    # SDK exception logs may include provider responses. Never expose those or keys.
    logging.disable(logging.CRITICAL)
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
    settings = Settings(
        token="local-smoke-test-token-not-used-for-network",
        _env_file=(".env", "apps/sidecar/.env"),
    )
    if not settings.openai_api_key or not settings.cerebras_api_key:
        print(json.dumps({"ok": False, "stage": "credentials", "error": "Required keys missing"}))
        return False
    stage = "openai_transcription_connect"
    turn: LiveKitVoiceTurn | None = None
    started = time.monotonic()
    connection = None
    response: asyncio.Task[None] | None = None
    try:
        connection = await asyncio.wait_for(
            OpenAIRealtimeTranscriptionGateway().connect(
                settings.openai_api_key.get_secret_value(), OpenAIVoiceOptions(), ()
            ),
            20,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "stage": stage,
                    "elapsedMs": round((time.monotonic() - started) * 1000),
                }
            ),
            flush=True,
        )

        async def dummy_analysis(question: str, provider: str) -> RepositoryAnswer:
            return RepositoryAnswer(
                answer=(
                    "Sentia helps explain a software project. "
                    "It displays an answer and supporting evidence."
                ),
                spoken_answer="Sentia explains software projects and shows evidence.",
                recommended_mode=WorkingMode.BRAINSTORM,
                mode_reason="Synthetic smoke test, no repository access.",
                model="fixture",
                files_scanned=0,
                files_read=0,
                usage=TokenUsage(input_tokens=0, output_tokens=0),
            )

        stage = "cerebras_narration_openai_tts"
        turn = LiveKitVoiceTurn(
            session_id="smoke-test",
            connection=connection,
            corrector=RepositoryTranscriptCorrector(()),
            openai_key=settings.openai_api_key.get_secret_value(),
            cerebras_key=settings.cerebras_api_key.get_secret_value(),
            cerebras_url=settings.cerebras_url,
            cerebras_model=settings.cerebras_model,
            tts_model="gpt-4o-mini-tts",
            tts_voice="marin",
            tts_url="https://api.openai.com/v1",
            analyze=dummy_analysis,
        )
        await turn.start()
        # Synthetic accepted question: this checks auth/configuration for STT,
        # not actual speech recognition. No mic is opened and no audio is played.
        turn.finished_recording = True
        turn.session.input.set_audio_enabled(False)
        started = time.monotonic()
        response = asyncio.create_task(turn.answer("smoke-answer", "What does Sentia do?", "codex"))
        audio_bytes = 0
        first_audio_ms = None
        async with asyncio.timeout(90):
            while True:
                event = await turn.outgoing.get()
                if isinstance(event, bytes):
                    audio_bytes += len(event)
                    if first_audio_ms is None:
                        first_audio_ms = round((time.monotonic() - started) * 1000)
                elif event["type"] == "voice.audio.flush":
                    turn.audio_output.acknowledge(event["segmentId"])
                elif event["type"] == "voice.error":
                    raise RuntimeError("Provider failure")
                elif event["type"] == "voice.complete":
                    break
        await response
        print(
            json.dumps(
                {
                    "ok": audio_bytes > 0,
                    "stage": stage,
                    "firstAudioMs": first_audio_ms,
                    "pcmBytes": audio_bytes,
                }
            ),
            flush=True,
        )
        return audio_bytes > 0
    except Exception as error:
        print(
            json.dumps({"ok": False, "stage": stage, "errorType": type(error).__name__}), flush=True
        )
        return False
    finally:
        if response is not None:
            response.cancel()
            await asyncio.gather(response, return_exceptions=True)
        if turn is not None:
            await turn.close()
        elif connection is not None:
            await connection.close()


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(check()) else 1)
