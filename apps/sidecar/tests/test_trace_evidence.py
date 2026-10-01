from __future__ import annotations

from pathlib import Path

import pytest
from sentia_sidecar.repository import RepositoryError, build_repository_manifest
from sentia_sidecar.structural_index import StructuralIndex
from sentia_sidecar.trace_evidence import TraceEvidenceReader, build_stack_profile


def reader(root: Path):
    manifest = build_repository_manifest(str(root))
    index = StructuralIndex(
        root, manifest.repository_revision, manifest.entities, manifest.relationships
    )
    return TraceEvidenceReader(manifest, index)


def test_reads_late_symbol_and_tracks_exact_lines(tmp_path):
    (tmp_path / "feature.py").write_text(
        "# padding\n" * 300 + "def late(seed):\n    return seed.upper()\n"
    )
    source = reader(tmp_path)
    entity = next(item for item in source.index.entities if item.name == "late")
    result = source.read_symbols([entity.entity_id])
    assert "return seed.upper()" in result.prompt
    assert result.included_lines == {"feature.py": frozenset({301, 302})}
    assert "padding" not in result.prompt


def test_rejects_stale_and_outside_evidence(tmp_path):
    path = tmp_path / "feature.py"
    path.write_text("def entry():\n    return 1\n")
    source = reader(tmp_path)
    with pytest.raises(RepositoryError):
        source.read_ranges([("../outside.py", 1, 2)])
    path.write_text("def entry():\n    return 2\n")
    with pytest.raises(RepositoryError, match="stale"):
        source.read_ranges([("feature.py", 1, 2)])


def test_evidence_budget_marks_truncation(tmp_path):
    (tmp_path / "feature.py").write_text("def entry():\n" + "    value = 'sample'\n" * 100)
    source = reader(tmp_path)
    result = source.read_ranges([("feature.py", 1, 101)], max_chars=500)
    assert len(result.prompt) <= 500
    assert "TRUNCATED" in result.prompt
    assert max(result.included_lines["feature.py"]) < 101


def test_stack_distinguishes_declared_observed_and_ignores_comments(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["celery>=5", "fastapi"]\n')
    (tmp_path / "api.py").write_text(
        "from fastapi import FastAPI\n# import django\napp = FastAPI()\n"
    )
    (tmp_path / "package.json").write_text(
        '{"dependencies":{"react":"1"},"engines":{"node":">=22"}}'
    )
    profile = build_stack_profile(build_repository_manifest(str(tmp_path)))
    technologies = profile["technologies"]
    assert any(item["name"] == "Celery" and item["status"] == "declared" for item in technologies)
    assert not any(
        item["name"] == "Celery" and item["status"] == "observed" for item in technologies
    )
    assert any(item["name"] == "FastAPI" and item["status"] == "observed" for item in technologies)
    assert not any(item["name"] == "Django" for item in technologies)


def test_evidence_rechecks_new_ignore_rules(tmp_path):
    (tmp_path / "feature.py").write_text("def entry():\n    return 1\n")
    source = reader(tmp_path)
    (tmp_path / ".gitignore").write_text("feature.py\n")
    with pytest.raises(RepositoryError, match="ignored"):
        source.read_ranges([("feature.py", 1, 2)])
