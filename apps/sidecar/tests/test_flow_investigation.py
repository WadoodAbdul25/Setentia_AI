from __future__ import annotations

from pathlib import Path

from sentia_sidecar.flow_investigation import (
    MAX_COVERAGE_CANDIDATES,
    ModelCodexFeatureCoverage,
    feature_coverage_candidates,
    feature_coverage_prompt,
    merge_feature_coverage_selection,
    root_candidates,
)
from sentia_sidecar.protocol import FlowRootSelection
from sentia_sidecar.repository import (
    RepositoryContext,
    build_repository_manifest,
    build_selected_repository_context,
)
from sentia_sidecar.structural_index import CodeEntity, StructuralIndex


def _fixture(
    tmp_path: Path,
) -> tuple[StructuralIndex, RepositoryContext, dict[str, CodeEntity]]:
    (tmp_path / "feature.py").write_text(
        "def entry():\n"
        "    worker()\n\n"
        "def worker():\n"
        "    outcome()\n\n"
        "def outcome():\n"
        "    return None\n\n"
        "def alternate():\n"
        "    return None\n",
        encoding="utf-8",
    )
    manifest = build_repository_manifest(str(tmp_path))
    index = StructuralIndex(
        manifest.root,
        manifest.repository_revision,
        manifest.entities,
        manifest.relationships,
    )
    repository = build_selected_repository_context(manifest, [("feature.py", "full")])
    entities = {
        entity.name: entity
        for entity in index.entities
        if entity.name in {"entry", "worker", "outcome", "alternate"}
    }
    return index, repository, entities


def _selection(*entities: CodeEntity) -> FlowRootSelection:
    return FlowRootSelection(
        repository_revision=entities[0].repository_revision,
        root_entity_ids=[entity.entity_id for entity in entities],
        rationale="Initial implementation landmark.",
    )


def _review(
    additional_ids: list[str],
    *,
    sufficient: bool = False,
) -> ModelCodexFeatureCoverage:
    return ModelCodexFeatureCoverage.model_validate(
        {
            "coverageAssessment": {
                "sufficient": sufficient,
                "reason": "Check entry and outcome coverage.",
            },
            "additionalAnchorEntityIds": additional_ids,
            "unresolvedConcepts": ["notification outcome"],
            "rationale": "Add major feature landmarks.",
        }
    )


def test_feature_coverage_catalog_prioritizes_incoming_and_outgoing_neighbors(
    tmp_path: Path,
) -> None:
    index, repository, entities = _fixture(tmp_path)
    initial = _selection(entities["worker"])
    initial_candidates = root_candidates(index, repository, "worker feature")

    candidates = feature_coverage_candidates(
        index,
        repository,
        "worker feature",
        initial,
        initial_candidates,
    )
    context = feature_coverage_prompt(
        "worker feature",
        repository,
        index,
        initial,
        candidates,
        selection_report="Read the feature implementation.",
    )

    candidate_ids = [entity.entity_id for entity in context.candidates]
    assert candidate_ids[:3] == [
        entities["worker"].entity_id,
        entities["entry"].entity_id,
        entities["outcome"].entity_id,
    ]
    assert len(candidate_ids) <= MAX_COVERAGE_CANDIDATES
    assert '"incomingCount":1' in context.text
    assert '"outgoingCount":1' in context.text
    assert "Do not return relationships or graph edges" in context.text


def test_feature_coverage_merge_discards_invalid_and_duplicate_ids(tmp_path: Path) -> None:
    index, repository, entities = _fixture(tmp_path)
    initial = _selection(entities["worker"])
    candidates = feature_coverage_candidates(
        index,
        repository,
        "worker feature",
        initial,
        root_candidates(index, repository, "worker feature"),
    )

    merged = merge_feature_coverage_selection(
        initial,
        _review([entities["worker"].entity_id, "ent_invented"]),
        index,
        candidates,
    )

    assert merged.selection.root_entity_ids == [entities["worker"].entity_id]
    assert merged.accepted_entity_ids == ()
    assert merged.rejected_entity_ids == ("ent_invented",)
    assert merged.selection.unresolved_concepts == ["notification outcome"]


def test_feature_coverage_merge_respects_sufficiency_and_three_anchor_limit(
    tmp_path: Path,
) -> None:
    index, repository, entities = _fixture(tmp_path)
    initial = _selection(entities["worker"], entities["outcome"])
    candidates = feature_coverage_candidates(
        index,
        repository,
        "worker feature",
        initial,
        root_candidates(index, repository, "worker feature"),
    )

    sufficient = merge_feature_coverage_selection(
        initial,
        _review([entities["entry"].entity_id], sufficient=True),
        index,
        candidates,
    )
    bounded = merge_feature_coverage_selection(
        initial,
        _review([entities["entry"].entity_id, entities["alternate"].entity_id]),
        index,
        candidates,
    )

    assert sufficient.selection.root_entity_ids == initial.root_entity_ids
    assert sufficient.accepted_entity_ids == ()
    assert bounded.selection.root_entity_ids == [
        entities["worker"].entity_id,
        entities["outcome"].entity_id,
        entities["entry"].entity_id,
    ]
    assert bounded.accepted_entity_ids == (entities["entry"].entity_id,)
    assert bounded.rejected_entity_ids == (entities["alternate"].entity_id,)
