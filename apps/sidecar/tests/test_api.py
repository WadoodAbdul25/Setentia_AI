from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sentia_sidecar.agent_runtime import AgentAvailability, AgentRouter
from sentia_sidecar.anthropic_service import AnthropicRepositoryService
from sentia_sidecar.app import create_app
from sentia_sidecar.protocol import (
    PROTOCOL_VERSION,
    AgentProvider,
    EvidenceRange,
    RepositoryAnswer,
    TokenUsage,
    WorkingMode,
)
from sentia_sidecar.repository import RepositoryManifest
from sentia_sidecar.repository_intelligence import RepositoryIntelligenceRouter, SpeechAnswer
from sentia_sidecar.settings import Settings
from starlette.websockets import WebSocketDisconnect


class FakeAgentAdapter:
    def __init__(self, provider: AgentProvider, authentication: str) -> None:
        self.provider = provider
        self.authentication = authentication

    async def availability(self) -> AgentAvailability:
        return AgentAvailability(
            provider=self.provider,
            installed=True,
            authentication=self.authentication,
            detail=f"{self.provider.value} status",
        )


class FakeCodexLoginManager:
    closed = False

    async def start(self) -> str:
        return "https://auth.openai.com/example"

    async def close(self) -> None:
        self.closed = True


def test_health_requires_extension_token(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 401


def test_health_reports_protocol(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/health", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["protocolVersion"] == PROTOCOL_VERSION
    assert response.json()["workflowState"] == "ready"


def test_openai_voice_credentials_stay_in_sidecar_memory(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    connected = client.put(
        "/api/v1/auth/openai-voice",
        headers=auth_headers,
        json={"apiKey": "openai-test-key-long-enough"},  # pragma: allowlist secret
    )
    status = client.get("/api/v1/auth/openai-voice", headers=auth_headers)
    disconnected = client.delete("/api/v1/auth/openai-voice", headers=auth_headers)

    assert connected.json()["connected"] is True
    assert status.json()["connected"] is True
    assert disconnected.json()["connected"] is False


def test_agent_connection_status_is_provider_specific(
    settings: Settings, auth_headers: dict[str, str]
) -> None:
    agents = AgentRouter(
        [
            FakeAgentAdapter(AgentProvider.CLAUDE, "claude_login_or_missing"),
            FakeAgentAdapter(AgentProvider.CODEX, "codex_login"),
        ]
    )
    with TestClient(create_app(settings, agent_router=agents)) as test_client:
        claude = test_client.get("/api/v1/agents/claude/status", headers=auth_headers)
        codex = test_client.get("/api/v1/agents/codex/status", headers=auth_headers)

    assert claude.json() == {
        "provider": "claude",
        "connected": False,
        "authentication": "claude_login_or_missing",
        "message": "claude status",
    }
    assert codex.json()["connected"] is True
    assert codex.json()["authentication"] == "codex_login"


def test_codex_login_returns_the_sdk_browser_url(
    settings: Settings, auth_headers: dict[str, str]
) -> None:
    login = FakeCodexLoginManager()
    with TestClient(create_app(settings, codex_login_manager=login)) as test_client:
        response = test_client.post("/api/v1/agents/codex/login", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "provider": "codex",
        "authUrl": "https://auth.openai.com/example",
    }
    assert login.closed is True


def test_conversation_persists_across_restart(
    settings: Settings, auth_headers: dict[str, str]
) -> None:
    with TestClient(create_app(settings)) as first:
        created = first.post(
            "/api/v1/conversations",
            headers=auth_headers,
            json={"title": "Architecture"},
        )
        assert created.status_code == 201

    with TestClient(create_app(settings)) as second:
        listed = second.get("/api/v1/conversations", headers=auth_headers)
        assert listed.status_code == 200
        assert [item["title"] for item in listed.json()] == ["Architecture"]


def test_websocket_rejects_missing_token(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as raised, client.websocket_connect("/ws"):
        pass
    assert raised.value.code == 1008


def test_websocket_sends_typed_connected_event(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    with client.websocket_connect("/ws?lastSequence=0", headers=auth_headers) as websocket:
        event = websocket.receive_json()
        assert event["protocolVersion"] == PROTOCOL_VERSION
        assert event["type"] == "system.connected"
        assert event["sequence"] >= 1


def test_workspace_attach_creates_project_snapshot(
    client: TestClient,
    auth_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("# Fixture\n", encoding="utf-8")

    response = client.put(
        "/api/v1/repository/snapshot",
        headers=auth_headers,
        json={"workspacePath": str(tmp_path)},
    )

    assert response.status_code == 200
    assert response.json()["workspaceName"] == tmp_path.name
    assert response.json()["snapshotPath"] == ".sentia/project-structure.json"
    assert response.json()["fileCount"] == 1
    assert response.json()["watching"] is True
    assert (tmp_path / ".sentia" / "project-structure.json").is_file()


class FakeAnthropicService(AnthropicRepositoryService):
    async def answer(self, question: str, repository: RepositoryManifest) -> RepositoryAnswer:
        assert question == "What is this codebase?"
        return RepositoryAnswer(
            answer="This fixture documents a small project.",
            spoken_answer="This fixture documents a small project.",
            evidence=[
                EvidenceRange(path="README.md", start_line=1, end_line=2, label="Project README")
            ],
            recommended_mode=WorkingMode.BRAINSTORM,
            mode_reason="The user asked for an explanation.",
            model=self.model,
            files_scanned=repository.files_scanned,
            files_read=1,
            selected_files=["README.md"],
            usage=TokenUsage(input_tokens=120, output_tokens=30),
        )

    async def render_speech(self, answer: str, workspace_path: Path) -> SpeechAnswer:
        return SpeechAnswer(spoken_answer=answer)


class FakeCodexRepositoryService:
    provider = AgentProvider.CODEX
    model = "codex-test"

    def __init__(self) -> None:
        self.questions: list[str] = []
        self.speech_requests: list[tuple[str, Path]] = []

    async def answer(
        self,
        question: str,
        repository: RepositoryManifest,
    ) -> RepositoryAnswer:
        self.questions.append(question)
        return RepositoryAnswer(
            answer="Codex inspected the fixture.",
            spoken_answer="Codex inspected the fixture in spoken form.",
            evidence=[],
            recommended_mode=WorkingMode.BRAINSTORM,
            mode_reason="The user asked for an explanation.",
            model=self.model,
            files_scanned=repository.files_scanned,
            files_read=1,
            selected_files=["README.md"],
            usage=TokenUsage(input_tokens=80, output_tokens=20),
        )

    async def render_speech(self, answer: str, workspace_path: Path) -> SpeechAnswer:
        self.speech_requests.append((answer, workspace_path))
        return SpeechAnswer(spoken_answer="Codex inspected the fixture in spoken form.")


def test_anthropic_auth_and_repository_question_flow(
    settings: Settings,
    auth_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("# Fixture\nA small project.\n", encoding="utf-8")
    service = FakeAnthropicService("claude-haiku-4-5-20251001")
    fake_api_key = "sk-ant-api03-test-key-long-enough"  # pragma: allowlist secret
    with TestClient(create_app(settings, service)) as test_client:
        connected = test_client.put(
            "/api/v1/auth/anthropic",
            headers=auth_headers,
            json={"apiKey": fake_api_key, "validate": False},
        )
        assert connected.status_code == 200
        assert connected.json()["connected"] is True

        answer = test_client.post(
            "/api/v1/repository/questions",
            headers=auth_headers,
            json={
                "workspacePath": str(tmp_path),
                "question": "What is this codebase?",
            },
        )
        assert answer.status_code == 200
        assert answer.json()["evidence"][0]["path"] == "README.md"
        assert answer.json()["recommendedMode"] == "brainstorm"
        assert answer.json()["filesRead"] == 1
        assert answer.json()["usage"]["inputTokens"] == 120

        disconnected = test_client.delete("/api/v1/auth/anthropic", headers=auth_headers)
        assert disconnected.json()["connected"] is False


def test_repository_question_routes_to_selected_codex_provider(
    settings: Settings,
    auth_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("# Fixture\n", encoding="utf-8")
    codex = FakeCodexRepositoryService()
    intelligence = RepositoryIntelligenceRouter([codex])

    with TestClient(create_app(settings, repository_router=intelligence)) as test_client:
        answer = test_client.post(
            "/api/v1/repository/questions",
            headers=auth_headers,
            json={
                "workspacePath": str(tmp_path),
                "question": "Explain this with Codex.",
                "provider": "codex",
            },
        )

    assert answer.status_code == 200
    assert answer.json()["model"] == "codex-test"
    assert answer.json()["answer"] == "Codex inspected the fixture."
    assert answer.json()["spokenAnswer"] == "Codex inspected the fixture in spoken form."
    assert codex.questions == ["Explain this with Codex."]


def test_speech_render_uses_the_exact_display_answer(
    settings: Settings,
    auth_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    codex = FakeCodexRepositoryService()
    intelligence = RepositoryIntelligenceRouter([codex])
    display_answer = "The front end uses Tailwind CSS and calls the API."

    with TestClient(create_app(settings, repository_router=intelligence)) as test_client:
        response = test_client.post(
            "/api/v1/voice/render",
            headers=auth_headers,
            json={
                "workspacePath": str(tmp_path),
                "answer": display_answer,
                "provider": "codex",
            },
        )
        streamed = test_client.post(
            "/api/v1/voice/render/stream",
            headers=auth_headers,
            json={
                "workspacePath": str(tmp_path),
                "answer": display_answer,
                "provider": "codex",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"spokenAnswer": "Codex inspected the fixture in spoken form."}
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("application/x-ndjson")
    assert streamed.text.splitlines() == [
        '{"type": "delta", "text": "Codex inspected the fixture in spoken form."}',
        '{"type":"done"}',
    ]
    assert codex.speech_requests == [
        (display_answer, tmp_path),
        (display_answer, tmp_path),
    ]
