from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sentia_sidecar.app import create_app
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
