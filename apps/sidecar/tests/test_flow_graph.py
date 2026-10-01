from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sentia_sidecar.flow_graph import FlowTraversalPolicy, build_feature_flow
from sentia_sidecar.protocol import (
    FlowEntityKind,
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
def create_session() -> str:
    return "session"

def record_metric() -> None:
    pass

def final_response() -> str:
    return "done"

def login() -> str:
    return create_session()
"""

GENERATED_AT = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def extract(root: Path, source: str = SOURCE, revision: int = 1) -> tuple[CodeEntity, ...]:
    return extract_python_entities(
        root,
        "auth.py",
        source,
        hashlib.sha256(source.encode("utf-8")).hexdigest(),
        revision,
    )


def relationship(
    source: CodeEntity,
    *,
    target: CodeEntity | None = None,
    unresolved_key: str | None = None,
    kind: FlowRelationshipKind = FlowRelationshipKind.CALLS,
    resolution: FlowResolution = FlowResolution.STATICALLY_RESOLVED,
    rule_id: str = "python.call.name",
) -> RelationshipFact:
    return RelationshipFact.create(
        repository_revision=source.repository_revision,
        source_entity_id=source.entity_id,
        target_entity_id=target.entity_id if target is not None else None,
        unresolved_key=unresolved_key,
        kind=kind,
        resolution=resolution,
        evidence=(source.flow_source_span(),),
        provenance=FlowProvenance(
            extractor="sentia.python.ast",
            extractor_version="1.0.0",
            rule_id=rule_id,
        ),
    )


def selection(root: CodeEntity, revision: int = 1) -> FlowRootSelection:
    return FlowRootSelection(
        repository_revision=revision,
        root_entity_ids=[root.entity_id],
        rationale="The login function is the feature entry point.",
    )


def anchor_selection(*anchors: CodeEntity) -> FlowRootSelection:
    return FlowRootSelection(
        repository_revision=anchors[0].repository_revision,
        root_entity_ids=[anchor.entity_id for anchor in anchors],
        rationale="The selected entities are relevant feature anchors.",
    )


def test_local_relevance_prioritizes_feature_branch_and_collapses_the_rest(
    tmp_path: Path,
) -> None:
    entities = extract(tmp_path)
    by_name = {entity.qualified_name: entity for entity in entities}
    login = by_name["login"]
    create_session = by_name["create_session"]
    record_metric = by_name["record_metric"]
    relationships = (
        relationship(login, target=record_metric, rule_id="python.call.metric"),
        relationship(login, target=create_session, rule_id="python.call.session"),
    )
    index = StructuralIndex(tmp_path, 1, entities, relationships)

    graph = build_feature_flow(
        index,
        selection(login),
        question="How does login create a session?",
        policy=FlowTraversalPolicy(max_nodes=4, max_branches_per_node=1),
        generated_at=GENERATED_AT,
    )

    labels = {node.label for node in graph.nodes}
    assert "create_session" in labels
    assert "record_metric" not in labels
    assert len(graph.frontiers) == 1
    assert graph.frontiers[0].hidden_neighbor_count == 1
    login_node = next(node for node in graph.nodes if node.entity_id == login.entity_id)
    assert login_node.expandable is True
    assert login_node.hidden_neighbor_count == 1
    assert graph.warnings[0].code == "traversal_bounded"


def test_feature_flow_preserves_cycles_and_marks_depth_frontiers(tmp_path: Path) -> None:
    entities = extract(tmp_path)
    by_name = {entity.qualified_name: entity for entity in entities}
    login = by_name["login"]
    create_session = by_name["create_session"]
    final_response = by_name["final_response"]
    relationships = (
        relationship(login, target=create_session, rule_id="python.call.forward"),
        relationship(create_session, target=login, rule_id="python.call.cycle"),
        relationship(create_session, target=final_response, rule_id="python.call.finish"),
    )
    index = StructuralIndex(tmp_path, 1, entities, relationships)

    graph = build_feature_flow(
        index,
        selection(login),
        question="How does login work?",
        policy=FlowTraversalPolicy(max_depth=1),
        generated_at=GENERATED_AT,
    )

    visible_entities = {node.entity_id for node in graph.nodes}
    assert login.entity_id in visible_entities
    assert create_session.entity_id in visible_entities
    assert final_response.entity_id not in visible_entities
    projected_pairs = {
        (edge.source_node_id, edge.target_node_id)
        for edge in graph.edges
        if edge.kind == FlowRelationshipKind.CALLS
    }
    assert len(projected_pairs) == 2
    create_session_node = next(
        node for node in graph.nodes if node.entity_id == create_session.entity_id
    )
    assert create_session_node.hidden_neighbor_count == 1


def test_feature_flow_reserves_a_directed_path_between_anchors(tmp_path: Path) -> None:
    entities = extract(tmp_path)
    by_name = {entity.qualified_name: entity for entity in entities}
    login = by_name["login"]
    create_session = by_name["create_session"]
    final_response = by_name["final_response"]
    index = StructuralIndex(
        tmp_path,
        1,
        entities,
        (
            relationship(login, target=create_session, rule_id="python.call.bridge_start"),
            relationship(
                create_session,
                target=final_response,
                rule_id="python.call.bridge_finish",
            ),
        ),
    )

    graph = build_feature_flow(
        index,
        anchor_selection(login, final_response),
        question="How does login produce its final response?",
        policy=FlowTraversalPolicy(max_depth=0, max_nodes=5),
        generated_at=GENERATED_AT,
    )

    assert {login.entity_id, create_session.entity_id, final_response.entity_id} <= {
        node.entity_id for node in graph.nodes
    }
    assert len([edge for edge in graph.edges if edge.kind == FlowRelationshipKind.CALLS]) == 2
    assert "disconnected_feature_anchors" not in {warning.code for warning in graph.warnings}
    assert graph.root_entity_ids == [login.entity_id]


def test_feature_flow_labels_a_factual_weak_anchor_connection(tmp_path: Path) -> None:
    entities = extract(tmp_path)
    by_name = {entity.qualified_name: entity for entity in entities}
    login = by_name["login"]
    record_metric = by_name["record_metric"]
    final_response = by_name["final_response"]
    index = StructuralIndex(
        tmp_path,
        1,
        entities,
        (
            relationship(record_metric, target=login, rule_id="python.call.shared_first"),
            relationship(
                record_metric,
                target=final_response,
                rule_id="python.call.shared_second",
            ),
        ),
    )

    graph = build_feature_flow(
        index,
        anchor_selection(login, final_response),
        question="How are login and the response structurally related?",
        generated_at=GENERATED_AT,
    )

    warning_codes = {warning.code for warning in graph.warnings}
    assert "weak_anchor_connection" in warning_codes
    assert "disconnected_feature_anchors" not in warning_codes
    assert record_metric.entity_id in {node.entity_id for node in graph.nodes}
    assert graph.root_entity_ids == [record_metric.entity_id]


def test_feature_flow_explains_disconnected_anchors(tmp_path: Path) -> None:
    entities = extract(tmp_path)
    by_name = {entity.qualified_name: entity for entity in entities}
    login = by_name["login"]
    final_response = by_name["final_response"]
    index = StructuralIndex(tmp_path, 1, entities)

    graph = build_feature_flow(
        index,
        anchor_selection(login, final_response),
        question="How does login produce its final response?",
        generated_at=GENERATED_AT,
    )

    warning = next(
        warning for warning in graph.warnings if warning.code == "disconnected_feature_anchors"
    )
    assert set(warning.entity_ids) == {login.entity_id, final_response.entity_id}
    assert "did not invent" in warning.message


def test_feature_flow_does_not_render_half_of_an_anchor_bridge(tmp_path: Path) -> None:
    entities = extract(tmp_path)
    by_name = {entity.qualified_name: entity for entity in entities}
    login = by_name["login"]
    create_session = by_name["create_session"]
    final_response = by_name["final_response"]
    index = StructuralIndex(
        tmp_path,
        1,
        entities,
        (
            relationship(login, target=create_session, rule_id="python.call.bridge_start"),
            relationship(
                create_session,
                target=final_response,
                rule_id="python.call.bridge_finish",
            ),
        ),
    )

    graph = build_feature_flow(
        index,
        anchor_selection(login, final_response),
        question="How does login produce its final response?",
        policy=FlowTraversalPolicy(max_depth=0, max_nodes=4),
        generated_at=GENERATED_AT,
    )

    warning_codes = {warning.code for warning in graph.warnings}
    assert "anchor_bridge_budget_reached" in warning_codes
    assert "disconnected_feature_anchors" in warning_codes
    assert create_session.entity_id not in {node.entity_id for node in graph.nodes}


def test_feature_flow_projects_unresolved_calls_as_terminal_nodes(tmp_path: Path) -> None:
    entities = extract(tmp_path)
    login = next(entity for entity in entities if entity.qualified_name == "login")
    unresolved = relationship(
        login,
        unresolved_key="plugin.resolve",
        resolution=FlowResolution.UNRESOLVED,
        rule_id="python.call.dynamic",
    )
    index = StructuralIndex(tmp_path, 1, entities, (unresolved,))

    graph = build_feature_flow(
        index,
        selection(login),
        question="How does the login plugin resolve?",
        policy=FlowTraversalPolicy(max_nodes=4),
        generated_at=GENERATED_AT,
    )

    unresolved_node = next(node for node in graph.nodes if node.kind == FlowEntityKind.UNRESOLVED)
    assert unresolved_node.label == "plugin.resolve"
    assert unresolved_node.expandable is False
    assert len(graph.edges) == 1
    assert graph.edges[0].resolution == FlowResolution.UNRESOLVED
    assert graph.edges[0].target_node_id == unresolved_node.id


def test_imports_are_metadata_and_do_not_drive_feature_traversal(tmp_path: Path) -> None:
    entities = extract(tmp_path)
    by_name = {entity.qualified_name: entity for entity in entities}
    login = by_name["login"]
    record_metric = by_name["record_metric"]
    imported = relationship(
        login,
        target=record_metric,
        kind=FlowRelationshipKind.IMPORTS,
        rule_id="python.import.name",
    )
    index = StructuralIndex(tmp_path, 1, entities, (imported,))

    graph = build_feature_flow(
        index,
        selection(login),
        question="How does login work?",
        generated_at=GENERATED_AT,
    )

    assert record_metric.entity_id not in {node.entity_id for node in graph.nodes}
    assert graph.edges == []
    assert graph.frontiers == []


def test_feature_flow_topology_and_map_identity_are_deterministic(tmp_path: Path) -> None:
    entities = extract(tmp_path)
    by_name = {entity.qualified_name: entity for entity in entities}
    login = by_name["login"]
    fact = relationship(login, target=by_name["create_session"])
    index = StructuralIndex(tmp_path, 1, entities, (fact,))

    first = build_feature_flow(
        index,
        selection(login),
        question="How does login create a session?",
        generated_at=GENERATED_AT,
    )
    second = build_feature_flow(
        index,
        selection(login),
        question="How does login create a session?",
        generated_at=GENERATED_AT,
    )

    assert first == second
    assert first.map_id == second.map_id


def test_feature_flow_labels_edge_budget_truncation(tmp_path: Path) -> None:
    entities = extract(tmp_path)
    by_name = {entity.qualified_name: entity for entity in entities}
    login = by_name["login"]
    create_session = by_name["create_session"]
    relationships = (
        relationship(login, target=create_session, rule_id="python.call.first"),
        relationship(login, target=create_session, rule_id="python.call.second"),
    )
    index = StructuralIndex(tmp_path, 1, entities, relationships)

    graph = build_feature_flow(
        index,
        selection(login),
        question="How does login create a session?",
        policy=FlowTraversalPolicy(max_edges=1),
        generated_at=GENERATED_AT,
    )

    assert len(graph.edges) == 1
    assert [warning.code for warning in graph.warnings] == ["edge_budget_reached"]


def test_feature_flow_rejects_stale_roots_and_impossible_root_budget(tmp_path: Path) -> None:
    entities = extract(tmp_path)
    login = next(entity for entity in entities if entity.qualified_name == "login")
    index = StructuralIndex(tmp_path, 1, entities)

    with pytest.raises(ValueError, match="does not match"):
        build_feature_flow(
            index,
            selection(login, revision=2),
            question="How does login work?",
        )

    with pytest.raises(ValueError, match="cannot represent"):
        build_feature_flow(
            index,
            selection(login),
            question="How does login work?",
            policy=FlowTraversalPolicy(max_nodes=1),
        )
