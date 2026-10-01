from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from sentia_sidecar.protocol import (
    TRAVERSABLE_FLOW_RELATIONSHIP_KINDS,
    FlowRelationshipKind,
    FlowResolution,
    FlowRootSelection,
)
from sentia_sidecar.structural_index import RelationshipFact, StructuralIndex

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

_RESOLUTION_RANK = {
    FlowResolution.COMPILER_RESOLVED: 0,
    FlowResolution.STATICALLY_RESOLVED: 1,
    FlowResolution.RUNTIME_OBSERVED: 2,
    FlowResolution.FRAMEWORK_INFERRED: 3,
    FlowResolution.UNRESOLVED: 4,
}


@dataclass(frozen=True)
class AnchorReconciliationPolicy:
    max_path_depth: int = 8
    max_visited_entities: int = 2_000
    max_candidate_paths_per_pair: int = 3
    max_incoming_candidates: int = 100
    max_outgoing_candidates: int = 100
    max_upstream_depth: int = 2

    def __post_init__(self) -> None:
        if not 1 <= self.max_path_depth <= 25:
            raise ValueError("Anchor path depth must be between 1 and 25.")
        if not 1 <= self.max_visited_entities <= 100_000:
            raise ValueError("Anchor reconciliation visit budget must be between 1 and 100000.")
        if not 1 <= self.max_candidate_paths_per_pair <= 20:
            raise ValueError("Anchor candidate path budget must be between 1 and 20.")
        if not 1 <= self.max_incoming_candidates <= 1_000:
            raise ValueError("Anchor incoming candidate budget must be between 1 and 1000.")
        if not 1 <= self.max_outgoing_candidates <= 1_000:
            raise ValueError("Anchor outgoing candidate budget must be between 1 and 1000.")
        if not 1 <= self.max_upstream_depth <= self.max_path_depth:
            raise ValueError("Anchor upstream depth must fit within the path depth budget.")


@dataclass(frozen=True)
class AnchorPath:
    source_anchor_id: str
    target_anchor_id: str
    entity_ids: tuple[str, ...]
    relationships: tuple[RelationshipFact, ...]
    directed: bool

    @property
    def relationship_ids(self) -> tuple[str, ...]:
        return tuple(fact.relationship_id for fact in self.relationships)

    @property
    def intermediate_entity_ids(self) -> tuple[str, ...]:
        anchors = {self.source_anchor_id, self.target_anchor_id}
        return tuple(entity_id for entity_id in self.entity_ids if entity_id not in anchors)


@dataclass(frozen=True)
class CoordinatorAnchorPath:
    anchor_entity_id: str
    entity_ids: tuple[str, ...]
    relationships: tuple[RelationshipFact, ...]


@dataclass(frozen=True)
class CoordinatorCandidate:
    entity_id: str
    directly_called_anchor_ids: tuple[str, ...]
    reachable_anchor_ids: tuple[str, ...]
    anchor_paths: tuple[CoordinatorAnchorPath, ...]

    @property
    def evidence_relationship_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {fact.relationship_id for path in self.anchor_paths for fact in path.relationships}
            )
        )


@dataclass(frozen=True)
class SharedCalleeCandidate:
    entity_id: str
    calling_anchor_ids: tuple[str, ...]
    relationships: tuple[RelationshipFact, ...]


@dataclass(frozen=True)
class AnchorReconciliationDiagnostics:
    visited_entities: int
    candidate_paths_found: int
    incoming_candidates_considered: int
    outgoing_candidates_considered: int
    truncation_reasons: tuple[str, ...]

    @property
    def truncated(self) -> bool:
        return bool(self.truncation_reasons)


