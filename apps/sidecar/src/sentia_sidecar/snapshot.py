from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

import structlog
from pydantic import BaseModel, ConfigDict, Field, model_validator
from watchfiles import Change, awatch

from sentia_sidecar.protocol import ProjectSnapshotStatus
from sentia_sidecar.repository import (
    DEPENDENCY_DIRECTORY_NAMES,
    RUNTIME_ARTIFACT_SUFFIXES,
    SENSITIVE_NAMES,
    ProjectPaths,
    RepositoryError,
    RepositoryFile,
    RepositoryManifest,
    assemble_repository_manifest,
    build_repository_manifest,
    discover_project_paths,
)
from sentia_sidecar.structural_index import (
    CodeEntity,
    CodeEntityIndex,
    RelationshipFact,
    StructuralIndex,
)

SNAPSHOT_RELATIVE_PATH = Path(".sentia/project-structure.json")
SNAPSHOT_SCHEMA_VERSION: Literal[5] = 5
WATCH_IGNORED_DIRECTORIES = DEPENDENCY_DIRECTORY_NAMES | {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".sentia",
    "__pycache__",
    "build",
    "coverage",
    "dist",
}

logger = structlog.get_logger(__name__)


class SnapshotError(RuntimeError):
    pass


class SnapshotManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    size: int
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    priority: int
    role: str
    structure: str


class SnapshotDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[5] = SNAPSHOT_SCHEMA_VERSION
    repository_revision: int = Field(ge=1)
    workspace_name: str
    created_at: datetime
    updated_at: datetime
    directories: list[str]
    files: list[str]
    manifest: list[SnapshotManifestEntry] = Field(default_factory=list)
    entities: list[CodeEntity] = Field(default_factory=list)
    relationships: list[RelationshipFact] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_structural_facts(self) -> Self:
        known_files = set(self.files)
        if any(entity.path not in known_files for entity in self.entities):
            raise ValueError("Snapshot entities must reference known files.")
        hashes_by_path = {entry.path: entry.content_hash for entry in self.manifest}
        if any(hashes_by_path.get(entity.path) != entity.content_hash for entity in self.entities):
            raise ValueError("Snapshot entities must match their file content hashes.")
        StructuralIndex(
            Path("."),
            self.repository_revision,
            self.entities,
            self.relationships,
        )
        return self


