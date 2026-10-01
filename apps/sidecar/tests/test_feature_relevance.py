"""Contract regressions with scripted semantic judgments, not live-model accuracy tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sentia_sidecar.feature_trace import (
    FeatureTraceEngine,
    TraceDecision,
    TraceTurn,
    catalog_decision_schema,
    constrain_decision,
    materialize_stages,
    trace_candidates,
)
from sentia_sidecar.protocol import AgentProvider, TokenUsage
from sentia_sidecar.repository import build_repository_manifest
from sentia_sidecar.structural_index import StructuralIndex
from sentia_sidecar.trace_evidence import TraceEvidenceReader

FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/feature-traces/alternatives"


@pytest.fixture
def alternatives(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / "app")
    manifest = build_repository_manifest(str(tmp_path))
    index = StructuralIndex(
        manifest.root, manifest.repository_revision, manifest.entities, manifest.relationships
    )
    return manifest, index, {entity.name: entity for entity in index.entities}


def decision(entities, names, *, planning=False):
    candidates = []
    stages = []
    for name, disposition, role in names:
        entity = entities[name]
        candidates.append(
            {
                "entityId": entity.entity_id,
                "decision": "uncertain" if planning else disposition,
                "responsibility": name,
                "reason": f"Compare {name} to the requested input and outcome.",
                "question": "Which behavior does this implement?" if planning else "",
                "evidence": []
                if planning
                else [{"startLine": entity.start_line, "endLine": entity.end_line}],
            }
        )
        if disposition == "include" and not planning:
            stages.append(
                {"id": name, "label": name, "role": role, "entityIds": [entity.entity_id]}
            )
    return {
        "featureSpecification": {
            "actor": "Visitor",
            "input": "Submitted input",
            "behavior": "Perform requested behavior",
            "outcome": "Requested result",
            "alternatives": [name for name, kind, _ in names if kind == "exclude"],
            "openQuestions": [],
        },
        "candidateDecisions": candidates,
        "scope": "feature",
        "requestedOutcome": "Requested result",
        "stages": stages,
        "readEntityIds": [],
        "searchQueries": [],
        "sufficient": not planning,
        "missingConcepts": [],
        "rationale": "Compare behavior using inspected source.",
    }


@pytest.mark.parametrize(
    "question,names",
    [
        (
            "Let returning users access their account",
            [
                ("sign_in", "include", "entry"),
                ("verify_credentials", "supporting", "transformation"),
                ("issue_session", "include", "outcome"),
                ("google_oauth_calendar", "exclude", "outcome"),
                ("authenticate_internal_service", "exclude", "entry"),
            ],
        ),
        (
            "Create an account and establish a session",
            [
                ("sign_up", "include", "entry"),
                ("create_account", "supporting", "transformation"),
                ("issue_session", "include", "outcome"),
                ("google_oauth_calendar", "exclude", "outcome"),
            ],
        ),
        (
            "Generate document content from seed",
            [
                ("submit_seed", "include", "entry"),
                ("generate_sections", "include", "transformation"),
                ("save_draft", "include", "outcome"),
                ("record_generation_event", "supporting", "outcome"),
                ("generate_sharing_email", "exclude", "entry"),
                ("generate_prototype", "exclude", "transformation"),
                ("finalize_document", "exclude", "outcome"),
            ],
        ),
    ],
)
async def test_competing_implementations_are_read_but_excluded_from_stages(
    alternatives, question, names
):
    manifest, index, entities = alternatives
    turns = []

    async def model(prompt, schema, stage):
        turns.append(stage)
        assert issubclass(schema, TraceDecision)
        if stage != "planning":
            # Verify every alternative was actually inspected, not merely catalogued.
            for name, _, _ in names:
                assert f"def {name}(" in prompt
        return TraceTurn(
            output=decision(entities, names, planning=stage == "planning"),
            usage=TokenUsage(input_tokens=100, output_tokens=30),
        )

    result = await FeatureTraceEngine().investigate(
        question, manifest, index, provider=AgentProvider.CODEX, model="scripted", run_model=model
    )
    assert turns == ["planning", "review"]
    assert result.trace.status == "supported", result.trace.gaps
    assert {stage.id for stage in result.trace.stages} == {
        name for name, kind, _ in names if kind == "include"
    }
    assert all(candidate.source_reviewed for candidate in result.trace.candidates)
    assert len(result.trace.candidates) == len(names)
    assert result.roots.usage.input_tokens == 200


@pytest.mark.parametrize("defect", ["outside_function", "unread", "excluded", "supporting"])
def test_unjustified_or_incidental_stage_cannot_be_promoted(alternatives, defect):
    manifest, index, entities = alternatives
    value = decision(
        entities, [("sign_in", "include", "entry"), ("issue_session", "include", "outcome")]
    )
    candidate = value["candidateDecisions"][0]
    if defect == "outside_function":
        candidate["evidence"] = [{"startLine": 1, "endLine": 1}]
    if defect in {"excluded", "supporting"}:
        candidate["decision"] = "exclude" if defect == "excluded" else "supporting"
    ids = [entities["issue_session"].entity_id]
    if defect != "unread":
        ids.append(entities["sign_in"].entity_id)
    context = TraceEvidenceReader(manifest, index).read_symbols(ids)
    stages, gaps = materialize_stages(TraceDecision.model_validate(value), index, context)
    assert "sign_in" not in {stage.id for stage in stages}
    assert any(gap.code == "unverified_candidate" for gap in gaps)


def test_stage_uses_reviewed_responsibility_not_unrelated_label(alternatives):
    manifest, index, entities = alternatives
    value = decision(entities, [("save_draft", "include", "outcome")])
    value["stages"][0]["label"] = "Automatically finalize document"
    context = TraceEvidenceReader(manifest, index).read_symbols([entities["save_draft"].entity_id])
    stages, _ = materialize_stages(TraceDecision.model_validate(value), index, context)
    assert stages[0].label == "save_draft"


def test_audit_behavior_is_not_removed_by_a_logging_blacklist(alternatives):
    manifest, index, entities = alternatives
    value = decision(entities, [("record_generation_event", "include", "outcome")])
    value["featureSpecification"]["behavior"] = "Record generation audit events"
    context = TraceEvidenceReader(manifest, index).read_symbols(
        [entities["record_generation_event"].entity_id]
    )
    stages, _ = materialize_stages(TraceDecision.model_validate(value), index, context)
    assert stages[0].entity_ids == [entities["record_generation_event"].entity_id]


def test_structural_neighbors_survive_a_small_candidate_budget(alternatives):
    _, index, entities = alternatives
    candidates = trace_candidates(
        index,
        ["generate prototype sharing email"],
        anchor_ids=[entities["sign_in"].entity_id],
        limit=3,
    )
    assert {entity.name for entity in candidates} == {
        "sign_in",
        "verify_credentials",
        "issue_session",
    }


def test_search_hints_are_bounded_in_schema_and_normalized_locally(alternatives):
    _, _, entities = alternatives
    value = decision(entities, [("sign_in", "include", "entry")], planning=True)
    value["searchQueries"] = ["", "   ", " sign in ", "x" * 450, "sign in"]
    parsed = TraceDecision.model_validate(value)
    assert parsed.search_queries == ["sign in", "x" * 300]
    item_schema = TraceDecision.model_json_schema(by_alias=True)["properties"]["searchQueries"][
        "items"
    ]
    assert item_schema["minLength"] == 1
    assert item_schema["maxLength"] == 300


def test_catalog_schema_bounds_every_symbol_reference(alternatives):
    _, _, entities = alternatives
    candidates = [entities["sign_in"], entities["issue_session"]]
    schema_type = catalog_decision_schema(candidates)
    for alias in (True, False):
        schema = schema_type.model_json_schema(by_alias=alias)
        definitions = schema["$defs"]
        assert definitions["CatalogEntityId"]["enum"] == [item.entity_id for item in candidates]
        reference = {"$ref": "#/$defs/CatalogEntityId"}
        assert (
            definitions["CandidateDecision"]["properties"]["entityId" if alias else "entity_id"]
            == reference
        )
        assert (
            definitions["PlannedStage"]["properties"]["entityIds" if alias else "entity_ids"][
                "items"
            ]
            == reference
        )
        assert (
            schema["properties"]["readEntityIds" if alias else "read_entity_ids"]["items"]
            == reference
        )
    # Each request has its own catalog; building another cannot mutate the first.
    other = catalog_decision_schema([entities["google_oauth_calendar"]]).model_json_schema()
    assert other["$defs"]["CatalogEntityId"]["enum"] != schema["$defs"]["CatalogEntityId"]["enum"]
    assert schema_type.model_json_schema()["$defs"]["CatalogEntityId"]["enum"] == [
        item.entity_id for item in candidates
    ]


def test_unknown_candidates_and_reads_never_reach_source_reader(alternatives):
    _, _, entities = alternatives
    candidates = [entities["sign_in"], entities["issue_session"]]
    value = decision(entities, [("sign_in", "include", "entry")])
    value["candidateDecisions"][0]["entityId"] = "ent_missing"
    value["stages"][0]["entityIds"] = ["ent_missing"]
    value["readEntityIds"] = ["ent_missing", entities["issue_session"].entity_id]
    repaired = constrain_decision(TraceDecision.model_validate(value), candidates)
    assert repaired.read_entity_ids == [entities["issue_session"].entity_id]
    assert repaired.stages == []
    assert repaired.candidate_decisions[0].entity_id == entities["sign_in"].entity_id
    assert repaired.candidate_decisions[0].decision == "uncertain"
    assert repaired.candidate_decisions[0].evidence == []
    assert not repaired.sufficient
