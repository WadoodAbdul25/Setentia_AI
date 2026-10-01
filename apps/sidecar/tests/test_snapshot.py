from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sentia_sidecar.protocol import (
    FlowEntityKind,
    FlowProvenance,
    FlowRelationshipKind,
    FlowResolution,
)
from sentia_sidecar.snapshot import (
    SNAPSHOT_RELATIVE_PATH,
    ProjectSnapshotService,
    _watch_filter,
    load_snapshot_code_index,
    load_snapshot_project_paths,
    load_snapshot_repository_manifest,
    load_snapshot_structural_index,
    refresh_project_snapshot,
)
from sentia_sidecar.structural_index import CodeEntity, RelationshipFact
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
    assert payload["schema_version"] == 5
    assert payload["repository_revision"] == 1
    assert initial.repository_revision == 1
    assert {item["path"] for item in payload["manifest"]} == {
        "backend/app.py",
        "README.md",
    }
    assert payload["files"] == ["backend/app.py", "README.md"]
    assert payload["directories"] == ["backend"]
    assert all(len(item["content_hash"]) == 64 for item in payload["manifest"])
    assert {entity["kind"] for entity in payload["entities"]} == {"file", "module"}
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
    assert refreshed.repository_revision == 2
    assert refreshed.created_at == initial.created_at
    assert refreshed.updated_at == unchanged_time

    created_time = initial_time + timedelta(minutes=2)
    (tmp_path / "frontend.ts").write_text("export {};\n", encoding="utf-8")
    created, created_changed = refresh_project_snapshot(str(tmp_path), now=created_time)
    assert created_changed is True
    assert created.repository_revision == 3
    assert created.created_at == initial.created_at
    assert created.updated_at == created_time

    deleted_time = initial_time + timedelta(minutes=3)
    (tmp_path / "frontend.ts").unlink()
    deleted, deleted_changed = refresh_project_snapshot(str(tmp_path), now=deleted_time)
    assert deleted_changed is True
    assert deleted.repository_revision == 4
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
                "schema_version": 5,
                "repository_revision": 1,
                "workspace_name": tmp_path.name,
                "created_at": "2026-07-27T10:00:00Z",
                "updated_at": "2026-07-27T10:00:00Z",
                "directories": [],
                "files": ["../outside.py"],
                "manifest": [],
                "entities": [],
            }
        ),
        encoding="utf-8",
    )

    assert load_snapshot_project_paths(str(tmp_path)) is None


def test_same_size_body_edit_refreshes_snapshot_content_identity(tmp_path: Path) -> None:
    initial_time = datetime(2026, 7, 27, 10, 0, tzinfo=UTC)
    source = tmp_path / "service.py"
    source.write_text("def service():\n    return 1\n", encoding="utf-8")

    initial, initial_changed = refresh_project_snapshot(str(tmp_path), now=initial_time)
    snapshot_path = tmp_path / SNAPSHOT_RELATIVE_PATH
    initial_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    initial_entry = initial_payload["manifest"][0]
    initial_function = next(
        entity for entity in initial_payload["entities"] if entity["qualified_name"] == "service"
    )

    source.write_text("def service():\n    return 2\n", encoding="utf-8")
    edited_time = initial_time + timedelta(minutes=1)
    refreshed, refreshed_changed = refresh_project_snapshot(str(tmp_path), now=edited_time)
    refreshed_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    refreshed_entry = refreshed_payload["manifest"][0]
    refreshed_function = next(
        entity for entity in refreshed_payload["entities"] if entity["qualified_name"] == "service"
    )

    assert initial_changed is True
    assert refreshed_changed is True
    assert refreshed.repository_revision == 2
    assert refreshed.created_at == initial.created_at
    assert refreshed.updated_at == edited_time
    assert refreshed_entry["size"] == initial_entry["size"]
    assert refreshed_entry["structure"] == initial_entry["structure"]
    assert refreshed_entry["content_hash"] != initial_entry["content_hash"]
    assert refreshed_function["entity_id"] == initial_function["entity_id"]
    assert refreshed_function["content_hash"] == refreshed_entry["content_hash"]
    assert refreshed_function["repository_revision"] == 2
    assert refreshed_entry["content_hash"] == hashlib.sha256(source.read_bytes()).hexdigest()
    cached_manifest = load_snapshot_repository_manifest(str(tmp_path))
    assert cached_manifest is not None
    assert cached_manifest.repository_revision == 2
    assert cached_manifest.files["service.py"].content_hash == refreshed_entry["content_hash"]


def test_unchanged_file_identity_does_not_rewrite_snapshot(tmp_path: Path) -> None:
    initial_time = datetime(2026, 7, 27, 10, 0, tzinfo=UTC)
    source = tmp_path / "service.py"
    source.write_text(
        "def service():\n    return 1\n",
        encoding="utf-8",
    )
    initial, initial_changed = refresh_project_snapshot(str(tmp_path), now=initial_time)
    refreshed, refreshed_changed = refresh_project_snapshot(
        str(tmp_path),
        now=initial_time + timedelta(minutes=1),
    )

    assert initial_changed is True
    assert refreshed_changed is False
    assert refreshed.repository_revision == 1
    assert refreshed.created_at == initial.created_at
    assert refreshed.updated_at == initial.updated_at


