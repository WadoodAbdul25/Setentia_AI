from __future__ import annotations

import time
from pathlib import Path

import pytest
import sentia_sidecar.repository_agent_tools as tools_module
from sentia_sidecar.repository import build_repository_manifest
from sentia_sidecar.repository_agent_tools import RepositoryAgentTools


async def test_snapshot_search_understands_provider_aliases_and_ignores_prompt_filler(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "backend" / "apps" / "ai" / "providers"
    provider.mkdir(parents=True)
    (provider / "anthropic_client.py").write_text(
        "class AnthropicClient:\n    def complete(self): ...\n",
        encoding="utf-8",
    )
    (provider / "gemini_client.py").write_text(
        "class GeminiClient:\n    def complete(self): ...\n",
        encoding="utf-8",
    )
    landing = tmp_path / "frontend" / "src" / "components" / "landing"
    landing.mkdir(parents=True)
    (landing / "ShaderHero.jsx").write_text(
        "const THEMES = {};\nfunction useCanvas() {}\n",
        encoding="utf-8",
    )
    manifest = build_repository_manifest(str(tmp_path))
    tools = RepositoryAgentTools(manifest)

    results = await tools.search(
        "what the gemini and claude agents exactly do and pair them with files and functions",
        4,
    )
    paths = [line.split('"path":"', 1)[1].split('"', 1)[0] for line in results.splitlines()]

    assert "backend/apps/ai/providers/anthropic_client.py" in paths
    assert "backend/apps/ai/providers/gemini_client.py" in paths
    assert "frontend/src/components/landing/ShaderHero.jsx" not in paths


async def test_snapshot_read_preserves_line_numbers_and_rejects_unknown_paths(
    tmp_path: Path,
) -> None:
    (tmp_path / "service.py").write_text(
        "def run() -> str:\n    return 'ready'\n",
        encoding="utf-8",
    )
    manifest = build_repository_manifest(str(tmp_path))
    tools = RepositoryAgentTools(manifest)

    content = await tools.read("service.py", "full")

    assert "### FILE: service.py" in content
    assert "   1: def run() -> str:" in content
    assert tools.read_requests == (("service.py", "full"),)
    with pytest.raises(ValueError, match="eligible-file snapshot"):
        await tools.read("../secrets.txt", "full")


async def test_snapshot_read_has_a_hard_retrieval_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "service.py").write_text("def run(): ...\n", encoding="utf-8")
    manifest = build_repository_manifest(str(tmp_path))
    original_reader = tools_module.build_selected_repository_context

    def slow_reader(*args: object, **kwargs: object) -> object:
        time.sleep(0.05)
        return original_reader(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(tools_module, "CONTENT_RETRIEVAL_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(tools_module, "build_selected_repository_context", slow_reader)
    tools = RepositoryAgentTools(manifest)

    with pytest.raises(TimeoutError):
        await tools.read("service.py", "full")
