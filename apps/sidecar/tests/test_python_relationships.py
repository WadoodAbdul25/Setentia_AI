from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from sentia_sidecar.protocol import FlowRelationshipKind, FlowResolution
from sentia_sidecar.python_relationships import extract_python_relationships
from sentia_sidecar.structural_index import (
    CodeEntity,
    RelationshipFact,
    StructuralIndex,
    extract_python_entities,
)


def build_index(
    root: Path,
    sources: Mapping[str, str],
    *,
    revision: int = 1,
) -> StructuralIndex:
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


def by_name(index: StructuralIndex, qualified_name: str, *, path: str) -> CodeEntity:
    return next(
        entity
        for entity in index.get_file_entities(path)
        if entity.qualified_name == qualified_name
    )


def outgoing(index: StructuralIndex, entity: CodeEntity) -> tuple[RelationshipFact, ...]:
    return index.outgoing_relationships(entity.entity_id)


def target_name(index: StructuralIndex, fact: RelationshipFact) -> str:
    assert fact.target_entity_id is not None
    target = index.get_entity(fact.target_entity_id)
    assert target is not None
    return target.qualified_name


def test_python_calls_constructs_awaits_and_self_methods_are_extracted(
    tmp_path: Path,
) -> None:
    source = """\
class Session:
    pass

async def load_user():
    return None

class AuthService:
    def validate(self) -> bool:
        return True

    async def login(self, handler, enabled: bool):
        self.validate()
        session = Session()
        if enabled:
            await load_user()
        handler()
        return session
"""
    index = build_index(tmp_path, {"auth.py": source}, revision=7)
    login = by_name(index, "AuthService.login", path="auth.py")
    facts = outgoing(index, login)
    targets = {
        target_name(index, fact): fact for fact in facts if fact.target_entity_id is not None
    }

    assert targets["AuthService.validate"].kind == FlowRelationshipKind.CALLS
    assert targets["AuthService.validate"].provenance.rule_id == "python.call.self_method"
    assert targets["Session"].kind == FlowRelationshipKind.CONSTRUCTS
    assert targets["Session"].provenance.rule_id == "python.construct.lexical_name"
    assert targets["load_user"].kind == FlowRelationshipKind.AWAITS
    assert targets["load_user"].asynchronous is True
    assert targets["load_user"].conditional is True
    unresolved = next(fact for fact in facts if fact.resolution == FlowResolution.UNRESOLVED)
    assert unresolved.unresolved_key == "handler"
    assert unresolved.provenance.rule_id == "python.call.dynamic"
    assert unresolved.conditional is False

    await_fact = targets["load_user"]
    assert await_fact.repository_revision == 7
    assert await_fact.evidence[0].path == "auth.py"
    assert await_fact.evidence[0].start_line == 15
    line = source.splitlines()[await_fact.evidence[0].start_line - 1]
    assert (
        line[await_fact.evidence[0].start_column : await_fact.evidence[0].end_column]
        == "load_user()"
    )


def test_nearest_nested_function_wins_while_parameter_shadowing_stays_unresolved(
    tmp_path: Path,
) -> None:
    source = """\
def helper() -> str:
    return "module"

def outer() -> str:
    def helper() -> str:
        return "nested"
    return helper()

def injected(helper):
    return helper()
"""
    index = build_index(tmp_path, {"service.py": source})
    outer = by_name(index, "outer", path="service.py")
    nested = by_name(index, "outer.helper", path="service.py")
    injected = by_name(index, "injected", path="service.py")

    assert outgoing(index, outer)[0].target_entity_id == nested.entity_id
    assert outgoing(index, injected)[0].resolution == FlowResolution.UNRESOLVED
    assert outgoing(index, injected)[0].unresolved_key == "helper"


def test_forward_function_references_resolve_after_the_entity_pass(tmp_path: Path) -> None:
    source = """\
def run():
    return helper()

def helper():
    return True
"""
    index = build_index(tmp_path, {"forward.py": source})
    run = by_name(index, "run", path="forward.py")
    helper = by_name(index, "helper", path="forward.py")

    assert outgoing(index, run)[0].target_entity_id == helper.entity_id


def test_explicit_import_aliases_resolve_across_indexed_modules(tmp_path: Path) -> None:
    sources = {
        "helpers.py": """\
class Session:
    pass

def load_user() -> str:
    return "user"
""",
        "app.py": """\
import helpers as helper_module
from helpers import load_user as fetch_user

def login():
    helper_module.load_user()
    fetch_user()
    return helper_module.Session()
""",
    }
    index = build_index(tmp_path, sources)
    login = by_name(index, "login", path="app.py")
    facts = outgoing(index, login)
    targets = [
        index.get_entity(fact.target_entity_id)
        for fact in facts
        if fact.target_entity_id is not None
    ]

    assert len(facts) == 3
    assert [target.qualified_name for target in targets if target is not None].count(
        "load_user"
    ) == 2
    assert any(fact.provenance.rule_id == "python.call.module_attribute" for fact in facts)
    assert any(fact.provenance.rule_id == "python.call.imported_symbol" for fact in facts)
    session_fact = next(
        fact
        for fact in facts
        if fact.target_entity_id is not None and target_name(index, fact) == "Session"
    )
    assert session_fact.kind == FlowRelationshipKind.CONSTRUCTS


def test_relative_imported_symbol_resolves_without_executing_imports(tmp_path: Path) -> None:
    sources = {
        "package/helpers.py": "def load_user() -> str:\n    return 'user'\n",
        "package/service.py": (
            "from .helpers import load_user\n\ndef login() -> str:\n    return load_user()\n"
        ),
    }
    index = build_index(tmp_path, sources)
    login = by_name(index, "login", path="package/service.py")
    fact = outgoing(index, login)[0]

    assert (
        fact.target_entity_id
        == by_name(
            index,
            "load_user",
            path="package/helpers.py",
        ).entity_id
    )
    assert fact.kind == FlowRelationshipKind.CALLS


def test_duplicate_declarations_and_dynamic_attributes_remain_unresolved(
    tmp_path: Path,
) -> None:
    source = """\
def helper():
    return 1

def helper():
    return 2

def run(service):
    helper()
    service.execute()
"""
    index = build_index(tmp_path, {"ambiguous.py": source})
    run = by_name(index, "run", path="ambiguous.py")
    facts = outgoing(index, run)

    assert {fact.unresolved_key for fact in facts} == {"helper", "service.execute"}
    assert all(fact.resolution == FlowResolution.UNRESOLVED for fact in facts)


def test_relationship_extraction_is_deterministic_and_syntax_errors_are_partial(
    tmp_path: Path,
) -> None:
    valid_source = "def helper():\n    return 1\n\ndef run():\n    return helper()\n"
    sources = {
        "valid.py": valid_source,
        "broken.py": "def broken(:\n",
    }

    first = build_index(tmp_path, sources, revision=3)
    second = build_index(tmp_path, sources, revision=3)

    assert first.relationships == second.relationships
    assert len(first.relationships) == 1
    assert first.relationships[0].relationship_id.startswith("rel_")
    assert first.relationships[0].evidence[0].repository_revision == 3
    assert first.get_file_entities("broken.py")