@dataclass(frozen=True)
class AnchorReconciliationResult:
    repository_revision: int
    anchor_entity_ids: tuple[str, ...]
    components: tuple[tuple[str, ...], ...]
    direct_anchor_relationships: tuple[RelationshipFact, ...]
    connecting_paths: tuple[AnchorPath, ...]
    backbone_paths: tuple[AnchorPath, ...]
    intermediate_entity_ids: tuple[str, ...]
    coordinator_candidates: tuple[CoordinatorCandidate, ...]
    shared_callee_candidates: tuple[SharedCalleeCandidate, ...]
    structural_entry_entity_ids: tuple[str, ...]
    expansion_seed_entity_ids: tuple[str, ...]
    disconnected_anchor_entity_ids: tuple[str, ...]
    diagnostics: AnchorReconciliationDiagnostics

    @property
    def backbone_entity_ids(self) -> frozenset[str]:
        return frozenset(
            {
                *self.anchor_entity_ids,
                *self.structural_entry_entity_ids,
                *(entity_id for path in self.backbone_paths for entity_id in path.entity_ids),
            }
        )

    @property
    def backbone_relationship_ids(self) -> frozenset[str]:
        return frozenset(
            fact.relationship_id for path in self.backbone_paths for fact in path.relationships
        )

    def diagnostics_metadata(self) -> dict[str, object]:
        connected_anchor_count = sum(
            len(component) for component in self.components if len(component) > 1
        )
        return {
            "repositoryRevision": self.repository_revision,
            "anchorEntityIds": list(self.anchor_entity_ids),
            "componentCount": len(self.components),
            "components": [list(component) for component in self.components],
            "connectedAnchorCount": connected_anchor_count,
            "directAnchorRelationshipIds": [
                fact.relationship_id for fact in self.direct_anchor_relationships
            ],
            "connectingPaths": [
                {
                    "sourceAnchorId": path.source_anchor_id,
                    "targetAnchorId": path.target_anchor_id,
                    "directed": path.directed,
                    "entityIds": list(path.entity_ids),
                    "relationshipIds": list(path.relationship_ids),
                }
                for path in self.connecting_paths
            ],
            "intermediateEntityIds": list(self.intermediate_entity_ids),
            "coordinatorCandidates": [
                {
                    "entityId": candidate.entity_id,
                    "directlyCalledAnchorIds": list(candidate.directly_called_anchor_ids),
                    "reachableAnchorIds": list(candidate.reachable_anchor_ids),
                    "evidenceRelationshipIds": list(candidate.evidence_relationship_ids),
                }
                for candidate in self.coordinator_candidates
            ],
            "sharedCalleeCandidates": [
                {
                    "entityId": candidate.entity_id,
                    "callingAnchorIds": list(candidate.calling_anchor_ids),
                    "evidenceRelationshipIds": [
                        fact.relationship_id for fact in candidate.relationships
                    ],
                }
                for candidate in self.shared_callee_candidates
            ],
            "structuralEntryEntityIds": list(self.structural_entry_entity_ids),
            "disconnectedAnchorEntityIds": list(self.disconnected_anchor_entity_ids),
            "visitedEntities": self.diagnostics.visited_entities,
            "candidatePathsFound": self.diagnostics.candidate_paths_found,
            "incomingCandidatesConsidered": self.diagnostics.incoming_candidates_considered,
            "outgoingCandidatesConsidered": self.diagnostics.outgoing_candidates_considered,
            "truncated": self.diagnostics.truncated,
            "truncationReasons": list(self.diagnostics.truncation_reasons),
        }


@dataclass
class _SearchUsage:
    visited_entities: int = 0
    candidate_paths_found: int = 0
    incoming_candidates_considered: int = 0
    outgoing_candidates_considered: int = 0
    truncation_reasons: set[str] | None = None

    def __post_init__(self) -> None:
        if self.truncation_reasons is None:
            self.truncation_reasons = set()

    def truncate(self, reason: str) -> None:
        assert self.truncation_reasons is not None
        self.truncation_reasons.add(reason)


