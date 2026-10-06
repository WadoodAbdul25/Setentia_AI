from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sentia_sidecar.answer_stream import AnswerActivity, AnswerStreamEvent
from sentia_sidecar.app import create_app
from sentia_sidecar.protocol import AgentProvider, RepositoryAnswer, TokenUsage, WorkingMode
from sentia_sidecar.repository_intelligence import RepositoryIntelligenceRouter
from sentia_sidecar.settings import Settings


def result() -> RepositoryAnswer:
    return RepositoryAnswer(
        answer="First. Second.",
        spoken_answer="First and second.",
        evidence=[],
        recommended_mode=WorkingMode.BRAINSTORM,
        mode_reason="Explanation",
        model="fixture",
        files_scanned=1,
        files_read=1,
        selected_files=["main.py"],
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


def test_repository_stream_route_sends_progress_deltas_and_final_metadata(
    settings: Settings,
    auth_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text("def start(): return True\n", encoding="utf8")

    class Service:
        provider = AgentProvider.CODEX
        model = "fixture"

        async def stream_answer(self, *args: Any) -> Any:
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
            yield AnswerStreamEvent(type="delta", text="First. ")
            yield AnswerStreamEvent(type="delta", text="Second.")
            yield AnswerStreamEvent(type="complete", answer=result())

    app = create_app(settings, repository_router=RepositoryIntelligenceRouter([Service()]))  # type: ignore[list-item]
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/repository/questions/stream",
            headers=auth_headers,
            json={"workspacePath": str(tmp_path), "question": "Explain", "provider": "codex"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    assert [event["type"] for event in events] == [
        "progress",
        "activity",
        "delta",
        "delta",
        "complete",
    ]
    assert events[1]["activity"]["details"] == ["main.py"]
    assert (
        "".join(event["text"] for event in events if event["type"] == "delta") == "First. Second."
    )
    assert events[-1]["answer"]["usage"]["inputTokens"] == 10


def test_failed_repository_stream_never_marks_partial_text_complete(
    settings: Settings,
    auth_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text("def start(): return True\n", encoding="utf8")

    class Service:
        provider = AgentProvider.CODEX
        model = "fixture"

        async def stream_answer(self, *args: Any) -> Any:
            yield AnswerStreamEvent(type="delta", text="Partial answer")
            raise RuntimeError("sensitive-provider-internals")

    app = create_app(settings, repository_router=RepositoryIntelligenceRouter([Service()]))  # type: ignore[list-item]
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/repository/questions/stream",
            headers=auth_headers,
            json={"workspacePath": str(tmp_path), "question": "Explain", "provider": "codex"},
        )
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    assert [event["type"] for event in events] == ["delta", "error"]
    assert "sensitive-provider-internals" not in response.text