def test_legacy_snapshot_is_rebuilt_with_content_identities_and_revision(tmp_path: Path) -> None:
    legacy_time = datetime(2026, 7, 27, 10, 0, tzinfo=UTC)
    refresh_time = legacy_time + timedelta(minutes=1)
    source = tmp_path / "service.py"
    source.write_text(
        "def service():\n    return 1\n",
        encoding="utf-8",
    )
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    snapshot_path = tmp_path / SNAPSHOT_RELATIVE_PATH
    snapshot_path.parent.mkdir()
    snapshot_path.write_text(
        json.dumps(
            {
                "schema_version": 4,
                "repository_revision": 7,
                "workspace_name": tmp_path.name,
                "created_at": legacy_time.isoformat(),
                "updated_at": legacy_time.isoformat(),
                "directories": [],
                "files": ["service.py"],
                "manifest": [
                    {
                        "path": "service.py",
                        "size": 28,
                        "content_hash": source_hash,
                        "priority": 1,
                        "role": "source",
                        "structure": "top-level symbols=[function:service@1]",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    refreshed, changed = refresh_project_snapshot(str(tmp_path), now=refresh_time)
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert changed is True
    assert refreshed.created_at == legacy_time
    assert refreshed.repository_revision == 8
    assert payload["schema_version"] == 5
    assert payload["repository_revision"] == 8
    assert payload["manifest"][0]["content_hash"] == source_hash
    assert any(entity["qualified_name"] == "service" for entity in payload["entities"])


async def test_attached_snapshot_manifest_is_kept_in_memory(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("def main() -> None:\n    pass\n", encoding="utf-8")
    service = ProjectSnapshotService()

    try:
        await service.attach(str(tmp_path))
        first = await service.manifest(str(tmp_path))
        second = await service.manifest(str(tmp_path))
        code_index = await service.code_index(str(tmp_path))
    finally:
        await service.stop()

    assert first is not None
    assert first.cache_source == "snapshot"
    assert first.repository_revision == 1
    assert first is second
    assert code_index is not None
    assert code_index.search_entities("main")[0].qualified_name == "main"


def test_snapshot_code_index_resolves_evidence_to_smallest_symbol(tmp_path: Path) -> None:
    (tmp_path / "auth.py").write_text(
        "class AuthService:\n    def login(self) -> bool:\n        return True\n",
        encoding="utf-8",
    )
    refresh_project_snapshot(str(tmp_path))

    index = load_snapshot_code_index(str(tmp_path))

    assert index is not None
    entity = index.find_entity_at_location("auth.py", 3)
    assert entity is not None
    assert entity.qualified_name == "AuthService.login"


def test_snapshot_round_trips_revision_bound_relationship_facts(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        "def helper() -> bool:\n    return True\n\ndef service() -> bool:\n    return helper()\n",
        encoding="utf-8",
    )
    refresh_project_snapshot(str(tmp_path))
    snapshot_path = tmp_path / SNAPSHOT_RELATIVE_PATH
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    entities = [CodeEntity.model_validate(entity) for entity in payload["entities"]]
    by_name = {entity.qualified_name: entity for entity in entities}
    service = by_name["service"]
    helper = by_name["helper"]
    relationship = RelationshipFact.create(
        repository_revision=1,
        source_entity_id=service.entity_id,
        target_entity_id=helper.entity_id,
        kind=FlowRelationshipKind.CALLS,
        resolution=FlowResolution.STATICALLY_RESOLVED,
        evidence=(service.flow_source_span(),),
        provenance=FlowProvenance(
            extractor="sentia.python.ast",
            extractor_version="1.0.0",
            rule_id="python.call.name",
        ),
    )
    payload["relationships"] = [relationship.model_dump(mode="json")]
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    index = load_snapshot_structural_index(str(tmp_path))
    manifest = load_snapshot_repository_manifest(str(tmp_path))

    assert index is not None
    assert index.repository_revision == 1
    assert index.outgoing_relationships(service.entity_id) == (relationship,)
    assert index.incoming_relationships(helper.entity_id) == (relationship,)
    assert manifest is not None
    assert manifest.relationships == (relationship,)


def test_snapshot_reextracts_relationships_at_the_new_repository_revision(
    tmp_path: Path,
) -> None:
    source = tmp_path / "service.py"
    source.write_text(
        "def helper() -> int:\n    return 1\n\ndef service() -> int:\n    return helper()\n",
        encoding="utf-8",
    )
    refresh_project_snapshot(str(tmp_path))
    snapshot_path = tmp_path / SNAPSHOT_RELATIVE_PATH
    initial_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    initial_relationship = initial_payload["relationships"][0]

    source.write_text(
        "def helper() -> int:\n    return 2\n\ndef service() -> int:\n    return helper()\n",
        encoding="utf-8",
    )
    refreshed, changed = refresh_project_snapshot(str(tmp_path))
    refreshed_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    refreshed_relationship = refreshed_payload["relationships"][0]
    index = load_snapshot_structural_index(str(tmp_path))

    assert changed is True
    assert refreshed.repository_revision == 2
    assert refreshed_relationship["repository_revision"] == 2
    assert refreshed_relationship["evidence"][0]["repository_revision"] == 2
    assert refreshed_relationship["relationship_id"] != initial_relationship["relationship_id"]
    assert index is not None
    service = next(
        entity
        for entity in index.entities
        if entity.kind == FlowEntityKind.FUNCTION and entity.qualified_name == "service"
    )
    helper = next(
        entity
        for entity in index.entities
        if entity.kind == FlowEntityKind.FUNCTION and entity.qualified_name == "helper"
    )
    assert index.outgoing_relationships(service.entity_id)[0].target_entity_id == helper.entity_id
