from __future__ import annotations

import hashlib
import heapq
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sentia_sidecar.anchor_reconciliation import (
    AnchorReconciliationResult,
    reconcile_anchors,
)
from sentia_sidecar.protocol import (
    TRAVERSABLE_FLOW_RELATIONSHIP_KINDS,
    FlowEdge,
    FlowEntityKind,
    FlowFrontier,
    FlowFrontierDirection,
    FlowNode,
    FlowRelationshipKind,
    FlowRootSelection,
    FlowSourceSpan,
    FlowWarning,
    SentiaFlowGraph,
)
from sentia_sidecar.relationship_signals import relationship_signals_metadata
from sentia_sidecar.structural_index import CodeEntity, RelationshipFact, StructuralIndex

TRAVERSABLE_RELATIONSHIP_KINDS = TRAVERSABLE_FLOW_RELATIONSHIP_KINDS

_RELATIONSHIP_PRIORITY = {
    FlowRelationshipKind.AWAITS: 120,
    FlowRelationshipKind.SENDS_HTTP_REQUEST: 115,
    FlowRelationshipKind.HANDLES_HTTP_REQUEST: 115,
    FlowRelationshipKind.EMITS_MESSAGE: 110,
    FlowRelationshipKind.HANDLES_MESSAGE: 110,
    FlowRelationshipKind.PUBLISHES_EVENT: 105,
    FlowRelationshipKind.CONSUMES_EVENT: 105,
    FlowRelationshipKind.CALLS: 100,
    FlowRelationshipKind.CONSTRUCTS: 95,
    FlowRelationshipKind.REGISTERS_HANDLER: 90,
}

_TERM_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9]*|[0-9]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_STOP_TERMS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "be",
    "by",
    "code",
    "does",
    "feature",
    "flow",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "which",
    "with",
}


@dataclass(frozen=True)
class FlowTraversalPolicy:
    max_depth: int = 5
    max_nodes: int = 50
    max_edges: int = 2_000
    max_branches_per_node: int = 8

    def __post_init__(self) -> None:
        if not 0 <= self.max_depth <= 25:
            raise ValueError("Flow traversal depth must be between 0 and 25.")
        if not 1 <= self.max_nodes <= 500:
            raise ValueError("Flow traversal node budget must be between 1 and 500.")
        if not 1 <= self.max_edges <= 2_000:
            raise ValueError("Flow traversal edge budget must be between 1 and 2000.")
        if not 1 <= self.max_branches_per_node <= 100:
            raise ValueError("Flow traversal branch budget must be between 1 and 100.")


@dataclass(frozen=True)
class _Neighbor:
    source_entity_id: str
    target_identity: str
    target_entity_id: str | None
    unresolved_key: str | None
    facts: tuple[RelationshipFact, ...]
    depth: int
    score: int


@dataclass
class _FrontierState:
    targets: dict[str, set[FlowRelationshipKind]]


@dataclass(frozen=True)
class _ReconciliationProjection:
    visible_entity_ids: frozenset[str]
    required_relationship_ids: frozenset[str]
    seed_entity_ids: frozenset[str]
    graph_root_entity_ids: tuple[str, ...]
    component_count: int
    weak_connection_used: bool
    budget_limited: bool
    search_bounded: bool


