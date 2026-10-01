from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from sentia_sidecar.flow_graph import build_feature_flow
from sentia_sidecar.protocol import FlowRootSelection
from sentia_sidecar.python_relationships import extract_python_relationships
from sentia_sidecar.relationship_signals import (
    TargetProvenance,
    TargetProvenanceBasis,
)
from sentia_sidecar.structural_index import (
    CodeEntity,
    RelationshipFact,
    StructuralIndex,
    extract_python_entities,
)


def build_index(root: Path, sources: Mapping[str, str], *, revision: int = 1) -> StructuralIndex:
    entities: list[CodeEntity] = []
    for path, source in sorted(sources.items()):
        entities.extend(
            extract_python_entities(
                root,
                path,
                source,
                hashlib.sha256(source.encode("utf-8")).hexdigest(),
                revision,
            )
        )
    entity_index = StructuralIndex(root, revision, entities)
    relationships = extract_python_relationships(entity_index, sources)
    return StructuralIndex(root, revision, entities, relationships)


def entity_by_name(index: StructuralIndex, qualified_name: str) -> CodeEntity:
    return next(entity for entity in index.entities if entity.qualified_name == qualified_name)


def fact_by_callee(index: StructuralIndex, source: CodeEntity, callee: str) -> RelationshipFact:
    return next(
        fact
        for fact in index.outgoing_relationships(source.entity_id)
        if fact.metadata.get("callee") == callee
    )


def provenance_for(
    index: StructuralIndex,
    source: CodeEntity,
    callee: str,
) -> TargetProvenance:
    fact = fact_by_callee(index, source, callee)
    signals = index.get_relationship_signals(fact.relationship_id)
    assert signals is not None
    return signals.target_provenance


def test_target_provenance_uses_resolved_entities_and_explicit_imports(
    tmp_path: Path,
) -> None:
    source = """\
import asyncio
import stripe
from requests import post

def _format_payload():
    return {}

async def perform_operation():
    return None

def run_feature(unknown):
    _format_payload()
    asyncio.run(perform_operation())
    stripe.PaymentIntent.create()
    post("https://example.test/jobs")
    unknown.foo()
"""
    index = build_index(tmp_path, {"feature.py": source}, revision=9)
    run_feature = entity_by_name(index, "run_feature")

    assert provenance_for(index, run_feature, "_format_payload") == TargetProvenance.PROJECT_OWNED
    assert provenance_for(index, run_feature, "perform_operation") == TargetProvenance.PROJECT_OWNED
    assert provenance_for(index, run_feature, "asyncio.run") == TargetProvenance.STANDARD_LIBRARY
    assert (
        provenance_for(index, run_feature, "stripe.PaymentIntent.create")
        == TargetProvenance.DEPENDENCY_OWNED
    )
    assert provenance_for(index, run_feature, "post") == TargetProvenance.DEPENDENCY_OWNED
    assert provenance_for(index, run_feature, "unknown.foo") == TargetProvenance.UNRESOLVED

    stripe_fact = fact_by_callee(index, run_feature, "stripe.PaymentIntent.create")
    stripe_signals = index.get_relationship_signals(stripe_fact.relationship_id)
    assert stripe_signals is not None
    assert stripe_signals.repository_revision == 9
    assert stripe_signals.target_provenance_basis == TargetProvenanceBasis.EXTERNAL_IMPORT
    assert stripe_signals.target_module == "stripe"


def test_relative_or_project_shadowed_imports_remain_conservative(tmp_path: Path) -> None:
    sources = {
        "package/__init__.py": "",
        "package/service.py": """\
from . import plugins
import package.dynamic

def run():
    plugins.execute()
    package.dynamic.execute()
""",
    }
    index = build_index(tmp_path, sources)
    run = entity_by_name(index, "run")

    assert provenance_for(index, run, "plugins.execute") == TargetProvenance.UNRESOLVED
    assert provenance_for(index, run, "package.dynamic.execute") == TargetProvenance.UNRESOLVED


def test_provenance_is_diagnostic_and_does_not_change_flow_visibility(tmp_path: Path) -> None:
    source = """\
import asyncio
import stripe

def _project_helper():
    return None

def run_feature():
    _project_helper()
    asyncio.run()
    stripe.PaymentIntent.create()
"""
    index = build_index(tmp_path, {"feature.py": source})
    run_feature = entity_by_name(index, "run_feature")
    graph = build_feature_flow(
        index,
        FlowRootSelection(
            repository_revision=1,
            root_entity_ids=[run_feature.entity_id],
            rationale="The function is the feature entry point.",
        ),
        question="How does run feature work?",
    )

    visible_labels = {node.label for node in graph.nodes}
    assert {"_project_helper", "asyncio.run", "stripe.PaymentIntent.create"} <= visible_labels
    by_target_label = {
        next(node.label for node in graph.nodes if node.id == edge.target_node_id): edge
        for edge in graph.edges
    }
    assert (
        next(iter(by_target_label["_project_helper"].metadata["relationshipSignals"].values()))[
            "targetProvenance"
        ]
        == "project_owned"
    )
    assert (
        next(iter(by_target_label["asyncio.run"].metadata["relationshipSignals"].values()))[
            "targetProvenance"
        ]
        == "standard_library"
    )
    assert (
        next(
            iter(
                by_target_label["stripe.PaymentIntent.create"]
                .metadata["relationshipSignals"]
                .values()
            )
        )["targetProvenance"]
        == "dependency_owned"
    )
