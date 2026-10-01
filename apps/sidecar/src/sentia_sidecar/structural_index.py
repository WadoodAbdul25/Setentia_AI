from __future__ import annotations

import ast
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Collection, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sentia_sidecar.protocol import (
    FlowEntityId,
    FlowEntityKind,
    FlowProvenance,
    FlowRelationshipKind,
    FlowResolution,
    FlowSourceSpan,
)
from sentia_sidecar.relationship_signals import (
    RelationshipSignals,
    classify_relationship_signals,
)

INDEXED_ENTITY_KINDS = {
    FlowEntityKind.FILE,
    FlowEntityKind.MODULE,
    FlowEntityKind.CLASS,
    FlowEntityKind.FUNCTION,
    FlowEntityKind.METHOD,
}


class CodeEntity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: FlowEntityId
    kind: FlowEntityKind
    language: Literal["python"]
    path: str = Field(min_length=1, max_length=500)
    module: str = Field(min_length=1, max_length=1_000)
    name: str = Field(min_length=1, max_length=500)
    qualified_name: str = Field(min_length=1, max_length=1_000)
    parent_entity_id: FlowEntityId | None = None
    start_line: int = Field(ge=1)
    start_column: int = Field(ge=0)
    end_line: int = Field(ge=1)
    end_column: int = Field(ge=0)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    repository_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_entity(self) -> Self:
        path = PurePosixPath(self.path)
        if path.is_absolute() or ".." in path.parts or "\\" in self.path:
            raise ValueError("Code entity paths must be workspace-relative POSIX paths.")
        if self.kind not in INDEXED_ENTITY_KINDS:
            raise ValueError("The entity kind is not supported by the structural index.")
        if (self.end_line, self.end_column) < (self.start_line, self.start_column):
            raise ValueError("Code entity source span end must not precede its start.")
        if self.parent_entity_id == self.entity_id:
            raise ValueError("A code entity may not be its own parent.")
        return self

    def flow_source_span(self) -> FlowSourceSpan:
        return FlowSourceSpan(
            path=self.path,
            start_line=self.start_line,
            start_column=self.start_column,
            end_line=self.end_line,
            end_column=self.end_column,
            content_hash=self.content_hash,
            repository_revision=self.repository_revision,
        )


class RelationshipFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    relationship_id: str = Field(pattern=r"^rel_[A-Za-z0-9_-]+$")
    repository_revision: int = Field(ge=1)
    source_entity_id: FlowEntityId
    target_entity_id: FlowEntityId | None = None
    unresolved_key: str | None = Field(default=None, min_length=1, max_length=1_000)
    kind: FlowRelationshipKind
    resolution: FlowResolution
    evidence: tuple[FlowSourceSpan, ...] = Field(min_length=1, max_length=100)
    provenance: FlowProvenance
    conditional: bool = False
    asynchronous: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_fact(self) -> Self:
        has_target = self.target_entity_id is not None
        has_unresolved_key = self.unresolved_key is not None
        if has_target == has_unresolved_key:
            raise ValueError(
                "A relationship fact must have exactly one target entity or unresolved key."
            )
        if (self.resolution == FlowResolution.UNRESOLVED) != has_unresolved_key:
            raise ValueError("Only unresolved relationship facts may use an unresolved key.")
        if any(span.repository_revision != self.repository_revision for span in self.evidence):
            raise ValueError("Relationship evidence must match the fact revision.")
        expected_id = stable_relationship_id(
            source_entity_id=self.source_entity_id,
            target_entity_id=self.target_entity_id,
            unresolved_key=self.unresolved_key,
            kind=self.kind,
            evidence=self.evidence,
            provenance=self.provenance,
        )
        if self.relationship_id != expected_id:
            raise ValueError("The relationship ID does not match the fact identity.")
        return self

    @classmethod
    def create(
        cls,
        *,
        repository_revision: int,
        source_entity_id: FlowEntityId,
        kind: FlowRelationshipKind,
        resolution: FlowResolution,
        evidence: Sequence[FlowSourceSpan],
        provenance: FlowProvenance,
        target_entity_id: FlowEntityId | None = None,
        unresolved_key: str | None = None,
        conditional: bool = False,
        asynchronous: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> Self:
        normalized_evidence = tuple(evidence)
        return cls(
            relationship_id=stable_relationship_id(
                source_entity_id=source_entity_id,
                target_entity_id=target_entity_id,
                unresolved_key=unresolved_key,
                kind=kind,
                evidence=normalized_evidence,
                provenance=provenance,
            ),
            repository_revision=repository_revision,
            source_entity_id=source_entity_id,
            target_entity_id=target_entity_id,
            unresolved_key=unresolved_key,
            kind=kind,
            resolution=resolution,
            evidence=normalized_evidence,
            provenance=provenance,
            conditional=conditional,
            asynchronous=asynchronous,
            metadata=metadata or {},
        )

    def at_repository_revision(self, repository_revision: int) -> Self:
        evidence = tuple(
            span.model_copy(update={"repository_revision": repository_revision})
            for span in self.evidence
        )
        return type(self).create(
            repository_revision=repository_revision,
            source_entity_id=self.source_entity_id,
            target_entity_id=self.target_entity_id,
            unresolved_key=self.unresolved_key,
            kind=self.kind,
            resolution=self.resolution,
            evidence=evidence,
            provenance=self.provenance,
            conditional=self.conditional,
            asynchronous=self.asynchronous,
            metadata=dict(self.metadata),
        )


def stable_relationship_id(
    *,
    source_entity_id: str,
    target_entity_id: str | None,
    unresolved_key: str | None,
    kind: FlowRelationshipKind,
    evidence: Sequence[FlowSourceSpan],
    provenance: FlowProvenance,
) -> str:
    target_identity = target_entity_id or f"unresolved:{unresolved_key or ''}"
    evidence_identity = sorted(
        (
            span.path,
            span.start_line,
            span.start_column,
            span.end_line,
            span.end_column,
            span.content_hash,
            span.repository_revision,
        )
        for span in evidence
    )
    identity = json.dumps(
        [
            source_entity_id,
            target_identity,
            kind.value,
            evidence_identity,
            provenance.extractor,
            provenance.extractor_version,
            provenance.rule_id,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return f"rel_{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


class StructuralIndex:
    def __init__(
        self,
        root: Path,
        repository_revision: int,
        entities: list[CodeEntity] | tuple[CodeEntity, ...],
        relationships: list[RelationshipFact] | tuple[RelationshipFact, ...] = (),
    ) -> None:
        if repository_revision < 1:
            raise ValueError("The repository revision must be positive.")
        self.root = root.expanduser().resolve()
        self.repository_revision = repository_revision
        self.entities = tuple(entities)
        self.relationships = tuple(
            sorted(relationships, key=lambda relationship: relationship.relationship_id)
        )
        self._by_id: dict[str, CodeEntity] = {}
        self._by_path: dict[str, list[CodeEntity]] = defaultdict(list)
        self._relationships_by_id: dict[str, RelationshipFact] = {}
        self._relationship_signals_by_id: dict[str, RelationshipSignals] = {}
        self._outgoing: dict[str, list[RelationshipFact]] = defaultdict(list)
        self._incoming: dict[str, list[RelationshipFact]] = defaultdict(list)
        for entity in self.entities:
            if entity.repository_revision != repository_revision:
                raise ValueError("Every code entity must match the index revision.")
            if entity.entity_id in self._by_id:
                raise ValueError("Code entity IDs must be unique within an index.")
            self._by_id[entity.entity_id] = entity
            self._by_path[entity.path].append(entity)
        for entity in self.entities:
            if entity.parent_entity_id is not None and entity.parent_entity_id not in self._by_id:
                raise ValueError("Code entity parents must resolve within the index.")
            seen = {entity.entity_id}
            parent_id = entity.parent_entity_id
            while parent_id is not None:
                if parent_id in seen:
                    raise ValueError("Code entity containment must not contain cycles.")
                seen.add(parent_id)
                parent_id = self._by_id[parent_id].parent_entity_id
        for path_entities in self._by_path.values():
            path_entities.sort(key=_entity_source_order)

        hashes_by_path = {
            entity.path: entity.content_hash
            for entity in self.entities
            if entity.kind == FlowEntityKind.FILE
        }
        for relationship in self.relationships:
            if relationship.repository_revision != repository_revision:
                raise ValueError("Every relationship fact must match the index revision.")
            expected_relationship_id = stable_relationship_id(
                source_entity_id=relationship.source_entity_id,
                target_entity_id=relationship.target_entity_id,
                unresolved_key=relationship.unresolved_key,
                kind=relationship.kind,
                evidence=relationship.evidence,
                provenance=relationship.provenance,
            )
            if relationship.relationship_id != expected_relationship_id:
                raise ValueError("Relationship IDs must match their fact identity.")
            if relationship.relationship_id in self._relationships_by_id:
                raise ValueError("Relationship IDs must be unique within an index.")
            if relationship.source_entity_id not in self._by_id:
                raise ValueError("Relationship sources must resolve within the index.")
            if (
                relationship.target_entity_id is not None
                and relationship.target_entity_id not in self._by_id
            ):
                raise ValueError("Resolved relationship targets must resolve within the index.")
            if any(
                hashes_by_path.get(span.path) != span.content_hash for span in relationship.evidence
            ):
                raise ValueError("Relationship evidence must match a file in the structural index.")
            self._relationships_by_id[relationship.relationship_id] = relationship
            self._outgoing[relationship.source_entity_id].append(relationship)
            if relationship.target_entity_id is not None:
                self._incoming[relationship.target_entity_id].append(relationship)
        project_modules = {
            entity.module for entity in self.entities if entity.kind == FlowEntityKind.MODULE
        }
        for relationship in self.relationships:
            signals = classify_relationship_signals(
                relationship,
                target_entity=(
                    self._by_id.get(relationship.target_entity_id)
                    if relationship.target_entity_id is not None
                    else None
                ),
                project_modules=project_modules,
            )
            self._relationship_signals_by_id[relationship.relationship_id] = signals

    def get_entity(self, entity_id: str) -> CodeEntity | None:
        return self._by_id.get(entity_id)

    def get_relationship(self, relationship_id: str) -> RelationshipFact | None:
        return self._relationships_by_id.get(relationship_id)

    def get_relationship_signals(self, relationship_id: str) -> RelationshipSignals | None:
        return self._relationship_signals_by_id.get(relationship_id)

    def outgoing_relationships(
        self,
        entity_id: str,
        *,
        kinds: Collection[FlowRelationshipKind] | None = None,
    ) -> tuple[RelationshipFact, ...]:
        relationships = self._outgoing.get(entity_id, ())
        if kinds is None:
            return tuple(relationships)
        selected_kinds = set(kinds)
        return tuple(
            relationship for relationship in relationships if relationship.kind in selected_kinds
        )

    def incoming_relationships(
        self,
        entity_id: str,
        *,
        kinds: Collection[FlowRelationshipKind] | None = None,
    ) -> tuple[RelationshipFact, ...]:
        relationships = self._incoming.get(entity_id, ())
        if kinds is None:
            return tuple(relationships)
        selected_kinds = set(kinds)
        return tuple(
            relationship for relationship in relationships if relationship.kind in selected_kinds
        )

    def unresolved_relationships(
        self,
        entity_id: str | None = None,
    ) -> tuple[RelationshipFact, ...]:
        relationships = (
            self.relationships if entity_id is None else self._outgoing.get(entity_id, ())
        )
        return tuple(
            relationship
            for relationship in relationships
            if relationship.resolution == FlowResolution.UNRESOLVED
        )

    def get_file_entities(self, path: str) -> tuple[CodeEntity, ...]:
        return tuple(self._by_path.get(_normalize_relative_path(path), ()))

    def find_entity_at_location(self, path: str, line: int) -> CodeEntity | None:
        if line < 1:
            return None
        candidates = [
            entity
            for entity in self._by_path.get(_normalize_relative_path(path), ())
            if entity.start_line <= line <= entity.end_line
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda entity: _location_rank(entity, self._by_id))

    def search_entities(self, query: str, *, limit: int = 20) -> tuple[CodeEntity, ...]:
        terms = _search_terms(query)
        if not terms or limit < 1:
            return ()
        scored = [
            (_entity_search_score(entity, terms, query.strip().lower()), entity)
            for entity in self.entities
        ]
        matches = [(score, entity) for score, entity in scored if score > 0]
        matches.sort(
            key=lambda item: (
                -item[0],
                item[1].qualified_name.lower(),
                item[1].path.lower(),
                item[1].entity_id,
            )
        )
        return tuple(entity for _, entity in matches[: min(limit, 100)])


class CodeEntityIndex(StructuralIndex):
    """Backward-compatible name for callers that only need entity lookup."""


def extract_python_entities(
    root: Path,
    relative_path: str,
    text: str,
    content_hash: str,
    repository_revision: int,
) -> tuple[CodeEntity, ...]:
    normalized_path = _normalize_relative_path(relative_path)
    module = _python_module_identity(normalized_path)
    workspace_identity = root.expanduser().resolve().as_posix()
    end_line, end_column = _file_end(text)
    file_entity_id = _stable_entity_id(
        workspace_identity,
        "python",
        module,
        normalized_path,
        FlowEntityKind.FILE,
    )
    file_entity = CodeEntity(
        entity_id=file_entity_id,
        kind=FlowEntityKind.FILE,
        language="python",
        path=normalized_path,
        module=module,
        name=PurePosixPath(normalized_path).name,
        qualified_name=normalized_path,
        start_line=1,
        start_column=0,
        end_line=end_line,
        end_column=end_column,
        content_hash=content_hash,
        repository_revision=repository_revision,
    )
    module_entity_id = _stable_entity_id(
        workspace_identity,
        "python",
        module,
        module,
        FlowEntityKind.MODULE,
    )
    module_entity = CodeEntity(
        entity_id=module_entity_id,
        kind=FlowEntityKind.MODULE,
        language="python",
        path=normalized_path,
        module=module,
        name=module.rpartition(".")[2],
        qualified_name=module,
        parent_entity_id=file_entity_id,
        start_line=1,
        start_column=0,
        end_line=end_line,
        end_column=end_column,
        content_hash=content_hash,
        repository_revision=repository_revision,
    )
    entities = [file_entity, module_entity]
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return tuple(entities)
    extractor = _PythonEntityExtractor(
        workspace_identity=workspace_identity,
        path=normalized_path,
        module=module,
        content_hash=content_hash,
        repository_revision=repository_revision,
        module_entity=module_entity,
    )
    extractor.visit(tree)
    entities.extend(extractor.entities)
    return tuple(entities)


class _PythonEntityExtractor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        workspace_identity: str,
        path: str,
        module: str,
        content_hash: str,
        repository_revision: int,
        module_entity: CodeEntity,
    ) -> None:
        self.workspace_identity = workspace_identity
        self.path = path
        self.module = module
        self.content_hash = content_hash
        self.repository_revision = repository_revision
        self.entities: list[CodeEntity] = []
        self._scopes = [module_entity]
        self._occurrences: dict[tuple[str, FlowEntityKind, str], int] = defaultdict(int)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_definition(node, FlowEntityKind.CLASS)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        kind = (
            FlowEntityKind.METHOD
            if self._scopes[-1].kind == FlowEntityKind.CLASS
            else FlowEntityKind.FUNCTION
        )
        self._visit_definition(node, kind)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        kind = (
            FlowEntityKind.METHOD
            if self._scopes[-1].kind == FlowEntityKind.CLASS
            else FlowEntityKind.FUNCTION
        )
        self._visit_definition(node, kind)

    def _visit_definition(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        kind: FlowEntityKind,
    ) -> None:
        parent = self._scopes[-1]
        qualified_name = (
            node.name
            if parent.kind == FlowEntityKind.MODULE
            else f"{parent.qualified_name}.{node.name}"
        )
        occurrence_key = (parent.entity_id, kind, node.name)
        self._occurrences[occurrence_key] += 1
        occurrence = self._occurrences[occurrence_key]
        discriminator = "" if occurrence == 1 else f"duplicate:{occurrence}"
        entity_id = _stable_entity_id(
            self.workspace_identity,
            "python",
            self.module,
            qualified_name,
            kind,
            discriminator,
        )
        start_line, start_column = _definition_start(node)
        entity = CodeEntity(
            entity_id=entity_id,
            kind=kind,
            language="python",
            path=self.path,
            module=self.module,
            name=node.name,
            qualified_name=qualified_name,
            parent_entity_id=parent.entity_id,
            start_line=start_line,
            start_column=start_column,
            end_line=node.end_lineno or node.lineno,
            end_column=node.end_col_offset or node.col_offset,
            content_hash=self.content_hash,
            repository_revision=self.repository_revision,
        )
        self.entities.append(entity)
        self._scopes.append(entity)
        self.generic_visit(node)
        self._scopes.pop()


def _stable_entity_id(
    workspace_identity: str,
    language: str,
    module: str,
    qualified_name: str,
    kind: FlowEntityKind,
    discriminator: str = "",
) -> str:
    identity = "\0".join(
        (workspace_identity, language, module, qualified_name, kind.value, discriminator)
    )
    return f"ent_{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _normalize_relative_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if not normalized or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("A workspace-relative path is required.")
    return candidate.as_posix()


def _python_module_identity(path: str) -> str:
    candidate = PurePosixPath(path)
    parts = list(candidate.with_suffix("").parts)
    if parts[-1] == "__init__" and len(parts) > 1:
        parts.pop()
    return ".".join(parts)


def _file_end(text: str) -> tuple[int, int]:
    lines = text.splitlines()
    if not lines:
        return 1, 0
    return len(lines), len(lines[-1].encode("utf-8"))


def _definition_start(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[int, int]:
    if not node.decorator_list:
        return node.lineno, node.col_offset
    first = min(node.decorator_list, key=lambda decorator: (decorator.lineno, decorator.col_offset))
    return first.lineno, first.col_offset


def _entity_source_order(entity: CodeEntity) -> tuple[int, int, int, int, str]:
    kind_order = {
        FlowEntityKind.FILE: 0,
        FlowEntityKind.MODULE: 1,
        FlowEntityKind.CLASS: 2,
        FlowEntityKind.FUNCTION: 3,
        FlowEntityKind.METHOD: 3,
    }
    return (
        entity.start_line,
        entity.start_column,
        kind_order[entity.kind],
        -entity.end_line,
        entity.entity_id,
    )


def _location_rank(entity: CodeEntity, by_id: dict[str, CodeEntity]) -> tuple[int, int, int]:
    depth = 0
    parent_id = entity.parent_entity_id
    seen: set[str] = set()
    while parent_id is not None and parent_id not in seen:
        seen.add(parent_id)
        parent = by_id.get(parent_id)
        if parent is None:
            break
        depth += 1
        parent_id = parent.parent_entity_id
    span_lines = entity.end_line - entity.start_line
    span_columns = entity.end_column - entity.start_column if span_lines == 0 else 0
    return (-depth, span_lines, span_columns)


def _search_terms(query: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(re.findall(r"[a-z0-9_$]+", query.lower())))


def _entity_search_score(entity: CodeEntity, terms: tuple[str, ...], query: str) -> int:
    name = entity.name.lower()
    qualified_name = entity.qualified_name.lower()
    path = entity.path.lower()
    module = entity.module.lower()
    score = 0
    if query == name:
        score += 100
    if query == qualified_name:
        score += 90
    if query and query in qualified_name:
        score += 40
    if query and query in path:
        score += 30
    for term in terms:
        if term == name:
            score += 25
        elif term in name:
            score += 15
        if term in qualified_name:
            score += 8
        if term in module:
            score += 4
        if term in path:
            score += 3
    return score
