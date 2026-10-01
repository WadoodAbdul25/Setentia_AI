from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sentia_sidecar.anchor_reconciliation import (
    AnchorReconciliationPolicy,
    reconcile_anchors,
)
from sentia_sidecar.protocol import (
    FlowProvenance,
    FlowRelationshipKind,
    FlowResolution,
    FlowRootSelection,
)
from sentia_sidecar.structural_index import (
    CodeEntity,
    RelationshipFact,
    StructuralIndex,
    extract_python_entities,
)

SOURCE = """\
def A():
    pass

def B():
    pass

def C():
    pass

def X():
    pass

def Y():
    pass

def Z():
    pass

def X1():
    pass

def X2():
    pass

def X3():
    pass

def X4():
    pass
"""


def entities(root: Path, *, revision: int = 1) -> dict[str, CodeEntity]:
    extracted = extract_python_entities(
        root,
        "feature.py",
        SOURCE,
        hashlib.sha256(SOURCE.encode()).hexdigest(),
        revision,
    )
    return {entity.qualified_name: entity for entity in extracted}


def fact(
    source: CodeEntity,
    target: CodeEntity | None = None,
    *,
    unresolved_key: str | None = None,
    rule: str,
) -> RelationshipFact:
    return RelationshipFact.create(
        repository_revision=source.repository_revision,
        source_entity_id=source.entity_id,
        target_entity_id=target.entity_id if target is not None else None,
        unresolved_key=unresolved_key,
        kind=FlowRelationshipKind.CALLS,
        resolution=(
            FlowResolution.STATICALLY_RESOLVED if target is not None else FlowResolution.UNRESOLVED
        ),
        evidence=(source.flow_source_span(),),
        provenance=FlowProvenance(
            extractor="sentia.test.ast",
            extractor_version="1.0.0",
            rule_id=rule,
        ),
    )


def selection(
    *selected: CodeEntity,
    revision: int | None = None,
    rationale: str = "anchors",
) -> FlowRootSelection:
    return FlowRootSelection(
        repository_revision=revision or selected[0].repository_revision,
        root_entity_ids=[entity.entity_id for entity in selected],
        rationale=rationale,
    )


def test_direct_anchor_chain_preserves_evidence_and_has_one_structural_entry(
    tmp_path: Path,
) -> None:
    by_name = entities(tmp_path)
    relationships = (
        fact(by_name["A"], by_name["B"], rule="a_b"),
        fact(by_name["B"], by_name["C"], rule="b_c"),
    )
    index = StructuralIndex(tmp_path, 1, tuple(by_name.values()), relationships)

    result = reconcile_anchors(index, selection(by_name["A"], by_name["B"], by_name["C"]))

    assert len(result.components) == 1
    assert set(result.components[0]) == {
        by_name["A"].entity_id,
        by_name["B"].entity_id,
        by_name["C"].entity_id,
    }
    assert {item.relationship_id for item in result.direct_anchor_relationships} == {
        relationship.relationship_id for relationship in relationships
    }
    assert result.structural_entry_entity_ids == (by_name["A"].entity_id,)
    assert result.expansion_seed_entity_ids == (by_name["C"].entity_id,)


def test_single_intermediate_is_retained_as_bridge_evidence(tmp_path: Path) -> None:
    by_name = entities(tmp_path)
    relationships = (
        fact(by_name["A"], by_name["X"], rule="a_x"),
        fact(by_name["X"], by_name["B"], rule="x_b"),
    )
    index = StructuralIndex(tmp_path, 1, tuple(by_name.values()), relationships)

    result = reconcile_anchors(index, selection(by_name["A"], by_name["B"]))

    assert len(result.components) == 1
    assert set(result.components[0]) == {by_name["A"].entity_id, by_name["B"].entity_id}
    assert by_name["X"].entity_id in result.intermediate_entity_ids
    path = result.backbone_paths[0]
    assert path.relationships == relationships
    assert path.directed is True


def test_multi_hop_path_preserves_relationship_direction(tmp_path: Path) -> None:
    by_name = entities(tmp_path)
    relationships = (
        fact(by_name["A"], by_name["X"], rule="a_x"),
        fact(by_name["X"], by_name["Y"], rule="x_y"),
        fact(by_name["Y"], by_name["B"], rule="y_b"),
    )
    index = StructuralIndex(tmp_path, 1, tuple(by_name.values()), relationships)

    result = reconcile_anchors(index, selection(by_name["A"], by_name["B"]))

    path = result.backbone_paths[0]
    assert path.entity_ids == tuple(by_name[name].entity_id for name in ("A", "X", "Y", "B"))
    assert tuple(
        (item.source_entity_id, item.target_entity_id) for item in path.relationships
    ) == tuple(
        (relationships[offset].source_entity_id, relationships[offset].target_entity_id)
        for offset in range(3)
    )


