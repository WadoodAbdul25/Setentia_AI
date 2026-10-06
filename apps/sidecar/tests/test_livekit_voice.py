from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from livekit import rtc
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, llm, stt, tts
from pydantic import SecretStr
from sentia_sidecar.answer_stream import AnswerActivity, AnswerStreamEvent
from sentia_sidecar.app import create_app
from sentia_sidecar.livekit_voice import (
    DesktopAudioInput,
    DesktopAudioOutput,
    LiveKitVoiceTurn,
    LiveTranscriptionSTT,
    SentiaNarrationAgent,
    pronunciation_text,
)
from sentia_sidecar.protocol import AgentProvider, RepositoryAnswer, TokenUsage, WorkingMode
from sentia_sidecar.repository_intelligence import RepositoryIntelligenceRouter
from sentia_sidecar.settings import Settings
from sentia_sidecar.voice import OpenAIVoiceOptions, RepositoryTranscriptCorrector, VoiceError
from starlette.websockets import WebSocketDisconnect


class FakeConnection:
    def __init__(self) -> None:
        self.audio: list[bytes] = []
        self.results: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.closed = False

    async def send_audio(self, audio: bytes) -> None:
        self.audio.append(audio)

    async def close_stream(self) -> None:
        await self.results.put(
            {"type": "TurnInfo", "event": "EndOfTurn", "transcript": "How does Sentia work?"}
        )

    async def close(self) -> None:
        self.closed = True

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        while not self.closed:
            result = await self.results.get()
            yield result
            if result.get("event") == "EndOfTurn":
                return


class FakeLLM(llm.LLM):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        self.options = kwargs
        self.contexts: list[llm.ChatContext] = []
        self.closed = False

    def chat(self, *, chat_ctx: llm.ChatContext, **kwargs: Any) -> llm.LLMStream:
        assert not kwargs.get("tools")
        self.contexts.append(chat_ctx)
        return FakeLLMStream(
            self, chat_ctx=chat_ctx, tools=[], conn_options=DEFAULT_API_CONNECT_OPTIONS
        )

    async def aclose(self) -> None:
        self.closed = True


class FakeLLMStream(llm.LLMStream):
    async def _run(self) -> None:
        for text in ["Sen", "tia reads ", "the repository. ", "It shows evidence."]:
            self._event_ch.send_nowait(
                llm.ChatChunk(id="fake", delta=llm.ChoiceDelta(role="assistant", content=text))
            )


class FakeTTS(tts.TTS):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False), sample_rate=24_000, num_channels=1
        )
        self.options = kwargs
        self.texts: list[str] = []
        self.closed = False

    def synthesize(self, text: str, **kwargs: Any) -> tts.ChunkedStream:
        self.texts.append(text)
        return FakeAudioStream(tts=self, input_text=text, conn_options=DEFAULT_API_CONNECT_OPTIONS)

    async def aclose(self) -> None:
        self.closed = True


class FakeAudioStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id="fake", sample_rate=24_000, num_channels=1, mime_type="audio/pcm"
        )
        output_emitter.push(b"\x00\x00" * 4800)


def grounded_answer() -> RepositoryAnswer:
    return RepositoryAnswer(
        answer="Sentia reads the repository and shows evidence.",
        spoken_answer="Original fallback explanation.",
        recommended_mode=WorkingMode.BRAINSTORM,
        mode_reason="Question only.",
        model="analysis-model",
        files_scanned=1,
        files_read=1,
        usage=TokenUsage(input_tokens=10, output_tokens=20),
    )