def build_feature_flow(
    index: StructuralIndex,
    selection: FlowRootSelection,
    *,
    question: str,
    candidate_identifiers: Sequence[str] = (),
    policy: FlowTraversalPolicy | None = None,
    reconciliation: AnchorReconciliationResult | None = None,
    generated_at: datetime | None = None,
) -> SentiaFlowGraph:
    """Project a bounded feature graph without asking a model to plan traversal."""

    if not question.strip():
        raise ValueError("A non-empty question is required to build a feature flow.")
    active_policy = policy or FlowTraversalPolicy()
    roots = _validate_roots(index, selection)
    active_reconciliation = reconciliation or reconcile_anchors(index, selection)
    _validate_reconciliation(index, selection, active_reconciliation)
    question_terms = _terms(question)
    candidate_terms = _terms(" ".join(candidate_identifiers))
    visible_entities: set[str] = set()
    visible_unresolved: dict[str, tuple[str, str, tuple[RelationshipFact, ...]]] = {}
    frontiers: dict[str, _FrontierState] = {}
    expanded: set[str] = set()
    queue: list[tuple[int, int, str, int, _Neighbor]] = []
    sequence = 0

    for root in roots:
        visible_entities.update(_ancestor_ids(index, root.entity_id))
    if len(visible_entities) > active_policy.max_nodes:
        raise ValueError("The node budget cannot represent the selected roots and their parents.")
    reconciliation_projection = _project_reconciliation(
        index,
        active_reconciliation,
        initially_visible=visible_entities,
        policy=active_policy,
    )
    visible_entities.update(reconciliation_projection.visible_entity_ids)

    def record_frontier(source_entity_id: str, neighbors: Iterable[_Neighbor]) -> None:
        state = frontiers.setdefault(
            source_entity_id,
            _FrontierState(targets={}),
        )
        for neighbor in neighbors:
            relationship_kinds = state.targets.setdefault(neighbor.target_identity, set())
            relationship_kinds.update(fact.kind for fact in neighbor.facts)

    def enqueue(source_entity_id: str, depth: int) -> None:
        nonlocal sequence
        if source_entity_id in expanded:
            return
        expanded.add(source_entity_id)
        neighbors = _ranked_neighbors(
            index,
            source_entity_id,
            depth=depth,
            question_terms=question_terms,
            candidate_terms=candidate_terms,
            visible_entities=visible_entities,
            visible_unresolved=visible_unresolved,
        )
        if not neighbors:
            return
        if depth >= active_policy.max_depth:
            record_frontier(source_entity_id, neighbors)
            return
        selected = neighbors[: active_policy.max_branches_per_node]
        record_frontier(source_entity_id, neighbors[active_policy.max_branches_per_node :])
        for neighbor in selected:
            sequence += 1
            heapq.heappush(
                queue,
                (-neighbor.score, neighbor.depth, neighbor.target_identity, sequence, neighbor),
            )

    for entity_id in sorted(reconciliation_projection.seed_entity_ids):
        enqueue(entity_id, 0)

    while queue:
        _, _, _, _, neighbor = heapq.heappop(queue)
        if _neighbor_is_visible(neighbor, visible_entities, visible_unresolved):
            continue
        node_cost = _neighbor_node_cost(index, neighbor, visible_entities)
        current_node_count = len(visible_entities) + len(visible_unresolved)
        if current_node_count + node_cost > active_policy.max_nodes:
            record_frontier(neighbor.source_entity_id, (neighbor,))
            continue
        if neighbor.target_entity_id is not None:
            visible_entities.update(_ancestor_ids(index, neighbor.target_entity_id))
            enqueue(neighbor.target_entity_id, neighbor.depth)
        else:
            unresolved_id = _unresolved_entity_id(
                neighbor.source_entity_id,
                neighbor.unresolved_key or "unresolved",
            )
            visible_unresolved[unresolved_id] = (
                neighbor.source_entity_id,
                neighbor.unresolved_key or "unresolved",
                neighbor.facts,
            )

    visible_target_ids = visible_entities | set(visible_unresolved)
    for state in frontiers.values():
        for target_identity in tuple(state.targets):
            if target_identity in visible_target_ids:
                state.targets.pop(target_identity)

    nodes = _project_nodes(index, visible_entities, visible_unresolved, frontiers)
    node_ids_by_entity = {node.entity_id: node.id for node in nodes}
    edges, edge_budget_reached = _project_edges(
        index,
        visible_entities,
        visible_unresolved,
        node_ids_by_entity,
        max_edges=active_policy.max_edges,
        required_relationship_ids=reconciliation_projection.required_relationship_ids,
    )
    projected_frontiers = _project_frontiers(frontiers, node_ids_by_entity)
    warnings = _projection_warnings(
        frontiers,
        edge_budget_reached=edge_budget_reached,
        anchor_ids=tuple(root.entity_id for root in roots),
        reconciliation=active_reconciliation,
        projection=reconciliation_projection,
    )
    map_id = _map_id(
        index=index,
        selection=selection,
        question_terms=question_terms,
        candidate_terms=candidate_terms,
        policy=active_policy,
        nodes=nodes,
        edges=edges,
    )
    return SentiaFlowGraph(
        schema_version=selection.schema_version,
        map_id=map_id,
        repository_revision=index.repository_revision,
        root_entity_ids=list(reconciliation_projection.graph_root_entity_ids),
        nodes=nodes,
        edges=edges,
        frontiers=projected_frontiers,
        warnings=warnings,
        generated_at=generated_at or datetime.now(UTC),
    )


