from __future__ import annotations

import sys
from collections.abc import Collection
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from sentia_sidecar.structural_index import CodeEntity, RelationshipFact


class TargetProvenance(StrEnum):
    PROJECT_OWNED = "project_owned"
    DEPENDENCY_OWNED = "dependency_owned"
    STANDARD_LIBRARY = "standard_library"
    UNRESOLVED = "unresolved"


class TargetProvenanceBasis(StrEnum):
    INDEXED_TARGET_ENTITY = "indexed_target_entity"
    STANDARD_LIBRARY_IMPORT = "standard_library_import"
    EXTERNAL_IMPORT = "external_import"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class RelationshipSignals(BaseModel):
    """Revision-bound deterministic evidence about one relationship fact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relationship_id: str = Field(pattern=r"^rel_[A-Za-z0-9_-]+$")
    repository_revision: int = Field(ge=1)
    target_provenance: TargetProvenance
    target_provenance_basis: TargetProvenanceBasis
    target_module: str | None = Field(default=None, min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_target_module(self) -> Self:
        import_bases = {
            TargetProvenanceBasis.STANDARD_LIBRARY_IMPORT,
            TargetProvenanceBasis.EXTERNAL_IMPORT,
        }
        if (self.target_provenance_basis in import_bases) != (self.target_module is not None):
            raise ValueError("Import-based target provenance must identify its target module.")
        return self


def classify_relationship_signals(
    relationship: RelationshipFact,
    *,
    target_entity: CodeEntity | None,
    project_modules: Collection[str],
) -> RelationshipSignals:
    """Classify target ownership from revision-bound structural evidence only."""

    if relationship.target_entity_id is not None and target_entity is not None:
        return RelationshipSignals(
            relationship_id=relationship.relationship_id,
            repository_revision=relationship.repository_revision,
            target_provenance=TargetProvenance.PROJECT_OWNED,
            target_provenance_basis=TargetProvenanceBasis.INDEXED_TARGET_ENTITY,
        )

    target_import = relationship.metadata.get("targetImport")
    if not isinstance(target_import, dict):
        return _unresolved_signals(relationship)
    module = target_import.get("module")
    relative = target_import.get("relative")
    if not isinstance(module, str) or not module or not isinstance(relative, bool):
        return _unresolved_signals(relationship)

    top_level_module = module.partition(".")[0]
    project_roots = {project_module.partition(".")[0] for project_module in project_modules}
    if relative or top_level_module in project_roots:
        return _unresolved_signals(relationship)
    if top_level_module in sys.stdlib_module_names:
        return RelationshipSignals(
            relationship_id=relationship.relationship_id,
            repository_revision=relationship.repository_revision,
            target_provenance=TargetProvenance.STANDARD_LIBRARY,
            target_provenance_basis=TargetProvenanceBasis.STANDARD_LIBRARY_IMPORT,
            target_module=module,
        )
    return RelationshipSignals(
        relationship_id=relationship.relationship_id,
        repository_revision=relationship.repository_revision,
        target_provenance=TargetProvenance.DEPENDENCY_OWNED,
        target_provenance_basis=TargetProvenanceBasis.EXTERNAL_IMPORT,
        target_module=module,
    )


def relationship_signals_metadata(signals: RelationshipSignals) -> dict[str, Any]:
    """Return the compact diagnostic form embedded in projected Flow edges."""

    payload: dict[str, Any] = {
        "targetProvenance": signals.target_provenance.value,
        "targetProvenanceBasis": signals.target_provenance_basis.value,
    }
    if signals.target_module is not None:
        payload["targetModule"] = signals.target_module
    return payload


def _unresolved_signals(relationship: RelationshipFact) -> RelationshipSignals:
    return RelationshipSignals(
        relationship_id=relationship.relationship_id,
        repository_revision=relationship.repository_revision,
        target_provenance=TargetProvenance.UNRESOLVED,
        target_provenance_basis=TargetProvenanceBasis.INSUFFICIENT_EVIDENCE,
    )