def test_local_dotenv_aliases_keep_credentials_secret(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "OPENAI_API_KEY=fixture-openai-key\nCEREBRAS_API_KEY=fixture-cerebras-key\n"
        "CEREBRAS_URL=https://api.cerebras.ai/v1\n",
        encoding="utf8",
    )
    settings = Settings(token="fixture-token-with-thirty-two-characters", _env_file=dotenv)
    assert settings.openai_api_key is not None
    assert settings.cerebras_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "fixture-openai-key"
    assert settings.cerebras_api_key.get_secret_value() == "fixture-cerebras-key"
    assert settings.cerebras_url == "https://api.cerebras.ai/v1"
    assert "fixture-openai-key" not in repr(settings)
    assert "fixture-cerebras-key" not in repr(settings)


@pytest.mark.parametrize(
    "chunks,expected",
    [
        (["Sen", "tia is here."], "Sen-shia is here."),
        (["Hello Sen", "tia! Next."], "Hello Sen-shia! Next."),
        (["Sentia", " \n", "SENTIA ", "SentiaCode"], "Sen-shia \nSen-shia SentiaCode"),
        (["sent", "ia"], "Sen-shia"),
    ],
)
async def test_pronunciation_across_provider_chunks(chunks: list[str], expected: str) -> None:
    async def source() -> AsyncIterator[str]:
        for chunk in chunks:
            yield chunk

    assert "".join([chunk async for chunk in pronunciation_text(source())]) == expected


async def test_device_ack_is_required_for_segment_completion() -> None:
    emitted: list[Any] = []
    output = DesktopAudioOutput(emitted.append)
    completed: list[Any] = []
    output.on("playback_finished", completed.append)
    await output.capture_frame(rtc.AudioFrame(b"\x00\x00" * 480, 24_000, 1, 480))
    output.flush()
    segment_id = emitted[0]["segmentId"]
    assert not completed
    output.acknowledge("stale-segment")
    assert not completed
    output.acknowledge(segment_id)
    assert len(completed) == 1
    assert completed[0].playback_position == pytest.approx(0.02)
    output.acknowledge(segment_id)
    assert len(completed) == 1


async def test_input_validates_pcm() -> None:
    source = DesktopAudioInput()
    with pytest.raises(VoiceError):
        await source.push(b"odd")
    await source.push(b"\x00\x00")
    assert (await source.__anext__()).sample_rate == 24_000


async def test_stt_adapter_preserves_model_and_final_text() -> None:
    connection = FakeConnection()
    adapter = LiveTranscriptionSTT(connection)
    stream = adapter.stream()
    stream.push_frame(rtc.AudioFrame(b"\x00\x00" * 480, 24_000, 1, 480))
    await asyncio.wait_for(adapter.drain(960), 2)
    await connection.close_stream()
    events = [event async for event in stream]
    assert adapter.model == "gpt-live-transcribe"
    assert events[0].type == stt.SpeechEventType.FINAL_TRANSCRIPT
    assert events[0].alternatives[0].text == "How does Sentia work?"
    assert connection.audio == [b"\x00\x00" * 480]
    await stream.aclose()


async def test_narrator_refuses_to_answer_without_analysis() -> None:
    agent = SentiaNarrationAgent()
    with pytest.raises(VoiceError, match="analysis must complete"):
        await anext(agent.llm_node(llm.ChatContext(), [], {}))