def _validate_roots(
    index: StructuralIndex,
    selection: FlowRootSelection,
) -> tuple[CodeEntity, ...]:
    if selection.repository_revision != index.repository_revision:
        raise ValueError("The root selection does not match the structural index revision.")
    roots: list[CodeEntity] = []
    for entity_id in selection.root_entity_ids:
        entity = index.get_entity(entity_id)
        if entity is None:
            raise ValueError("Every selected Flow Map root must exist in the structural index.")
        roots.append(entity)
    return tuple(roots)


def _validate_reconciliation(
    index: StructuralIndex,
    selection: FlowRootSelection,
    reconciliation: AnchorReconciliationResult,
) -> None:
    if reconciliation.repository_revision != index.repository_revision:
        raise ValueError("Anchor reconciliation does not match the structural index revision.")
    if reconciliation.anchor_entity_ids != tuple(selection.root_entity_ids):
        raise ValueError("Anchor reconciliation does not match the final validated anchors.")


def _project_reconciliation(
    index: StructuralIndex,
    reconciliation: AnchorReconciliationResult,
    *,
    initially_visible: set[str],
    policy: FlowTraversalPolicy,
) -> _ReconciliationProjection:
    """Atomically reserve the deterministic backbone before optional expansion."""

    anchor_ids = reconciliation.anchor_entity_ids
    visible_entity_ids = set(initially_visible)
    required_relationship_ids: set[str] = set()
    parents = {anchor_id: anchor_id for anchor_id in anchor_ids}

    def find(anchor_id: str) -> str:
        current = anchor_id
        while parents[current] != current:
            current = parents[current]
        return current

    def union(first_anchor_id: str, second_anchor_id: str) -> None:
        first_root = find(first_anchor_id)
        second_root = find(second_anchor_id)
        if first_root == second_root:
            return
        if first_root < second_root:
            parents[second_root] = first_root
        else:
            parents[first_root] = second_root

    admitted_paths = []
    budget_limited = False
    for path in reconciliation.backbone_paths:
        candidate_entity_ids = {
            entity_id
            for entity_id in path.entity_ids
        }
        candidate_visible_ids = {
            ancestor_id
            for entity_id in candidate_entity_ids
            for ancestor_id in _ancestor_ids(index, entity_id)
        }
        candidate_relationship_ids = {
            fact.relationship_id for fact in path.relationships
        }
        if (
            len(visible_entity_ids | candidate_visible_ids) > policy.max_nodes
            or len(required_relationship_ids | candidate_relationship_ids) > policy.max_edges
        ):
            budget_limited = True
            continue

        visible_entity_ids.update(candidate_visible_ids)
        required_relationship_ids.update(candidate_relationship_ids)
        admitted_paths.append(path)
        union(path.source_anchor_id, path.target_anchor_id)

    component_count = len({find(anchor_id) for anchor_id in anchor_ids})
    if budget_limited:
        graph_root_entity_ids = anchor_ids
        seed_entity_ids = set(anchor_ids)
    else:
        graph_root_entity_ids = reconciliation.structural_entry_entity_ids
        seed_entity_ids = set(reconciliation.expansion_seed_entity_ids)
    seed_entity_ids &= visible_entity_ids
    if not seed_entity_ids:
        seed_entity_ids.update(anchor_ids)
    return _ReconciliationProjection(
        visible_entity_ids=frozenset(visible_entity_ids),
        required_relationship_ids=frozenset(required_relationship_ids),
        seed_entity_ids=frozenset(seed_entity_ids),
        graph_root_entity_ids=graph_root_entity_ids,
        component_count=component_count,
        weak_connection_used=any(not path.directed for path in admitted_paths),
        budget_limited=budget_limited,
        search_bounded=reconciliation.diagnostics.truncated,
    )


