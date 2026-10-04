from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sentia_sidecar.app import create_app
from sentia_sidecar.repository import RepositoryManifest, build_repository_manifest
from sentia_sidecar.settings import Settings

TEST_TOKEN = "test-token-that-is-at-least-thirty-two-characters"


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'sentia-test.db'}"


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(token=TEST_TOKEN, database_url=database_url)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture
def markdown_first_manifest(tmp_path: Path) -> RepositoryManifest:
    """Reproduce the session's three long documents followed by five code files."""
    for name in ("README.md", "PRODUCT_VISION_V2.md", "SESSIONS.md"):
        (tmp_path / name).write_text(
            f"# {name}\n"
            + "Planned features are not proof of implementation. " * 2
            + "\n"
            + ("Documentation evidence describing future product features.\n" * 170),
            encoding="utf-8",
        )
    for index, relative in enumerate(
        (
            "backend/config/urls.py",
            "backend/auth/models.py",
            "backend/billing/models.py",
            "backend/tasks/models.py",
            "backend/projects/models.py",
        )
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"def feature_{index}():\n    return 'implemented feature {index}'\n\n"
            + ("# Implementation evidence showing current product behavior.\n" * 170),
            encoding="utf-8",
        )
    return build_repository_manifest(str(tmp_path))