def reconcile_anchors(
    index: StructuralIndex,
    selection: FlowRootSelection,
    *,
    policy: AnchorReconciliationPolicy | None = None,
) -> AnchorReconciliationResult:
    """Reconstruct evidence-backed connectivity between final validated anchors."""

    if selection.repository_revision != index.repository_revision:
        raise ValueError("The anchor selection does not match the structural index revision.")
    anchor_ids = tuple(selection.root_entity_ids)
    if any(index.get_entity(entity_id) is None for entity_id in anchor_ids):
        raise ValueError("Every anchor must exist in the structural index.")

    active_policy = policy or AnchorReconciliationPolicy()
    usage = _SearchUsage()
    anchor_set = set(anchor_ids)
    direct_relationships = tuple(
        fact
        for fact in index.relationships
        if fact.kind in TRAVERSABLE_FLOW_RELATIONSHIP_KINDS
        and fact.target_entity_id is not None
        and fact.source_entity_id in anchor_set
        and fact.target_entity_id in anchor_set
    )

    connecting_paths: list[AnchorPath] = []
    sorted_anchor_ids = tuple(sorted(anchor_ids))
    for offset, first_anchor_id in enumerate(sorted_anchor_ids):
        for second_anchor_id in sorted_anchor_ids[offset + 1 :]:
            paths = _anchor_pair_paths(
                index,
                first_anchor_id,
                second_anchor_id,
                policy=active_policy,
                usage=usage,
            )
            connecting_paths.extend(paths)

    coordinator_candidates = _coordinator_candidates(
        index,
        sorted_anchor_ids,
        policy=active_policy,
        usage=usage,
    )
    shared_callees = _shared_callee_candidates(
        index,
        sorted_anchor_ids,
        policy=active_policy,
        usage=usage,
    )
    connecting_paths = _add_coordinator_connections(connecting_paths, coordinator_candidates)
    connecting_paths.sort(key=_path_rank)

    backbone_paths, components = _select_backbone_paths(sorted_anchor_ids, connecting_paths)
    intermediates = tuple(
        sorted(
            {entity_id for path in connecting_paths for entity_id in path.intermediate_entity_ids}
        )
    )
    structural_entries = _structural_entries(
        components,
        backbone_paths,
        coordinator_candidates,
    )
    expansion_seeds = _expansion_seeds(
        sorted_anchor_ids,
        backbone_paths,
        structural_entries,
    )
    disconnected = tuple(
        component[0] for component in components if len(component) == 1 and len(anchor_ids) > 1
    )
    truncation_reasons = usage.truncation_reasons or set()
    diagnostics = AnchorReconciliationDiagnostics(
        visited_entities=usage.visited_entities,
        candidate_paths_found=usage.candidate_paths_found,
        incoming_candidates_considered=usage.incoming_candidates_considered,
        outgoing_candidates_considered=usage.outgoing_candidates_considered,
        truncation_reasons=tuple(sorted(truncation_reasons)),
    )
    return AnchorReconciliationResult(
        repository_revision=index.repository_revision,
        anchor_entity_ids=anchor_ids,
        components=components,
        direct_anchor_relationships=direct_relationships,
        connecting_paths=tuple(connecting_paths),
        backbone_paths=backbone_paths,
        intermediate_entity_ids=intermediates,
        coordinator_candidates=coordinator_candidates,
        shared_callee_candidates=shared_callees,
        structural_entry_entity_ids=structural_entries,
        expansion_seed_entity_ids=expansion_seeds,
        disconnected_anchor_entity_ids=disconnected,
        diagnostics=diagnostics,
    )


def _anchor_pair_paths(
    index: StructuralIndex,
    first_anchor_id: str,
    second_anchor_id: str,
    *,
    policy: AnchorReconciliationPolicy,
    usage: _SearchUsage,
) -> list[AnchorPath]:
    directed_paths: list[AnchorPath] = []
    for source_id, target_id in (
        (first_anchor_id, second_anchor_id),
        (second_anchor_id, first_anchor_id),
    ):
        directed_paths.extend(
            _bounded_paths(
                index,
                source_id,
                target_id,
                source_anchor_id=source_id,
                target_anchor_id=target_id,
                directed=True,
                policy=policy,
                usage=usage,
            )
        )
    if directed_paths:
        directed_paths.sort(key=_path_rank)
        return directed_paths[: policy.max_candidate_paths_per_pair]
    return _bounded_paths(
        index,
        first_anchor_id,
        second_anchor_id,
        source_anchor_id=first_anchor_id,
        target_anchor_id=second_anchor_id,
        directed=False,
        policy=policy,
        usage=usage,
    )