class LegacySnapshotDocumentV4(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[4]
    repository_revision: int = Field(ge=1)
    workspace_name: str
    created_at: datetime
    updated_at: datetime
    directories: list[str]
    files: list[str]
    manifest: list[SnapshotManifestEntry] = Field(default_factory=list)


SnapshotUpdateCallback = Callable[[ProjectSnapshotStatus, list[str]], Awaitable[None]]


def refresh_project_snapshot(
    workspace_path: str,
    *,
    now: datetime | None = None,
) -> tuple[ProjectSnapshotStatus, bool]:
    project = discover_project_paths(workspace_path)
    snapshot_path = project.root / SNAPSHOT_RELATIVE_PATH
    existing = _load_snapshot(snapshot_path)
    legacy = _load_legacy_snapshot_v4(snapshot_path) if existing is None else None
    current_time = now or datetime.now(UTC)
    directories = list(project.directories)
    files = list(project.files)
    matching_existing = (
        existing if existing is not None and existing.workspace_name == project.root.name else None
    )
    matching_legacy = (
        legacy if legacy is not None and legacy.workspace_name == project.root.name else None
    )
    baseline = matching_existing or matching_legacy
    comparison_revision = baseline.repository_revision if baseline is not None else 1
    manifest_entries, entities, relationships = _snapshot_repository_facts(
        project,
        comparison_revision,
    )
    changed = (
        matching_existing is None
        or matching_existing.directories != directories
        or matching_existing.files != files
        or matching_existing.manifest != manifest_entries
        or matching_existing.entities != entities
        or matching_existing.relationships != relationships
    )

    if matching_existing is not None and not changed:
        document = matching_existing
    else:
        repository_revision = baseline.repository_revision + 1 if baseline is not None else 1
        revisioned_entities = [
            entity.model_copy(update={"repository_revision": repository_revision})
            for entity in entities
        ]
        revisioned_relationships = [
            relationship.at_repository_revision(repository_revision)
            for relationship in relationships
        ]
        document = SnapshotDocument(
            repository_revision=repository_revision,
            workspace_name=project.root.name,
            created_at=baseline.created_at if baseline is not None else current_time,
            updated_at=current_time,
            directories=directories,
            files=files,
            manifest=manifest_entries,
            entities=revisioned_entities,
            relationships=revisioned_relationships,
        )
        _write_snapshot(snapshot_path, document)

    return _status(document, watching=False), changed


def load_snapshot_project_paths(workspace_path: str) -> ProjectPaths | None:
    root = Path(workspace_path).expanduser().resolve()
    document = _load_snapshot(root / SNAPSHOT_RELATIVE_PATH)
    if document is None or document.workspace_name != root.name:
        return None
    if not all(_is_safe_relative_path(path) for path in [*document.directories, *document.files]):
        logger.warning("project_snapshot_rejected", workspace_name=root.name)
        return None
    return ProjectPaths(
        root=root,
        directories=tuple(document.directories),
        files=tuple(document.files),
    )


def load_snapshot_repository_manifest(workspace_path: str) -> RepositoryManifest | None:
    root = Path(workspace_path).expanduser().resolve()
    document = _load_snapshot(root / SNAPSHOT_RELATIVE_PATH)
    if document is None or document.workspace_name != root.name or not document.manifest:
        return None
    known_files = set(document.files)
    if not all(
        entry.path in known_files and _is_safe_relative_path(entry.path)
        for entry in document.manifest
    ):
        logger.warning("project_manifest_cache_rejected", workspace_name=root.name)
        return None
    candidates = [
        RepositoryFile(
            path=entry.path,
            size=entry.size,
            content_hash=entry.content_hash,
            priority=entry.priority,
            role=entry.role,
            structure=entry.structure,
        )
        for entry in document.manifest
    ]
    return assemble_repository_manifest(
        root,
        candidates,
        repository_revision=document.repository_revision,
        entities=document.entities,
        relationships=document.relationships,
        cache_source="snapshot",
    )


def load_snapshot_code_index(workspace_path: str) -> CodeEntityIndex | None:
    root = Path(workspace_path).expanduser().resolve()
    document = _load_snapshot(root / SNAPSHOT_RELATIVE_PATH)
    if document is None or document.workspace_name != root.name:
        return None
    if not all(_is_safe_relative_path(path) for path in document.files):
        logger.warning("project_code_index_rejected", workspace_name=root.name)
        return None
    return CodeEntityIndex(
        root,
        document.repository_revision,
        document.entities,
        document.relationships,
    )


def load_snapshot_structural_index(workspace_path: str) -> StructuralIndex | None:
    root = Path(workspace_path).expanduser().resolve()
    document = _load_snapshot(root / SNAPSHOT_RELATIVE_PATH)
    if document is None or document.workspace_name != root.name:
        return None
    if not all(_is_safe_relative_path(path) for path in document.files):
        logger.warning("project_structural_index_rejected", workspace_name=root.name)
        return None
    return StructuralIndex(
        root,
        document.repository_revision,
        document.entities,
        document.relationships,
    )


class ProjectSnapshotService:
    def __init__(self, on_update: SnapshotUpdateCallback | None = None) -> None:
        self._on_update = on_update
        self._root: Path | None = None
        self._manifest: RepositoryManifest | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def attach(self, workspace_path: str) -> ProjectSnapshotStatus:
        root = await asyncio.to_thread(_resolve_workspace_path, workspace_path)
        async with self._lock:
            if self._root != root:
                await self._stop_locked()
                self._root = root
            status, changed = await asyncio.to_thread(refresh_project_snapshot, str(root))
            self._manifest = await asyncio.to_thread(
                load_snapshot_repository_manifest,
                str(root),
            )
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(
                    self._watch(root),
                    name=f"sentia-snapshot-{root.name}",
                )
            watching_status = status.model_copy(update={"watching": True})
            logger.info(
                "project_snapshot_attached",
                workspace_name=watching_status.workspace_name,
                snapshot_path=watching_status.snapshot_path,
                files=watching_status.file_count,
                directories=watching_status.directory_count,
                repository_revision=watching_status.repository_revision,
                changed=changed,
            )
            return watching_status

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()
            self._root = None
            self._manifest = None

    async def manifest(self, workspace_path: str) -> RepositoryManifest | None:
        root = await asyncio.to_thread(_resolve_workspace_path, workspace_path)
        async with self._lock:
            if root == self._root and self._manifest is not None:
                return self._manifest
        return await asyncio.to_thread(load_snapshot_repository_manifest, str(root))

    async def code_index(self, workspace_path: str) -> CodeEntityIndex | None:
        manifest = await self.manifest(workspace_path)
        if manifest is None:
            return None
        return CodeEntityIndex(
            manifest.root,
            manifest.repository_revision,
            manifest.entities,
            manifest.relationships,
        )

    async def structural_index(self, workspace_path: str) -> StructuralIndex | None:
        manifest = await self.manifest(workspace_path)
        if manifest is None:
            return None
        return StructuralIndex(
            manifest.root,
            manifest.repository_revision,
            manifest.entities,
            manifest.relationships,
        )

    async def _stop_locked(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _watch(self, root: Path) -> None:
        try:
            async for changes in awatch(
                root,
                watch_filter=lambda change, path: _watch_filter(root, change, path),
                debounce=300,
                step=50,
                rust_timeout=500,
            ):
                changed_paths = _changed_relative_paths(root, changes)
                status, changed = await asyncio.to_thread(refresh_project_snapshot, str(root))
                if not changed:
                    continue
                watching_status = status.model_copy(update={"watching": True})
                self._manifest = await asyncio.to_thread(
                    load_snapshot_repository_manifest,
                    str(root),
                )
                logger.info(
                    "project_snapshot_updated",
                    workspace_name=watching_status.workspace_name,
                    files=watching_status.file_count,
                    directories=watching_status.directory_count,
                    repository_revision=watching_status.repository_revision,
                    triggers=changed_paths,
                )
                if self._on_update is not None:
                    await self._on_update(watching_status, changed_paths)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("project_snapshot_watcher_failed", workspace_name=root.name)


def _load_snapshot(path: Path) -> SnapshotDocument | None:
    try:
        return SnapshotDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, ValueError):
        return None


def _load_legacy_snapshot_v4(path: Path) -> LegacySnapshotDocumentV4 | None:
    try:
        return LegacySnapshotDocumentV4.model_validate_json(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, ValueError):
        return None


def _resolve_workspace_path(workspace_path: str) -> Path:
    return Path(workspace_path).expanduser().resolve()


def _snapshot_repository_facts(
    project: ProjectPaths,
    repository_revision: int,
) -> tuple[list[SnapshotManifestEntry], list[CodeEntity], list[RelationshipFact]]:
    try:
        manifest = build_repository_manifest(
            str(project.root),
            project,
            repository_revision=repository_revision,
        )
    except RepositoryError:
        return [], [], []
    entries = [
        SnapshotManifestEntry(
            path=item.path,
            size=item.size,
            content_hash=item.content_hash,
            priority=item.priority,
            role=item.role,
            structure=item.structure,
        )
        for item in manifest.files.values()
    ]
    return entries, list(manifest.entities), list(manifest.relationships)


def _is_safe_relative_path(value: str) -> bool:
    path = Path(value)
    lowered_name = path.name.lower()
    return (
        bool(value)
        and not path.is_absolute()
        and ".." not in path.parts
        and not any(ord(character) < 32 for character in value)
        and not any(part.lower() in WATCH_IGNORED_DIRECTORIES for part in path.parts)
        and lowered_name not in SENSITIVE_NAMES
        and not lowered_name.startswith(".env.")
        and not lowered_name.endswith(RUNTIME_ARTIFACT_SUFFIXES)
    )


def _write_snapshot(path: Path, document: SnapshotDocument) -> None:
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=".project-structure-",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document.model_dump(mode="json"), stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary_path, path)
    except (OSError, UnicodeError, ValueError) as error:
        raise SnapshotError(f"Sentia could not write {SNAPSHOT_RELATIVE_PATH}.") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _status(document: SnapshotDocument, *, watching: bool) -> ProjectSnapshotStatus:
    return ProjectSnapshotStatus(
        repository_revision=document.repository_revision,
        workspace_name=document.workspace_name,
        snapshot_path=SNAPSHOT_RELATIVE_PATH.as_posix(),
        created_at=document.created_at,
        updated_at=document.updated_at,
        file_count=len(document.files),
        directory_count=len(document.directories),
        watching=watching,
    )


def _watch_filter(root: Path, change: Change, raw_path: str) -> bool:
    if change not in {Change.added, Change.deleted, Change.modified}:
        return False
    try:
        relative = Path(raw_path).resolve().relative_to(root)
    except ValueError:
        return False
    return not any(part.lower() in WATCH_IGNORED_DIRECTORIES for part in relative.parts)


def _changed_relative_paths(root: Path, changes: set[tuple[Change, str]]) -> list[str]:
    relative_paths: list[str] = []
    for _, raw_path in changes:
        try:
            relative_paths.append(Path(raw_path).resolve().relative_to(root).as_posix())
        except ValueError:
            continue
    return sorted(relative_paths)[:100]