def _ranked_neighbors(
    index: StructuralIndex,
    source_entity_id: str,
    *,
    depth: int,
    question_terms: frozenset[str],
    candidate_terms: frozenset[str],
    visible_entities: set[str],
    visible_unresolved: dict[str, tuple[str, str, tuple[RelationshipFact, ...]]],
) -> tuple[_Neighbor, ...]:
    grouped: dict[str, list[RelationshipFact]] = defaultdict(list)
    targets: dict[str, tuple[str | None, str | None]] = {}
    for fact in index.outgoing_relationships(
        source_entity_id,
        kinds=TRAVERSABLE_RELATIONSHIP_KINDS,
    ):
        if fact.target_entity_id is not None:
            target_identity = fact.target_entity_id
            if target_identity in visible_entities:
                continue
            targets[target_identity] = (target_identity, None)
        else:
            unresolved_key = fact.unresolved_key or "unresolved"
            target_identity = _unresolved_entity_id(source_entity_id, unresolved_key)
            if target_identity in visible_unresolved:
                continue
            targets[target_identity] = (None, unresolved_key)
        grouped[target_identity].append(fact)

    neighbors: list[_Neighbor] = []
    source = index.get_entity(source_entity_id)
    for target_identity, facts in grouped.items():
        neighbor_target_entity_id, neighbor_unresolved_key = targets[target_identity]
        target = (
            index.get_entity(neighbor_target_entity_id)
            if neighbor_target_entity_id is not None
            else None
        )
        score = max(_RELATIONSHIP_PRIORITY[fact.kind] for fact in facts)
        target_terms = (
            _entity_terms(target) if target is not None else _terms(neighbor_unresolved_key or "")
        )
        score += 30 * len(question_terms & target_terms)
        score += 15 * len(candidate_terms & target_terms)
        if source is not None and target is not None and source.module == target.module:
            score += 10
        if target is not None:
            fan_out = len(
                {
                    fact.target_entity_id or fact.unresolved_key
                    for fact in index.outgoing_relationships(
                        target.entity_id,
                        kinds=TRAVERSABLE_RELATIONSHIP_KINDS,
                    )
                }
            )
            score -= min(fan_out, 20)
        neighbors.append(
            _Neighbor(
                source_entity_id=source_entity_id,
                target_identity=target_identity,
                target_entity_id=neighbor_target_entity_id,
                unresolved_key=neighbor_unresolved_key,
                facts=tuple(facts),
                depth=depth + 1,
                score=score,
            )
        )
    neighbors.sort(key=lambda neighbor: (-neighbor.score, neighbor.target_identity))
    return tuple(neighbors)


def _neighbor_is_visible(
    neighbor: _Neighbor,
    visible_entities: set[str],
    visible_unresolved: dict[str, tuple[str, str, tuple[RelationshipFact, ...]]],
) -> bool:
    if neighbor.target_entity_id is not None:
        return neighbor.target_entity_id in visible_entities
    return neighbor.target_identity in visible_unresolved


