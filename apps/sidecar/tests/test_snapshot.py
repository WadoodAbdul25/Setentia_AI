from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sentia_sidecar.snapshot import (
    SNAPSHOT_RELATIVE_PATH,
    ProjectSnapshotService,
    _watch_filter,
    load_snapshot_project_paths,
    load_snapshot_repository_manifest,
    refresh_project_snapshot,
)
from watchfiles import Change


def test_snapshot_preserves_created_at_and_refreshes_cached_file_metadata(
    tmp_path: Path,
) -> None:
    initial_time = datetime(2026, 7, 27, 10, 0, tzinfo=UTC)
    (tmp_path / "README.md").write_text("# Fixture\n", encoding="utf-8")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "app.py").write_text("app = object()\n", encoding="utf-8")

    initial, initial_changed = refresh_project_snapshot(str(tmp_path), now=initial_time)
    snapshot_path = tmp_path / SNAPSHOT_RELATIVE_PATH
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert initial_changed is True
    assert payload["created_at"] == payload["updated_at"]
    assert payload["schema_version"] == 2
    assert {item["path"] for item in payload["manifest"]} == {
        "backend/app.py",
        "README.md",
    }
    assert payload["files"] == ["backend/app.py", "README.md"]
    assert payload["directories"] == ["backend"]
    assert initial.watching is False
    loaded_paths = load_snapshot_project_paths(str(tmp_path))
    assert loaded_paths is not None
    assert loaded_paths.files == ("backend/app.py", "README.md")
    cached_manifest = load_snapshot_repository_manifest(str(tmp_path))
    assert cached_manifest is not None
    assert cached_manifest.cache_source == "snapshot"

    unchanged_time = initial_time + timedelta(minutes=1)
    (tmp_path / "README.md").write_text("# Fixture changed\n", encoding="utf-8")
    refreshed, refreshed_changed = refresh_project_snapshot(str(tmp_path), now=unchanged_time)
    assert refreshed_changed is True
    assert refreshed.created_at == initial.created_at
    assert refreshed.updated_at == unchanged_time

    created_time = initial_time + timedelta(minutes=2)
    (tmp_path / "frontend.ts").write_text("export {};\n", encoding="utf-8")
    created, created_changed = refresh_project_snapshot(str(tmp_path), now=created_time)
    assert created_changed is True
    assert created.created_at == initial.created_at
    assert created.updated_at == created_time

    deleted_time = initial_time + timedelta(minutes=3)
    (tmp_path / "frontend.ts").unlink()
    deleted, deleted_changed = refresh_project_snapshot(str(tmp_path), now=deleted_time)
    assert deleted_changed is True
    assert deleted.created_at == initial.created_at
    assert deleted.updated_at == deleted_time


def test_snapshot_watch_filter_accepts_create_delete_and_modify(tmp_path: Path) -> None:
    source = tmp_path / "src" / "new.py"
    internal_snapshot = tmp_path / ".sentia" / "project-structure.json"

    assert _watch_filter(tmp_path, Change.added, str(source)) is True
    assert _watch_filter(tmp_path, Change.deleted, str(source)) is True
    assert _watch_filter(tmp_path, Change.modified, str(source)) is True
    assert _watch_filter(tmp_path, Change.added, str(internal_snapshot)) is False


def test_tampered_snapshot_cannot_escape_workspace(tmp_path: Path) -> None:
    (tmp_path / ".sentia").mkdir()
    (tmp_path / SNAPSHOT_RELATIVE_PATH).write_text(
        json.dumps(
            {
                "schema_version": 2,
                "workspace_name": tmp_path.name,
                "created_at": "2026-07-27T10:00:00Z",
                "updated_at": "2026-07-27T10:00:00Z",
                "directories": [],
                "files": ["../outside.py"],
                "manifest": [],
            }
        ),
        encoding="utf-8",
    )

    assert load_snapshot_project_paths(str(tmp_path)) is None


async def test_attached_snapshot_manifest_is_kept_in_memory(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("def main() -> None:\n    pass\n", encoding="utf-8")
    service = ProjectSnapshotService()

    try:
        await service.attach(str(tmp_path))
        first = await service.manifest(str(tmp_path))
        second = await service.manifest(str(tmp_path))
    finally:
        await service.stop()

    assert first is not None
    assert first.cache_source == "snapshot"
    assert first is second