def _bounded_paths(
    index: StructuralIndex,
    source_entity_id: str,
    target_entity_id: str,
    *,
    source_anchor_id: str,
    target_anchor_id: str,
    directed: bool,
    policy: AnchorReconciliationPolicy,
    usage: _SearchUsage,
) -> list[AnchorPath]:
    queue: deque[tuple[str, tuple[str, ...], tuple[RelationshipFact, ...]]] = deque(
        [(source_entity_id, (source_entity_id,), ())]
    )
    found: list[AnchorPath] = []
    while queue and len(found) < policy.max_candidate_paths_per_pair:
        if usage.visited_entities >= policy.max_visited_entities:
            usage.truncate("max_visited_entities")
            break
        entity_id, entity_path, relationships = queue.popleft()
        usage.visited_entities += 1
        if len(relationships) >= policy.max_path_depth:
            if any(
                neighbor_id not in entity_path
                for neighbor_id, _ in _neighbors(
                    index,
                    entity_id,
                    directed=directed,
                    policy=policy,
                    usage=usage,
                )
            ):
                usage.truncate("max_path_depth")
            continue
        for neighbor_id, fact in _neighbors(
            index,
            entity_id,
            directed=directed,
            policy=policy,
            usage=usage,
        ):
            if neighbor_id in entity_path:
                continue
            next_entities = (*entity_path, neighbor_id)
            next_relationships = (*relationships, fact)
            if neighbor_id == target_entity_id:
                found.append(
                    AnchorPath(
                        source_anchor_id=source_anchor_id,
                        target_anchor_id=target_anchor_id,
                        entity_ids=next_entities,
                        relationships=next_relationships,
                        directed=directed,
                    )
                )
                usage.candidate_paths_found += 1
                if len(found) == policy.max_candidate_paths_per_pair and queue:
                    usage.truncate("max_candidate_paths_per_pair")
                continue
            queue.append((neighbor_id, next_entities, next_relationships))
    found.sort(key=_path_rank)
    return found


def _neighbors(
    index: StructuralIndex,
    entity_id: str,
    *,
    directed: bool,
    policy: AnchorReconciliationPolicy,
    usage: _SearchUsage,
) -> tuple[tuple[str, RelationshipFact], ...]:
    outgoing_all = tuple(
        (fact.target_entity_id, fact)
        for fact in index.outgoing_relationships(
            entity_id,
            kinds=TRAVERSABLE_FLOW_RELATIONSHIP_KINDS,
        )
        if fact.target_entity_id is not None and fact.resolution != FlowResolution.UNRESOLVED
    )
    usage.outgoing_candidates_considered += len(outgoing_all)
    outgoing = _bounded_neighbors(
        outgoing_all,
        policy.max_outgoing_candidates,
        "max_outgoing_candidates",
        usage,
    )
    neighbors = list(outgoing)
    if not directed:
        incoming_all = tuple(
            (fact.source_entity_id, fact)
            for fact in index.incoming_relationships(
                entity_id,
                kinds=TRAVERSABLE_FLOW_RELATIONSHIP_KINDS,
            )
            if fact.resolution != FlowResolution.UNRESOLVED
        )
        usage.incoming_candidates_considered += len(incoming_all)
        neighbors.extend(
            _bounded_neighbors(
                incoming_all,
                policy.max_incoming_candidates,
                "max_incoming_candidates",
                usage,
            )
        )
    neighbors.sort(key=_neighbor_rank)
    return tuple(neighbors)


def _bounded_neighbors(
    neighbors: tuple[tuple[str | None, RelationshipFact], ...],
    limit: int,
    reason: str,
    usage: _SearchUsage,
) -> tuple[tuple[str, RelationshipFact], ...]:
    resolved = [(entity_id, fact) for entity_id, fact in neighbors if entity_id is not None]
    resolved.sort(key=_neighbor_rank)
    if len(resolved) > limit:
        usage.truncate(reason)
    return tuple(resolved[:limit])


def _neighbor_rank(item: tuple[str, RelationshipFact]) -> tuple[object, ...]:
    entity_id, fact = item
    return (
        _RESOLUTION_RANK[fact.resolution],
        -_RELATIONSHIP_PRIORITY[fact.kind],
        entity_id,
        fact.relationship_id,
    )


def _path_rank(path: AnchorPath) -> tuple[object, ...]:
    return (
        not path.directed,
        len(path.relationships),
        sum(_RESOLUTION_RANK[fact.resolution] for fact in path.relationships),
        path.relationship_ids,
        path.source_anchor_id,
        path.target_anchor_id,
    )