def _neighbor_node_cost(
    index: StructuralIndex,
    neighbor: _Neighbor,
    visible_entities: set[str],
) -> int:
    if neighbor.target_entity_id is None:
        return 1
    return len(set(_ancestor_ids(index, neighbor.target_entity_id)) - visible_entities)


def _ancestor_ids(index: StructuralIndex, entity_id: str) -> tuple[str, ...]:
    ancestors: list[str] = []
    current_id: str | None = entity_id
    while current_id is not None:
        entity = index.get_entity(current_id)
        if entity is None:
            raise ValueError("Flow Map containment ancestors must resolve within the index.")
        ancestors.append(entity.entity_id)
        current_id = entity.parent_entity_id
    ancestors.reverse()
    return tuple(ancestors)


def _project_nodes(
    index: StructuralIndex,
    visible_entities: set[str],
    visible_unresolved: dict[str, tuple[str, str, tuple[RelationshipFact, ...]]],
    frontiers: dict[str, _FrontierState],
) -> list[FlowNode]:
    entities = [index.get_entity(entity_id) for entity_id in visible_entities]
    known_entities = [entity for entity in entities if entity is not None]
    known_entities.sort(key=lambda entity: _entity_projection_order(index, entity))
    nodes = [
        FlowNode(
            id=_node_id(entity.entity_id),
            entity_id=entity.entity_id,
            kind=entity.kind,
            label=entity.name[:240],
            qualified_name=entity.qualified_name,
            parent_node_id=(
                _node_id(entity.parent_entity_id)
                if entity.parent_entity_id in visible_entities
                else None
            ),
            source_spans=[entity.flow_source_span()],
            expandable=bool(
                frontiers.get(entity.entity_id) and frontiers[entity.entity_id].targets
            ),
            hidden_neighbor_count=len(frontiers[entity.entity_id].targets)
            if entity.entity_id in frontiers and frontiers[entity.entity_id].targets
            else 0,
            metadata={
                "language": entity.language,
                "module": entity.module,
                "path": entity.path,
            },
        )
        for entity in known_entities
    ]
    for unresolved_id, (source_entity_id, unresolved_key, facts) in sorted(
        visible_unresolved.items()
    ):
        evidence = _unique_evidence(span for fact in facts for span in fact.evidence)
        nodes.append(
            FlowNode(
                id=_node_id(unresolved_id),
                entity_id=unresolved_id,
                kind=FlowEntityKind.UNRESOLVED,
                label=unresolved_key[:240],
                qualified_name=unresolved_key[:1_000],
                source_spans=evidence[:100],
                metadata={"sourceEntityId": source_entity_id},
            )
        )
    return nodes