async def test_real_session_runs_grounded_pipeline_and_waits_for_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sentia_sidecar.livekit_voice.openai.LLM", FakeLLM)
    monkeypatch.setattr("sentia_sidecar.livekit_voice.openai.TTS", FakeTTS)
    connection = FakeConnection()
    analyses: list[tuple[str, str]] = []

    async def analyze(question: str, provider: str) -> RepositoryAnswer:
        analyses.append((question, provider))
        return grounded_answer()

    turn = LiveKitVoiceTurn(
        session_id="test-session",
        connection=connection,
        corrector=RepositoryTranscriptCorrector(()),
        openai_key="test-openai-key",
        cerebras_key="test-cerebras-key",
        cerebras_url="https://api.cerebras.ai/v1",
        cerebras_model="gpt-oss-120b",
        tts_model="gpt-4o-mini-tts",
        tts_voice="marin",
        tts_url="https://api.openai.com/v1",
        analyze=analyze,
    )
    try:
        await turn.start()
        await turn.audio_input.push(b"\x00\x00" * 480)
        await asyncio.wait_for(turn.finish_recording(), 3)
        response = asyncio.create_task(turn.answer("request-1", turn.transcript, "codex"))
        seen: list[Any] = []
        while True:
            event = await asyncio.wait_for(turn.outgoing.get(), 5)
            seen.append(event)
            if isinstance(event, dict) and event["type"] == "voice.audio.flush":
                assert not response.done()
                turn.audio_output.acknowledge(event["segmentId"])
            if isinstance(event, dict) and event["type"] == "voice.complete":
                break
        await response
        assert analyses == [("How does Sentia work?", "codex")]
        answer_index = next(
            i for i, x in enumerate(seen) if isinstance(x, dict) and x["type"] == "voice.answer"
        )
        audio_index = next(i for i, x in enumerate(seen) if isinstance(x, bytes))
        assert answer_index < audio_index
        narrator = turn.narrator
        assert isinstance(narrator, FakeLLM)
        assert narrator.options["model"] == "gpt-oss-120b"
        assert narrator.options["extra_headers"] == {"X-Cerebras-3rd-Party-Integration": "livekit"}
        assert len(narrator.contexts) == 1
        data = json.loads(narrator.contexts[0].items[-1].text_content)
        assert data["answer"] == grounded_answer().answer
        synthesizer = turn.synthesizer
        assert isinstance(synthesizer, FakeTTS)
        assert "Sen-shia" in " ".join(synthesizer.texts)
        assert "test-cerebras-key" not in str(seen)
    finally:
        await turn.close()
    assert connection.closed


async def test_checked_passage_speaks_before_repository_generation_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sentia_sidecar.livekit_voice.openai.LLM", FakeLLM)
    monkeypatch.setattr("sentia_sidecar.livekit_voice.openai.TTS", FakeTTS)
    release_final = asyncio.Event()
    first = "Sentia reads repository evidence."
    second = "It shows the supported answer."

    async def analyze(question: str, provider: str) -> RepositoryAnswer:
        raise AssertionError("The non-streaming analysis path must not be used")

    async def analyze_stream(question: str, provider: str) -> Any:
        yield AnswerStreamEvent(
            type="progress", stage="file_selection", text="Searching the files."
        )
        yield AnswerStreamEvent(
            type="activity",
            activity=AnswerActivity(
                id="content_read",
                stage="content_read",
                status="completed",
                message="Read evidence from 1 file.",
                details=["main.py"],
            ),
        )
        yield AnswerStreamEvent(type="delta", text=first)
        yield AnswerStreamEvent(type="passage", text=first)
        await release_final.wait()
        yield AnswerStreamEvent(type="delta", text="\n\n" + second)
        yield AnswerStreamEvent(type="passage", text=second)
        yield AnswerStreamEvent(type="complete", answer=grounded_answer())

    turn = LiveKitVoiceTurn(
        session_id="stream-session",
        connection=FakeConnection(),
        corrector=RepositoryTranscriptCorrector(()),
        openai_key="fixture",
        cerebras_key="fixture",
        cerebras_url="https://api.cerebras.ai/v1",
        cerebras_model="gpt-oss-120b",
        tts_model="gpt-4o-mini-tts",
        tts_voice="marin",
        tts_url="https://api.openai.com/v1",
        analyze=analyze,
        analyze_stream=analyze_stream,
        progress_phrases=False,
    )
    task: asyncio.Task[None] | None = None
    seen: list[Any] = []
    try:
        await turn.start()
        await turn.finish_recording()
        task = asyncio.create_task(turn.answer("stream-request", "Explain", "codex"))
        while True:
            event = await asyncio.wait_for(turn.outgoing.get(), 5)
            seen.append(event)
            if isinstance(event, dict) and event["type"] == "voice.audio.flush":
                if not release_final.is_set():
                    assert not any(
                        isinstance(x, dict) and x["type"] == "voice.answer" for x in seen
                    )
                    assert any(isinstance(x, bytes) for x in seen)
                    assert not task.done()
                    release_final.set()
                turn.audio_output.acknowledge(event["segmentId"])
            if isinstance(event, dict) and event["type"] == "voice.complete":
                break
        await task
        assert any(
            isinstance(event, dict)
            and event.get("type") == "voice.answer.activity"
            and event["requestId"] == "stream-request"
            and event["activity"]["details"] == ["main.py"]
            for event in seen
        )
        narrator = turn.narrator
        assert isinstance(narrator, FakeLLM)
        assert [
            json.loads(context.items[-1].text_content)["answer"] for context in narrator.contexts
        ] == [first, second]
    finally:
        release_final.set()
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await turn.close()


