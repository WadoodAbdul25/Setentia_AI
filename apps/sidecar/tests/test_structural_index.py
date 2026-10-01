from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sentia_sidecar.protocol import (
    FlowEntityKind,
    FlowProvenance,
    FlowRelationshipKind,
    FlowResolution,
    FlowSourceSpan,
)
from sentia_sidecar.structural_index import (
    CodeEntity,
    CodeEntityIndex,
    RelationshipFact,
    StructuralIndex,
    extract_python_entities,
)

PYTHON_SOURCE = """\
from typing import Any

@service
class AuthService:
    async def login(self, email: str) -> Any:
        def record_attempt() -> str:
            return email

        return record_attempt()

def helper() -> bool:
    return True
"""


def extract(root: Path, source: str, revision: int = 1) -> tuple[CodeEntity, ...]:
    content_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return extract_python_entities(
        root,
        "backend/auth.py",
        source,
        content_hash,
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


def test_python_entities_capture_stable_containment_and_source_ranges(tmp_path: Path) -> None:
    entities = extract(tmp_path, PYTHON_SOURCE)
    by_qualified_name = {entity.qualified_name: entity for entity in entities}

    file_entity = by_qualified_name["backend/auth.py"]
    module = by_qualified_name["backend.auth"]
    auth_service = by_qualified_name["AuthService"]
    login = by_qualified_name["AuthService.login"]
    record_attempt = by_qualified_name["AuthService.login.record_attempt"]
    helper = by_qualified_name["helper"]

    assert file_entity.kind == FlowEntityKind.FILE
    assert module.kind == FlowEntityKind.MODULE
    assert module.parent_entity_id == file_entity.entity_id
    assert auth_service.kind == FlowEntityKind.CLASS
    assert auth_service.parent_entity_id == module.entity_id
    assert auth_service.start_line == 3
    assert login.kind == FlowEntityKind.METHOD
    assert login.parent_entity_id == auth_service.entity_id
    assert record_attempt.kind == FlowEntityKind.FUNCTION
    assert record_attempt.parent_entity_id == login.entity_id
    assert helper.kind == FlowEntityKind.FUNCTION
    assert helper.parent_entity_id == module.entity_id
    assert all(entity.content_hash == file_entity.content_hash for entity in entities)
    assert all(entity.repository_revision == 1 for entity in entities)
    assert login.flow_source_span().path == "backend/auth.py"


def test_entity_ids_survive_line_shifts_and_body_edits(tmp_path: Path) -> None:
    initial = extract(tmp_path, PYTHON_SOURCE)
    edited_source = "# shifted\n" + PYTHON_SOURCE.replace("return True", "return False")
    edited = extract(tmp_path, edited_source, revision=2)
    initial_by_key = {(entity.kind, entity.qualified_name): entity for entity in initial}
    edited_by_key = {(entity.kind, entity.qualified_name): entity for entity in edited}

    assert initial_by_key.keys() == edited_by_key.keys()
    for key, initial_entity in initial_by_key.items():
        edited_entity = edited_by_key[key]
        assert edited_entity.entity_id == initial_entity.entity_id
        assert edited_entity.content_hash != initial_entity.content_hash
        assert edited_entity.repository_revision == 2
    assert edited_by_key[(FlowEntityKind.METHOD, "AuthService.login")].start_line == 6


def test_code_entity_index_resolves_locations_and_searches_symbols(tmp_path: Path) -> None:
    entities = extract(tmp_path, PYTHON_SOURCE, revision=7)
    index = CodeEntityIndex(tmp_path, 7, entities)

    nested = index.find_entity_at_location("backend/auth.py", 7)
    login = index.find_entity_at_location("backend/auth.py", 9)
    search_results = index.search_entities("login")
    file_entities = index.get_file_entities("backend/auth.py")

    assert nested is not None
    assert nested.qualified_name == "AuthService.login.record_attempt"
    assert login is not None
    assert login.qualified_name == "AuthService.login"
    assert search_results[0].qualified_name == "AuthService.login"
    assert file_entities[0].kind == FlowEntityKind.FILE
    assert file_entities[1].kind == FlowEntityKind.MODULE
    assert index.get_entity(login.entity_id) == login
    assert index.find_entity_at_location("backend/missing.py", 1) is None
    assert index.search_entities("") == ()


def test_syntax_error_still_indexes_the_file_and_module(tmp_path: Path) -> None:
    entities = extract(tmp_path, "def broken(:\n")

    assert [entity.kind for entity in entities] == [
        FlowEntityKind.FILE,
        FlowEntityKind.MODULE,
    ]


def test_relationship_facts_have_deterministic_content_derived_ids(tmp_path: Path) -> None:
    entities = extract(tmp_path, PYTHON_SOURCE, revision=7)
    by_name = {entity.qualified_name: entity for entity in entities}
    first = relationship(
        by_name["AuthService.login"],
        target=by_name["AuthService.login.record_attempt"],
    )
    second = relationship(
        by_name["AuthService.login"],
        target=by_name["AuthService.login.record_attempt"],
    )

    assert first.relationship_id.startswith("rel_")
    assert first.relationship_id == second.relationship_id
    assert first.target_entity_id == by_name["AuthService.login.record_attempt"].entity_id
    assert first.unresolved_key is None


def test_relationship_fact_requires_one_consistent_target(tmp_path: Path) -> None:
    source = next(
        entity
        for entity in extract(tmp_path, PYTHON_SOURCE)
        if entity.qualified_name == "AuthService.login"
    )
    provenance = FlowProvenance(
        extractor="sentia.python.ast",
        extractor_version="1.0.0",
        rule_id="python.call.name",
    )

    with pytest.raises(ValueError, match="exactly one target"):
        RelationshipFact.create(
            repository_revision=1,
            source_entity_id=source.entity_id,
            kind=FlowRelationshipKind.CALLS,
            resolution=FlowResolution.STATICALLY_RESOLVED,
            evidence=(source.flow_source_span(),),
            provenance=provenance,
        )

    with pytest.raises(ValueError, match="Only unresolved"):
        RelationshipFact.create(
            repository_revision=1,
            source_entity_id=source.entity_id,
            target_entity_id="ent_missing",
            kind=FlowRelationshipKind.CALLS,
            resolution=FlowResolution.UNRESOLVED,
            evidence=(source.flow_source_span(),),
            provenance=provenance,
        )


def test_relationship_fact_rebinds_evidence_and_identity_to_a_revision(
    tmp_path: Path,
) -> None:
    entities = extract(tmp_path, PYTHON_SOURCE, revision=7)
    by_name = {entity.qualified_name: entity for entity in entities}
    initial = relationship(by_name["AuthService.login"], target=by_name["helper"])

    rebound = initial.at_repository_revision(8)

    assert rebound.repository_revision == 8
    assert {span.repository_revision for span in rebound.evidence} == {8}
    assert rebound.relationship_id != initial.relationship_id


def test_structural_index_indexes_resolved_and_unresolved_relationships(
    tmp_path: Path,
) -> None:
    entities = extract(tmp_path, PYTHON_SOURCE, revision=7)
    by_name = {entity.qualified_name: entity for entity in entities}
    login = by_name["AuthService.login"]
    record_attempt = by_name["AuthService.login.record_attempt"]
    resolved = relationship(login, target=record_attempt)
    unresolved = relationship(
        login,
        unresolved_key="dynamic_handler",
        resolution=FlowResolution.UNRESOLVED,
        rule_id="python.call.dynamic",
    )

    index = StructuralIndex(tmp_path, 7, entities, (unresolved, resolved))

    assert index.get_relationship(resolved.relationship_id) == resolved
    assert {fact.relationship_id for fact in index.outgoing_relationships(login.entity_id)} == {
        resolved.relationship_id,
        unresolved.relationship_id,
    }
    assert index.incoming_relationships(record_attempt.entity_id) == (resolved,)
    assert index.incoming_relationships(login.entity_id) == ()
    assert index.unresolved_relationships() == (unresolved,)
    assert index.unresolved_relationships(login.entity_id) == (unresolved,)
    assert (
        index.outgoing_relationships(
            login.entity_id,
            kinds={FlowRelationshipKind.AWAITS},
        )
        == ()
    )


@pytest.mark.parametrize("invalid_case", ["revision", "source", "target", "evidence"])
def test_structural_index_rejects_relationships_outside_its_revision_bound_facts(
    tmp_path: Path,
    invalid_case: str,
) -> None:
    entities = extract(tmp_path, PYTHON_SOURCE, revision=7)
    by_name = {entity.qualified_name: entity for entity in entities}
    login = by_name["AuthService.login"]
    helper = by_name["helper"]
    fact = relationship(login, target=helper)
    expected_error = ""

    if invalid_case == "revision":
        fact = fact.at_repository_revision(8)
        expected_error = "match the index revision"
    elif invalid_case == "source":
        fact = RelationshipFact.create(
            repository_revision=7,
            source_entity_id="ent_missing",
            target_entity_id=helper.entity_id,
            kind=FlowRelationshipKind.CALLS,
            resolution=FlowResolution.STATICALLY_RESOLVED,
            evidence=fact.evidence,
            provenance=fact.provenance,
        )
        expected_error = "sources must resolve"
    elif invalid_case == "target":
        fact = RelationshipFact.create(
            repository_revision=7,
            source_entity_id=login.entity_id,
            target_entity_id="ent_missing",
            kind=FlowRelationshipKind.CALLS,
            resolution=FlowResolution.STATICALLY_RESOLVED,
            evidence=fact.evidence,
            provenance=fact.provenance,
        )
        expected_error = "targets must resolve"
    else:
        unknown_evidence = FlowSourceSpan(
            path="backend/missing.py",
            start_line=1,
            start_column=0,
            end_line=1,
            end_column=1,
            content_hash="a" * 64,
            repository_revision=7,
        )
        fact = RelationshipFact.create(
            repository_revision=7,
            source_entity_id=login.entity_id,
            target_entity_id=helper.entity_id,
            kind=FlowRelationshipKind.CALLS,
            resolution=FlowResolution.STATICALLY_RESOLVED,
            evidence=(unknown_evidence,),
            provenance=fact.provenance,
        )
        expected_error = "evidence must match"

    with pytest.raises(ValueError, match=expected_error):
        StructuralIndex(tmp_path, 7, entities, (fact,))


def test_structural_index_rejects_duplicate_relationship_ids(tmp_path: Path) -> None:
    entities = extract(tmp_path, PYTHON_SOURCE, revision=7)
    by_name = {entity.qualified_name: entity for entity in entities}
    fact = relationship(by_name["AuthService.login"], target=by_name["helper"])

    with pytest.raises(ValueError, match="Relationship IDs must be unique"):
        StructuralIndex(tmp_path, 7, entities, (fact, fact))


def test_structural_index_revalidates_relationship_identity(tmp_path: Path) -> None:
    entities = extract(tmp_path, PYTHON_SOURCE, revision=7)
    by_name = {entity.qualified_name: entity for entity in entities}
    fact = relationship(by_name["AuthService.login"], target=by_name["helper"])
    tampered = fact.model_copy(update={"relationship_id": "rel_tampered"})

    with pytest.raises(ValueError, match="match their fact identity"):
        StructuralIndex(tmp_path, 7, entities, (tampered,))