def _project_edges(
    index: StructuralIndex,
    visible_entities: set[str],
    visible_unresolved: dict[str, tuple[str, str, tuple[RelationshipFact, ...]]],
    node_ids_by_entity: dict[str, str],
    *,
    max_edges: int,
    required_relationship_ids: frozenset[str],
) -> tuple[list[FlowEdge], bool]:
    grouped: dict[tuple[Any, ...], list[RelationshipFact]] = defaultdict(list)
    for fact in index.relationships:
        if fact.source_entity_id not in visible_entities:
            continue
        if fact.target_entity_id is not None:
            if fact.target_entity_id not in visible_entities:
                continue
            target_entity_id = fact.target_entity_id
        else:
            target_entity_id = _unresolved_entity_id(
                fact.source_entity_id,
                fact.unresolved_key or "unresolved",
            )
            if target_entity_id not in visible_unresolved:
                continue
        key = (
            fact.source_entity_id,
            target_entity_id,
            fact.kind,
            fact.resolution,
            fact.provenance.extractor,
            fact.provenance.extractor_version,
            fact.provenance.rule_id,
            fact.conditional,
            fact.asynchronous,
        )
        grouped[key].append(fact)

    prioritized_edges: list[tuple[bool, FlowEdge]] = []
    for key, facts in sorted(
        grouped.items(), key=lambda item: tuple(str(value) for value in item[0])
    ):
        source_entity_id = str(key[0])
        target_entity_id = str(key[1])
        for chunk in _edge_chunks(facts):
            fact_ids = sorted(fact.relationship_id for fact in chunk)
            evidence = _unique_evidence(span for fact in chunk for span in fact.evidence)
            fact_metadata = {fact.relationship_id: fact.metadata for fact in chunk if fact.metadata}
            relationship_signals = {
                fact.relationship_id: relationship_signals_metadata(signals)
                for fact in chunk
                if (signals := index.get_relationship_signals(fact.relationship_id)) is not None
            }
            edge = FlowEdge(
                id=_edge_id(fact_ids),
                source_node_id=node_ids_by_entity[source_entity_id],
                target_node_id=node_ids_by_entity[target_entity_id],
                kind=chunk[0].kind,
                resolution=chunk[0].resolution,
                provenance=chunk[0].provenance,
                evidence=evidence,
                conditional=chunk[0].conditional,
                asynchronous=chunk[0].asynchronous,
                metadata={
                    "relationshipFactIds": fact_ids,
                    **({"factMetadata": fact_metadata} if fact_metadata else {}),
                    **(
                        {"relationshipSignals": relationship_signals}
                        if relationship_signals
                        else {}
                    ),
                },
            )
            prioritized_edges.append(
                (not bool(required_relationship_ids.intersection(fact_ids)), edge)
            )
    prioritized_edges.sort(key=lambda item: (item[0], item[1].id))
    edges = [edge for _, edge in prioritized_edges]
    return edges[:max_edges], len(edges) > max_edges


def _edge_chunks(facts: Sequence[RelationshipFact]) -> tuple[tuple[RelationshipFact, ...], ...]:
    chunks: list[tuple[RelationshipFact, ...]] = []
    current: list[RelationshipFact] = []
    evidence_count = 0
    for fact in sorted(facts, key=lambda item: item.relationship_id):
        if current and evidence_count + len(fact.evidence) > 100:
            chunks.append(tuple(current))
            current = []
            evidence_count = 0
        current.append(fact)
        evidence_count += len(fact.evidence)
    if current:
        chunks.append(tuple(current))
    return tuple(chunks)


def _project_frontiers(
    frontiers: dict[str, _FrontierState],
    node_ids_by_entity: dict[str, str],
) -> list[FlowFrontier]:
    return [
        FlowFrontier(
            node_id=node_ids_by_entity[source_entity_id],
            direction=FlowFrontierDirection.OUTGOING,
            hidden_neighbor_count=len(state.targets),
            relationship_kinds=sorted(
                {
                    kind
                    for relationship_kinds in state.targets.values()
                    for kind in relationship_kinds
                },
                key=lambda kind: kind.value,
            ),
        )
        for source_entity_id, state in sorted(frontiers.items())
        if state.targets and source_entity_id in node_ids_by_entity
    ]