async def test_progress_speech_failure_does_not_abort_written_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sentia_sidecar.livekit_voice.openai.LLM", FakeLLM)
    monkeypatch.setattr("sentia_sidecar.livekit_voice.openai.TTS", FakeTTS)

    async def analyze(question: str, provider: str) -> RepositoryAnswer:
        return grounded_answer()

    async def analyze_stream(question: str, provider: str) -> Any:
        yield AnswerStreamEvent(
            type="progress", stage="file_selection", text="Searching the files."
        )
        await asyncio.sleep(0.05)
        yield AnswerStreamEvent(type="delta", text=grounded_answer().answer)
        yield AnswerStreamEvent(type="complete", answer=grounded_answer())

    turn = LiveKitVoiceTurn(
        session_id="failed-progress",
        connection=FakeConnection(),
        corrector=RepositoryTranscriptCorrector(()),
        openai_key="fixture",
        cerebras_key="fixture",
        cerebras_url="https://api.cerebras.ai/v1",
        cerebras_model="gpt-oss-120b",
        tts_model="gpt-4o-mini-tts",
        tts_voice="marin",
        tts_url="https://api.openai.com/v1",
        analyze=analyze,
        analyze_stream=analyze_stream,
    )

    def fail_speech(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("TTS unavailable")

    monkeypatch.setattr(turn.session, "say", fail_speech)
    try:
        await turn.start()
        await turn.finish_recording()
        await asyncio.wait_for(turn.answer("request", "Explain", "codex"), 3)
        seen = []
        while not turn.outgoing.empty():
            seen.append(turn.outgoing.get_nowait())
        assert turn.speech_failed
        assert any(isinstance(x, dict) and x["type"] == "voice.answer" for x in seen)
        assert any(isinstance(x, dict) and x["type"] == "voice.complete" for x in seen)
        assert not any(isinstance(x, dict) and x["type"] == "voice.error" for x in seen)
    finally:
        await turn.close()


class FakeGateway:
    def __init__(self) -> None:
        self.connection = FakeConnection()
        self.calls = 0
        self.options: OpenAIVoiceOptions | None = None

    async def connect(self, *args: Any) -> FakeConnection:
        self.calls += 1
        self.options = args[1]
        return self.connection


def test_cancel_closes_session_without_analysis(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sentia_sidecar.livekit_voice.openai.LLM", FakeLLM)
    monkeypatch.setattr("sentia_sidecar.livekit_voice.openai.TTS", FakeTTS)
    configured = settings.model_copy(
        update={
            "openai_api_key": SecretStr("test-openai-key"),
            "cerebras_api_key": SecretStr("test-cerebras-key"),
            "openai_eot_threshold": 0.08,
            "openai_eot_min_silence_ms": 750,
            "openai_eot_inference_timeout_ms": 250,
        }
    )
    gateway = FakeGateway()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Cancellation fixture\n", encoding="utf8")
    url = "/api/v1/voice/livekit/session?" + urlencode(
        {"sessionId": "cancel-turn", "workspacePath": str(workspace)}
    )
    with (
        TestClient(create_app(configured, openai_voice_gateway=gateway)) as client,
        client.websocket_connect(
            url, headers={"Authorization": f"Bearer {settings.token.get_secret_value()}"}
        ) as socket,
    ):
        assert socket.receive_json()["state"] == "listening"
        socket.send_json({"type": "voice.cancel"})
    assert gateway.connection.closed
    assert gateway.options is not None
    assert gateway.options.eot_threshold == 0.08
    assert gateway.options.semantic_silence_ms == 750
    assert gateway.options.eot_inference_timeout_ms == 250


class FakeAnalysis:
    provider = AgentProvider.CODEX

    def __init__(self) -> None:
        self.questions: list[str] = []

    async def answer(self, question: str, repository: Any) -> RepositoryAnswer:
        self.questions.append(question)
        return grounded_answer()


def test_websocket_pipeline_authentication_and_missing_credentials(
    settings: Settings, tmp_path: Path
) -> None:
    url = "/api/v1/voice/livekit/session?" + urlencode(
        {"sessionId": "voice-1", "workspacePath": str(tmp_path)}
    )
    gateway = FakeGateway()
    with TestClient(create_app(settings, openai_voice_gateway=gateway)) as client:
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(url):
            pass
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(
                url, headers={"Authorization": f"Bearer {settings.token.get_secret_value()}"}
            ),
        ):
            pass
    assert gateway.calls == 0


def test_websocket_runs_analysis_narration_and_device_ack(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sentia_sidecar.livekit_voice.openai.LLM", FakeLLM)
    monkeypatch.setattr("sentia_sidecar.livekit_voice.openai.TTS", FakeTTS)
    configured = settings.model_copy(
        update={
            "openai_api_key": SecretStr("test-openai-key"),
            "cerebras_api_key": SecretStr("test-cerebras-key"),
        }
    )
    gateway = FakeGateway()
    analysis = FakeAnalysis()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Example\n", encoding="utf8")
    url = "/api/v1/voice/livekit/session?" + urlencode(
        {"sessionId": "voice-1", "workspacePath": str(workspace)}
    )
    app = create_app(
        configured,
        openai_voice_gateway=gateway,
        repository_router=RepositoryIntelligenceRouter([analysis]),
    )
    with (
        TestClient(app) as client,
        client.websocket_connect(
            url, headers={"Authorization": f"Bearer {settings.token.get_secret_value()}"}
        ) as socket,
    ):
        assert socket.receive_json()["state"] == "listening"
        socket.send_bytes(b"\x00\x00" * 480)
        socket.send_json({"type": "voice.stop"})
        while True:
            payload = socket.receive_json()
            if payload["type"] == "voice.transcript" and payload["payload"]["isFinal"]:
                break
        socket.send_json(
            {
                "type": "voice.ask",
                "requestId": "q-1",
                "question": "How does Sentia work?",
                "provider": "codex",
            }
        )
        answered = False
        audio_bytes = 0
        while True:
            message = socket.receive()
            if isinstance(message.get("bytes"), bytes):
                # Stage speech can now play before the repository answer is complete.
                audio_bytes += len(message["bytes"])
                continue
            payload = json.loads(message["text"])
            if payload["type"] == "voice.answer":
                answered = True
                assert payload["requestId"] == "q-1"
                assert payload["payload"]["answer"] == grounded_answer().answer
            elif payload["type"] == "voice.audio.flush":
                socket.send_json(
                    {"type": "voice.playback_finished", "segmentId": payload["segmentId"]}
                )
            elif payload["type"] == "voice.complete":
                assert answered
                break
            assert payload["type"] != "voice.error"
        assert audio_bytes > 0
    assert analysis.questions == ["How does Sentia work?"]
    assert gateway.connection.closed