def test_common_caller_is_a_request_local_coordinator_candidate(tmp_path: Path) -> None:
    by_name = entities(tmp_path)
    relationships = (
        fact(by_name["X"], by_name["A"], rule="x_a"),
        fact(by_name["X"], by_name["B"], rule="x_b"),
    )
    index = StructuralIndex(tmp_path, 1, tuple(by_name.values()), relationships)

    result = reconcile_anchors(index, selection(by_name["A"], by_name["B"]))

    coordinator = result.coordinator_candidates[0]
    assert coordinator.entity_id == by_name["X"].entity_id
    assert set(coordinator.directly_called_anchor_ids) == {
        by_name["A"].entity_id,
        by_name["B"].entity_id,
    }
    assert set(coordinator.evidence_relationship_ids) == {
        relationship.relationship_id for relationship in relationships
    }
    assert result.structural_entry_entity_ids == (by_name["X"].entity_id,)
    assert not hasattr(by_name["X"], "is_feature_step")


def test_three_anchor_coordinator_records_all_direct_evidence(tmp_path: Path) -> None:
    by_name = entities(tmp_path)
    relationships = tuple(
        fact(by_name["X"], by_name[name], rule=f"x_{name.lower()}") for name in ("A", "B", "C")
    )
    index = StructuralIndex(tmp_path, 1, tuple(by_name.values()), relationships)

    result = reconcile_anchors(index, selection(by_name["A"], by_name["B"], by_name["C"]))

    coordinator = result.coordinator_candidates[0]
    assert coordinator.entity_id == by_name["X"].entity_id
    assert len(coordinator.directly_called_anchor_ids) == 3
    assert len(coordinator.evidence_relationship_ids) == 3


def test_partial_coordinator_and_downstream_bridge_form_one_component(tmp_path: Path) -> None:
    by_name = entities(tmp_path)
    relationships = (
        fact(by_name["X"], by_name["A"], rule="x_a"),
        fact(by_name["X"], by_name["B"], rule="x_b"),
        fact(by_name["B"], by_name["Y"], rule="b_y"),
        fact(by_name["Y"], by_name["C"], rule="y_c"),
    )
    index = StructuralIndex(tmp_path, 1, tuple(by_name.values()), relationships)

    result = reconcile_anchors(index, selection(by_name["A"], by_name["B"], by_name["C"]))

    assert len(result.components) == 1
    assert {by_name["X"].entity_id, by_name["Y"].entity_id} <= set(result.intermediate_entity_ids)
    assert result.coordinator_candidates[0].entity_id == by_name["X"].entity_id


def test_shared_callee_is_not_misclassified_as_upstream_coordinator(tmp_path: Path) -> None:
    by_name = entities(tmp_path)
    relationships = (
        fact(by_name["A"], by_name["X"], rule="a_x"),
        fact(by_name["B"], by_name["X"], rule="b_x"),
    )
    index = StructuralIndex(tmp_path, 1, tuple(by_name.values()), relationships)

    result = reconcile_anchors(index, selection(by_name["A"], by_name["B"]))

    assert result.coordinator_candidates == ()
    assert result.shared_callee_candidates[0].entity_id == by_name["X"].entity_id
    assert set(result.structural_entry_entity_ids) == {
        by_name["A"].entity_id,
        by_name["B"].entity_id,
    }
    assert all(
        relationship.target_entity_id == by_name["X"].entity_id
        for relationship in result.shared_callee_candidates[0].relationships
    )


def test_disconnected_anchors_remain_separate_without_an_invented_bridge(tmp_path: Path) -> None:
    by_name = entities(tmp_path)
    relationships = (
        fact(by_name["A"], by_name["X"], rule="a_x"),
        fact(by_name["B"], by_name["Y"], rule="b_y"),
    )
    index = StructuralIndex(tmp_path, 1, tuple(by_name.values()), relationships)

    result = reconcile_anchors(index, selection(by_name["A"], by_name["B"]))

    assert {component[0] for component in result.components} == {
        by_name["A"].entity_id,
        by_name["B"].entity_id,
    }
    assert result.connecting_paths == ()
    assert set(result.disconnected_anchor_entity_ids) == {
        by_name["A"].entity_id,
        by_name["B"].entity_id,
    }