def _coordinator_candidates(
    index: StructuralIndex,
    anchor_ids: tuple[str, ...],
    *,
    policy: AnchorReconciliationPolicy,
    usage: _SearchUsage,
) -> tuple[CoordinatorCandidate, ...]:
    paths_by_candidate: dict[str, dict[str, CoordinatorAnchorPath]] = defaultdict(dict)
    for anchor_id in anchor_ids:
        queue: deque[tuple[str, tuple[str, ...], tuple[RelationshipFact, ...]]] = deque(
            [(anchor_id, (anchor_id,), ())]
        )
        while queue:
            if usage.visited_entities >= policy.max_visited_entities:
                usage.truncate("max_visited_entities")
                break
            entity_id, entity_path, relationships = queue.popleft()
            usage.visited_entities += 1
            if len(relationships) >= policy.max_upstream_depth:
                if any(
                    fact.source_entity_id not in entity_path
                    for fact in index.incoming_relationships(
                        entity_id,
                        kinds=TRAVERSABLE_FLOW_RELATIONSHIP_KINDS,
                    )
                ):
                    usage.truncate("max_upstream_depth")
                continue
            incoming_all = tuple(
                (fact.source_entity_id, fact)
                for fact in index.incoming_relationships(
                    entity_id,
                    kinds=TRAVERSABLE_FLOW_RELATIONSHIP_KINDS,
                )
                if fact.resolution != FlowResolution.UNRESOLVED
            )
            usage.incoming_candidates_considered += len(incoming_all)
            incoming = _bounded_neighbors(
                incoming_all,
                policy.max_incoming_candidates,
                "max_incoming_candidates",
                usage,
            )
            for source_id, fact in incoming:
                if source_id in entity_path:
                    continue
                next_entities = (source_id, *entity_path)
                next_relationships = (fact, *relationships)
                candidate_path = CoordinatorAnchorPath(
                    anchor_entity_id=anchor_id,
                    entity_ids=next_entities,
                    relationships=next_relationships,
                )
                existing = paths_by_candidate[source_id].get(anchor_id)
                if existing is None or _coordinator_path_rank(
                    candidate_path
                ) < _coordinator_path_rank(existing):
                    paths_by_candidate[source_id][anchor_id] = candidate_path
                queue.append((source_id, next_entities, next_relationships))

    candidates: list[CoordinatorCandidate] = []
    for entity_id, anchor_paths in paths_by_candidate.items():
        if len(anchor_paths) < 2:
            continue
        paths = tuple(anchor_paths[anchor_id] for anchor_id in sorted(anchor_paths))
        directly_called = tuple(
            path.anchor_entity_id for path in paths if len(path.relationships) == 1
        )
        candidates.append(
            CoordinatorCandidate(
                entity_id=entity_id,
                directly_called_anchor_ids=directly_called,
                reachable_anchor_ids=tuple(path.anchor_entity_id for path in paths),
                anchor_paths=paths,
            )
        )
    candidates.sort(key=_coordinator_rank)
    return tuple(candidates)


def _coordinator_path_rank(path: CoordinatorAnchorPath) -> tuple[object, ...]:
    return (
        len(path.relationships),
        sum(_RESOLUTION_RANK[fact.resolution] for fact in path.relationships),
        tuple(fact.relationship_id for fact in path.relationships),
    )


def _coordinator_rank(candidate: CoordinatorCandidate) -> tuple[object, ...]:
    return (
        -len(candidate.reachable_anchor_ids),
        -len(candidate.directly_called_anchor_ids),
        sum(len(path.relationships) for path in candidate.anchor_paths),
        candidate.entity_id,
    )


def _shared_callee_candidates(
    index: StructuralIndex,
    anchor_ids: tuple[str, ...],
    *,
    policy: AnchorReconciliationPolicy,
    usage: _SearchUsage,
) -> tuple[SharedCalleeCandidate, ...]:
    facts_by_target: dict[str, list[RelationshipFact]] = defaultdict(list)
    anchors_by_target: dict[str, set[str]] = defaultdict(set)
    for anchor_id in anchor_ids:
        outgoing_all = tuple(
            (fact.target_entity_id, fact)
            for fact in index.outgoing_relationships(
                anchor_id,
                kinds=TRAVERSABLE_FLOW_RELATIONSHIP_KINDS,
            )
            if fact.target_entity_id is not None and fact.resolution != FlowResolution.UNRESOLVED
        )
        usage.outgoing_candidates_considered += len(outgoing_all)
        for target_id, fact in _bounded_neighbors(
            outgoing_all,
            policy.max_outgoing_candidates,
            "max_outgoing_candidates",
            usage,
        ):
            facts_by_target[target_id].append(fact)
            anchors_by_target[target_id].add(anchor_id)
    return tuple(
        SharedCalleeCandidate(
            entity_id=target_id,
            calling_anchor_ids=tuple(sorted(anchors_by_target[target_id])),
            relationships=tuple(
                sorted(
                    facts_by_target[target_id],
                    key=lambda fact: fact.relationship_id,
                )
            ),
        )
        for target_id in sorted(anchors_by_target)
        if len(anchors_by_target[target_id]) >= 2
    )