def _projection_warnings(
    frontiers: dict[str, _FrontierState],
    *,
    edge_budget_reached: bool,
    anchor_ids: tuple[str, ...],
    reconciliation: AnchorReconciliationResult,
    projection: _ReconciliationProjection,
) -> list[FlowWarning]:
    warnings: list[FlowWarning] = []
    if projection.component_count > 1:
        warnings.append(
            FlowWarning(
                code="disconnected_feature_anchors",
                message=(
                    f"The selected anchors remain in {projection.component_count} "
                    "evidence-connected components. Sentia did not invent a missing "
                    "relationship."
                ),
                entity_ids=list(anchor_ids),
            )
        )
    if projection.budget_limited:
        warnings.append(
            FlowWarning(
                code="anchor_bridge_budget_reached",
                message=(
                    "At least one evidence-backed anchor path could not fit within the "
                    "configured node or edge budget."
                ),
                entity_ids=list(anchor_ids),
            )
        )
    if projection.search_bounded:
        warnings.append(
            FlowWarning(
                code="anchor_reconciliation_bounded",
                message=(
                    "Anchor reconciliation reached a deterministic search bound "
                    f"({', '.join(reconciliation.diagnostics.truncation_reasons)}); "
                    "additional evidence-backed paths may exist."
                ),
                entity_ids=list(anchor_ids),
            )
        )
    if projection.weak_connection_used:
        warnings.append(
            FlowWarning(
                code="weak_anchor_connection",
                message=(
                    "Some anchors are connected through shared structural relationships, "
                    "not a single directed execution path."
                ),
                entity_ids=list(anchor_ids),
            )
        )
    bounded_sources = sorted(
        source_entity_id for source_entity_id, state in frontiers.items() if state.targets
    )
    if bounded_sources:
        warnings.append(
            FlowWarning(
                code="traversal_bounded",
                message=(
                    "Additional evidence-backed branches were omitted by deterministic "
                    "traversal budgets; displayed relationships remain source-backed."
                ),
                entity_ids=bounded_sources[:100],
            )
        )
    if edge_budget_reached:
        warnings.append(
            FlowWarning(
                code="edge_budget_reached",
                message="Some relationships between visible nodes were omitted by the edge budget.",
            )
        )
    return warnings


def _terms(value: str) -> frozenset[str]:
    expanded = _CAMEL_BOUNDARY.sub(" ", value)
    return frozenset(
        token
        for token in (match.group(0).lower() for match in _TERM_PATTERN.finditer(expanded))
        if len(token) > 1 and token not in _STOP_TERMS
    )


def _entity_terms(entity: CodeEntity) -> frozenset[str]:
    return _terms(f"{entity.name} {entity.qualified_name} {entity.module} {entity.path}")


def _entity_projection_order(index: StructuralIndex, entity: CodeEntity) -> tuple[Any, ...]:
    depth = len(_ancestor_ids(index, entity.entity_id))
    return (depth, entity.path, entity.start_line, entity.start_column, entity.entity_id)


def _unique_evidence(evidence: Iterable[FlowSourceSpan]) -> list[FlowSourceSpan]:
    by_identity: dict[tuple[Any, ...], FlowSourceSpan] = {}
    for span in evidence:
        identity = (
            span.path,
            span.start_line,
            span.start_column,
            span.end_line,
            span.end_column,
            span.content_hash,
            span.repository_revision,
        )
        by_identity[identity] = span
    return [by_identity[identity] for identity in sorted(by_identity)]


def _node_id(entity_id: str) -> str:
    return f"node_{hashlib.sha256(entity_id.encode('utf-8')).hexdigest()}"


def _unresolved_entity_id(source_entity_id: str, unresolved_key: str) -> str:
    identity = f"{source_entity_id}\0{unresolved_key}"
    return f"ent_{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _edge_id(relationship_fact_ids: Sequence[str]) -> str:
    identity = "\0".join(relationship_fact_ids)
    return f"edge_{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _map_id(
    *,
    index: StructuralIndex,
    selection: FlowRootSelection,
    question_terms: frozenset[str],
    candidate_terms: frozenset[str],
    policy: FlowTraversalPolicy,
    nodes: Sequence[FlowNode],
    edges: Sequence[FlowEdge],
) -> str:
    identity = json.dumps(
        {
            "workspace": index.root.as_posix(),
            "revision": index.repository_revision,
            "roots": list(selection.root_entity_ids),
            "questionTerms": sorted(question_terms),
            "candidateTerms": sorted(candidate_terms),
            "policy": {
                "maxDepth": policy.max_depth,
                "maxNodes": policy.max_nodes,
                "maxEdges": policy.max_edges,
                "maxBranchesPerNode": policy.max_branches_per_node,
            },
            "nodes": [node.entity_id for node in nodes],
            "edges": [edge.id for edge in edges],
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"map_{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"