def test_cycle_terminates_and_retains_a_valid_path(tmp_path: Path) -> None:
    by_name = entities(tmp_path)
    relationships = (
        fact(by_name["A"], by_name["X"], rule="a_x"),
        fact(by_name["X"], by_name["B"], rule="x_b"),
        fact(by_name["B"], by_name["Y"], rule="b_y"),
        fact(by_name["Y"], by_name["A"], rule="y_a"),
    )
    index = StructuralIndex(tmp_path, 1, tuple(by_name.values()), relationships)

    result = reconcile_anchors(index, selection(by_name["A"], by_name["B"]))

    assert len(result.components) == 1
    assert result.backbone_paths
    assert result.diagnostics.visited_entities <= 2_000


def test_path_depth_limit_reports_truncation_without_false_connectivity(tmp_path: Path) -> None:
    by_name = entities(tmp_path)
    names = ("A", "X1", "X2", "X3", "X4", "B")
    relationships = tuple(
        fact(by_name[source], by_name[target], rule=f"{source}_{target}")
        for source, target in zip(names, names[1:], strict=False)
    )
    index = StructuralIndex(tmp_path, 1, tuple(by_name.values()), relationships)

    result = reconcile_anchors(
        index,
        selection(by_name["A"], by_name["B"]),
        policy=AnchorReconciliationPolicy(max_path_depth=3, max_upstream_depth=2),
    )

    assert len(result.components) == 2
    assert result.backbone_paths == ()
    assert "max_path_depth" in result.diagnostics.truncation_reasons


def test_multiple_paths_are_retained_and_shortest_path_deterministically_backbones(
    tmp_path: Path,
) -> None:
    by_name = entities(tmp_path)
    relationships = (
        fact(by_name["A"], by_name["X"], rule="a_x"),
        fact(by_name["X"], by_name["B"], rule="x_b"),
        fact(by_name["A"], by_name["Y"], rule="a_y"),
        fact(by_name["Y"], by_name["Z"], rule="y_z"),
        fact(by_name["Z"], by_name["B"], rule="z_b"),
    )
    index = StructuralIndex(tmp_path, 1, tuple(by_name.values()), relationships)

    first = reconcile_anchors(index, selection(by_name["A"], by_name["B"]))
    second = reconcile_anchors(index, selection(by_name["A"], by_name["B"]))

    assert first == second
    assert len(first.connecting_paths) == 2
    assert first.backbone_paths[0].entity_ids == tuple(
        by_name[name].entity_id for name in ("A", "X", "B")
    )


def test_unresolved_relationship_does_not_become_a_bridge(tmp_path: Path) -> None:
    by_name = entities(tmp_path)
    unresolved = fact(by_name["A"], unresolved_key="dynamic.B", rule="dynamic")
    index = StructuralIndex(tmp_path, 1, tuple(by_name.values()), (unresolved,))

    result = reconcile_anchors(index, selection(by_name["A"], by_name["B"]))

    assert len(result.components) == 2
    assert result.connecting_paths == ()
    assert unresolved.relationship_id not in result.backbone_relationship_ids


def test_revision_mismatch_is_rejected(tmp_path: Path) -> None:
    by_name = entities(tmp_path)
    index = StructuralIndex(tmp_path, 1, tuple(by_name.values()))

    with pytest.raises(ValueError, match="does not match"):
        reconcile_anchors(
            index,
            selection(by_name["A"], by_name["B"], revision=2),
        )


def test_result_is_provider_independent_and_ignores_semantic_rationale(tmp_path: Path) -> None:
    by_name = entities(tmp_path)
    relationship = fact(by_name["A"], by_name["B"], rule="a_b")
    index = StructuralIndex(tmp_path, 1, tuple(by_name.values()), (relationship,))

    claude_labeled = reconcile_anchors(
        index,
        selection(by_name["A"], by_name["B"], rationale="Claude rationale"),
    )
    codex_labeled = reconcile_anchors(
        index,
        selection(by_name["A"], by_name["B"], rationale="Codex rationale"),
    )

    assert claude_labeled == codex_labeled


def test_reconciliation_is_directly_callable_without_a_model_service(tmp_path: Path) -> None:
    by_name = entities(tmp_path)
    relationship = fact(by_name["A"], by_name["B"], rule="a_b")
    index = StructuralIndex(tmp_path, 1, tuple(by_name.values()), (relationship,))

    result = reconcile_anchors(index, selection(by_name["A"], by_name["B"]))

    assert result.repository_revision == index.repository_revision
    assert result.backbone_relationship_ids == {relationship.relationship_id}