def _add_coordinator_connections(
    paths: list[AnchorPath],
    coordinators: tuple[CoordinatorCandidate, ...],
) -> list[AnchorPath]:
    result = list(paths)
    identities = {(path.source_anchor_id, path.target_anchor_id, path.entity_ids) for path in paths}
    for coordinator in coordinators:
        for offset, first_path in enumerate(coordinator.anchor_paths):
            for second_path in coordinator.anchor_paths[offset + 1 :]:
                entity_ids = (
                    *reversed(first_path.entity_ids),
                    *second_path.entity_ids[1:],
                )
                identity = (
                    first_path.anchor_entity_id,
                    second_path.anchor_entity_id,
                    entity_ids,
                )
                if identity in identities:
                    continue
                result.append(
                    AnchorPath(
                        source_anchor_id=first_path.anchor_entity_id,
                        target_anchor_id=second_path.anchor_entity_id,
                        entity_ids=entity_ids,
                        relationships=(
                            *reversed(first_path.relationships),
                            *second_path.relationships,
                        ),
                        directed=False,
                    )
                )
                identities.add(identity)
    return result


def _select_backbone_paths(
    anchor_ids: tuple[str, ...],
    paths: list[AnchorPath],
) -> tuple[tuple[AnchorPath, ...], tuple[tuple[str, ...], ...]]:
    parents = {anchor_id: anchor_id for anchor_id in anchor_ids}

    def find(anchor_id: str) -> str:
        current = anchor_id
        while parents[current] != current:
            current = parents[current]
        return current

    def union(first_anchor_id: str, second_anchor_id: str) -> bool:
        first_root = find(first_anchor_id)
        second_root = find(second_anchor_id)
        if first_root == second_root:
            return False
        if first_root < second_root:
            parents[second_root] = first_root
        else:
            parents[first_root] = second_root
        return True

    backbone: list[AnchorPath] = []
    for path in sorted(paths, key=_path_rank):
        if union(path.source_anchor_id, path.target_anchor_id):
            backbone.append(path)
    grouped: dict[str, list[str]] = defaultdict(list)
    for anchor_id in anchor_ids:
        grouped[find(anchor_id)].append(anchor_id)
    components = tuple(
        sorted(
            (tuple(sorted(ids)) for ids in grouped.values()),
            key=lambda ids: ids[0],
        )
    )
    return tuple(backbone), components


def _structural_entries(
    components: tuple[tuple[str, ...], ...],
    backbone_paths: tuple[AnchorPath, ...],
    coordinators: tuple[CoordinatorCandidate, ...],
) -> tuple[str, ...]:
    entries: list[str] = []
    for component in components:
        component_set = set(component)
        strong_coordinator = next(
            (
                candidate
                for candidate in coordinators
                if len(candidate.directly_called_anchor_ids) >= 2
                and component_set <= set(candidate.reachable_anchor_ids)
            ),
            None,
        )
        if strong_coordinator is not None:
            entries.append(strong_coordinator.entity_id)
            continue
        backbone_entities = {
            entity_id
            for path in backbone_paths
            if path.source_anchor_id in component_set and path.target_anchor_id in component_set
            for entity_id in path.entity_ids
        }
        incoming_anchors = {
            fact.target_entity_id
            for path in backbone_paths
            if path.source_anchor_id in component_set and path.target_anchor_id in component_set
            for fact in path.relationships
            if fact.target_entity_id in component_set and fact.source_entity_id in backbone_entities
        }
        component_entries = sorted(component_set - incoming_anchors)
        entries.extend(component_entries or [component[0]])
    return tuple(dict.fromkeys(entries))


def _expansion_seeds(
    anchor_ids: tuple[str, ...],
    backbone_paths: tuple[AnchorPath, ...],
    structural_entries: tuple[str, ...],
) -> tuple[str, ...]:
    backbone_entities = {
        *anchor_ids,
        *structural_entries,
        *(entity_id for path in backbone_paths for entity_id in path.entity_ids),
    }
    sources = {fact.source_entity_id for path in backbone_paths for fact in path.relationships}
    terminals = sorted(backbone_entities - sources)
    return tuple(terminals or structural_entries)
