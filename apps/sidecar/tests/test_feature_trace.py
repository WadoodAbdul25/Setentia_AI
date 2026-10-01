from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError
from sentia_sidecar.anthropic_service import AnthropicRepositoryService
from sentia_sidecar.app import create_app
from sentia_sidecar.codex_service import CodexRepositoryService
from sentia_sidecar.feature_trace import (
    FeatureTraceEngine,
    TraceDecision,
    TracePolicy,
    TraceTurn,
    materialize_stages,
    resolve_stage_paths,
)
from sentia_sidecar.protocol import AgentProvider, FlowMapResponse, TokenUsage
from sentia_sidecar.repository import build_repository_manifest
from sentia_sidecar.repository_intelligence import RepositoryIntelligenceRouter
from sentia_sidecar.structural_index import StructuralIndex
from sentia_sidecar.trace_evidence import TraceEvidenceReader

FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/feature-traces/workflow"


@pytest.fixture
def workflow(tmp_path: Path):
    shutil.copytree(FIXTURE, tmp_path / "workflow")
    manifest = build_repository_manifest(str(tmp_path))
    index = StructuralIndex(
        manifest.root, manifest.repository_revision, manifest.entities, manifest.relationships
    )
    return manifest, index, {entity.name: entity for entity in index.entities}


def plan(entities: dict[str, Any], *, sufficient: bool = False) -> dict[str, Any]:
    result = {
        "featureSpecification": {
            "actor": "User",
            "input": "Seed content",
            "behavior": "Generate report content",
            "outcome": "Persist generated report",
            "alternatives": ["Initialize blank report"],
            "openQuestions": [],
        },
        "scope": "feature",
        "requestedOutcome": "Generate and persist report content from the submitted seed.",
        "stages": [
            {"id": name, "label": label, "role": role, "entityIds": [entities[name].entity_id]}
            for name, label, role in [
                ("submit", "Receive seed", "entry"),
                ("generate_report", "Dispatch generation task", "orchestration"),
                ("run_workflow", "Invoke workflow", "orchestration"),
                ("generate", "Generate content", "transformation"),
                ("validate", "Validate content", "transformation"),
                ("persist", "Save generated report", "outcome"),
            ]
        ],
        "readEntityIds": [],
        "searchQueries": [],
        "sufficient": sufficient,
        "missingConcepts": [],
        "rationale": "Follow the request through the task and workflow to persisted content.",
    }
    result["candidateDecisions"] = assessments(entities, result["stages"])
    return result


def assessments(entities, stages):
    by_id = {entity.entity_id: entity for entity in entities.values()}
    return [
        {
            "entityId": stage["entityIds"][0],
            "decision": "include",
            "responsibility": stage["label"],
            "reason": "Controls this part of report generation.",
            "question": "",
            "evidence": [
                {
                    "startLine": by_id[stage["entityIds"][0]].start_line,
                    "endLine": by_id[stage["entityIds"][0]].end_line,
                }
            ],
        }
        for stage in stages
    ]


async def test_trace_reviews_six_anchors_and_resolves_framework_boundaries(workflow):
    manifest, index, entities = workflow
    calls: list[tuple[str, str]] = []

    async def model(prompt: str, schema: type[BaseModel], stage: str) -> TraceTurn:
        calls.append((stage, prompt))
        assert issubclass(schema, TraceDecision)
        assert "STACK PROFILE" in prompt and "Celery" in prompt
        return TraceTurn(
            output=plan(entities, sufficient=len(calls) > 1),
            usage=TokenUsage(input_tokens=100, output_tokens=20),
        )

    result = await FeatureTraceEngine().investigate(
        "Trace seed to a generated report",
        manifest,
        index,
        provider=AgentProvider.CODEX,
        model="test",
        run_model=model,
    )
    assert [stage for stage, _ in calls] == ["planning", "review"]
    assert len(result.roots.selection.root_entity_ids) == 6
    assert result.trace.status == "supported", result.trace.gaps
    assert result.trace.stop_reason == "coverage_satisfied"
    assert result.roots.usage.input_tokens == 200
    assert sum(item.output_tokens for item in result.trace.usage) == 40
    pairs = {(item.source_stage_id, item.target_stage_id) for item in result.trace.transitions}
    assert ("submit", "generate_report") in pairs
    assert ("run_workflow", "generate") in pairs
    assert ("generate", "validate") in pairs
    assert ("validate", "persist") in pairs
    for transition in result.trace.transitions:
        assert all(index.get_relationship(rid) for rid in transition.relationship_ids)
        assert all(
            span.repository_revision == manifest.repository_revision for span in transition.evidence
        )
    assert "TARGETED REPOSITORY EVIDENCE" in calls[1][1]


async def test_registration_and_blank_initialization_do_not_prove_generation(workflow):
    manifest, index, entities = workflow
    bad = plan(entities)
    bad["stages"] = [
        {"id": name, "label": name, "role": role, "entityIds": [entities[name].entity_id]}
        for name, role in [
            ("build_workflow", "entry"),
            ("generate", "transformation"),
            ("initialize_empty", "outcome"),
        ]
    ]
    bad["candidateDecisions"] = assessments(entities, bad["stages"])

    async def model(prompt: str, schema: type[BaseModel], stage: str) -> TraceTurn:
        return TraceTurn(
            output={**bad, "sufficient": True}, usage=TokenUsage(input_tokens=5, output_tokens=3)
        )

    result = await FeatureTraceEngine().investigate(
        "Generate content",
        manifest,
        index,
        provider=AgentProvider.CODEX,
        model="test",
        run_model=model,
    )
    assert result.trace.status == "partial"
    assert any(gap.code == "unresolved_transition" for gap in result.trace.gaps)
    assert any(item.kind == "registration" for item in result.trace.transitions)


async def test_review_failure_preserves_partial_trace_and_charged_usage(workflow):
    manifest, index, entities = workflow

    async def model(prompt: str, schema: type[BaseModel], stage: str) -> TraceTurn:
        output = plan(entities) if stage == "planning" else "not json"
        return TraceTurn(output=output, usage=TokenUsage(input_tokens=100, output_tokens=10))

    result = await FeatureTraceEngine().investigate(
        "Generate report",
        manifest,
        index,
        provider=AgentProvider.CODEX,
        model="test",
        run_model=model,
    )
    assert result.trace.status == "partial"
    assert result.trace.stop_reason == "review_failed"
    assert result.roots.usage.input_tokens == 200
    assert result.trace.stages == []
    assert result.trace.transitions == []
    assert all(not candidate.source_reviewed for candidate in result.trace.candidates)


async def test_review_timeout_is_bounded(workflow):
    manifest, index, entities = workflow

    async def model(prompt: str, schema: type[BaseModel], stage: str) -> TraceTurn:
        if stage != "planning":
            await asyncio.sleep(1)
        return TraceTurn(
            output=plan(entities), usage=TokenUsage(input_tokens=100, output_tokens=10)
        )

    result = await FeatureTraceEngine(TracePolicy(turn_timeout_seconds=0.01)).investigate(
        "Generate report",
        manifest,
        index,
        provider=AgentProvider.CODEX,
        model="test",
        run_model=model,
    )
    assert result.trace.stop_reason == "review_failed"
    assert len(result.trace.usage) == 1


async def test_unknown_anchor_becomes_gap_without_discarding_valid_candidates(workflow):
    manifest, index, entities = workflow
    invalid = plan(entities)
    invalid["stages"][0]["entityIds"] = ["ent_invented"]

    async def model(prompt: str, schema: type[BaseModel], stage: str) -> TraceTurn:
        return TraceTurn(output=invalid, usage=TokenUsage(input_tokens=1, output_tokens=1))

    result = await FeatureTraceEngine().investigate(
        "Generate report",
        manifest,
        index,
        provider=AgentProvider.CODEX,
        model="test",
        run_model=model,
    )
    assert result.trace.status == "partial"
    assert "ent_invented" not in result.roots.selection.root_entity_ids
    assert all("ent_invented" not in stage.entity_ids for stage in result.trace.stages)
    assert any("out-of-catalog" in gap.message for gap in result.trace.gaps)
    assert len(result.trace.usage) <= 3


def test_depth_limit_is_a_gap(workflow):
    manifest, index, entities = workflow
    decision = TraceDecision.model_validate(plan(entities))
    context = TraceEvidenceReader(manifest, index).read_symbols(
        [entity_id for stage in decision.stages for entity_id in stage.entity_ids]
    )
    stages, _ = materialize_stages(decision, index, context)
    _, gaps = resolve_stage_paths(index, stages, TracePolicy(max_visited_entities=1))
    assert any(gap.code == "search_bounded" for gap in gaps)


async def test_planning_reference_error_recovers_in_existing_review_budget(workflow):
    manifest, index, entities = workflow
    calls = []

    async def model(prompt, schema, stage):
        calls.append(stage)
        output = plan(entities, sufficient=stage != "planning")
        if stage == "planning":
            output["candidateDecisions"].append(
                {
                    **output["candidateDecisions"][0],
                    "entityId": "ent_missing",
                }
            )
            output["readEntityIds"] = ["ent_missing"]
        else:
            assert "out-of-catalog" in prompt
        return TraceTurn(output=output, usage=TokenUsage(input_tokens=10, output_tokens=5))

    result = await FeatureTraceEngine().investigate(
        "Generate report",
        manifest,
        index,
        provider=AgentProvider.CODEX,
        model="test",
        run_model=model,
    )
    assert calls == ["planning", "review"]
    assert result.trace.status == "supported"
    assert result.roots.usage.input_tokens == 20
    assert all(candidate.entity_id != "ent_missing" for candidate in result.trace.candidates)


def test_collapsed_path_preserves_helpers_and_async_boundary(workflow):
    manifest, index, entities = workflow
    value = plan(entities)
    value["stages"] = [
        stage for stage in value["stages"] if stage["id"] in {"submit", "generate", "persist"}
    ]
    selected = {stage["entityIds"][0] for stage in value["stages"]}
    for candidate in value["candidateDecisions"]:
        if candidate["entityId"] not in selected:
            candidate["decision"] = "supporting"
    decision = TraceDecision.model_validate(value)
    context = TraceEvidenceReader(manifest, index).read_symbols(
        [candidate.entity_id for candidate in decision.candidate_decisions]
    )
    stages, _ = materialize_stages(decision, index, context)
    connections, _ = resolve_stage_paths(index, stages, TracePolicy())
    connection = next(
        item
        for item in connections
        if item.source_stage_id == "submit" and item.target_stage_id == "generate"
    )
    assert entities["generate_report"].entity_id in connection.via_entity_ids
    assert connection.asynchronous
    assert len(connection.relationship_ids) == len(connection.via_entity_ids) + 1
    filtered, _ = resolve_stage_paths(
        index, stages, TracePolicy(), excluded_ids={entities["generate_report"].entity_id}
    )
    assert not any(
        item.source_stage_id == "submit" and item.target_stage_id == "generate" for item in filtered
    )


async def test_overview_is_not_claimed_complete_from_one_trace(workflow):
    manifest, index, entities = workflow

    async def model(prompt: str, schema: type[BaseModel], stage: str) -> TraceTurn:
        return TraceTurn(
            output={**plan(entities, sufficient=True), "scope": "overview"},
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )

    result = await FeatureTraceEngine().investigate(
        "Map the entire backend",
        manifest,
        index,
        provider=AgentProvider.CODEX,
        model="test",
        run_model=model,
    )
    assert result.trace.status == "partial"
    assert any(gap.code == "scope_not_inventoried" for gap in result.trace.gaps)


def test_shared_v2_transport_fixture():
    fixture = (
        Path(__file__).resolve().parents[3] / "tests/fixtures/feature-traces/valid-response.json"
    )
    payload = json.loads(fixture.read_text())
    parsed = FlowMapResponse.model_validate(payload)
    assert parsed.feature_trace is not None
    assert parsed.feature_trace.schema_version == "2"


async def test_source_change_during_review_rejects_stale_trace(workflow):
    manifest, index, entities = workflow

    async def model(prompt: str, schema: type[BaseModel], stage: str) -> TraceTurn:
        if stage != "planning":
            source = manifest.root / entities["persist"].path
            source.write_text(source.read_text() + "\n# code changed during investigation\n")
        return TraceTurn(
            output=plan(entities, sufficient=stage != "planning"),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )

    with pytest.raises(ValueError, match="stale"):
        await FeatureTraceEngine().investigate(
            "Generate report",
            manifest,
            index,
            provider=AgentProvider.CODEX,
            model="test",
            run_model=model,
        )


@pytest.mark.parametrize("provider", [AgentProvider.CODEX, AgentProvider.CLAUDE])
def test_v2_api_uses_shared_engine_for_both_providers(
    workflow, settings, auth_headers, monkeypatch, provider
):
    manifest, _, entities = workflow
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return plan(entities, sufficient="CURRENT PLAN" in prompt)

    if provider == AgentProvider.CODEX:
        service = CodexRepositoryService("test-model")

        async def client():
            return object(), False

        async def structured(client, *, instructions, prompt, response_model):
            assert "feature trace investigator" in instructions
            return SimpleNamespace(
                final_response=json.dumps(answer(prompt)),
                usage=SimpleNamespace(last=SimpleNamespace(input_tokens=100, output_tokens=20)),
            )

        monkeypatch.setattr(service, "_codex_client", client)
        monkeypatch.setattr(service, "_run_structured", structured)
    else:

        async def query(*, prompt, options):
            assert "feature trace investigator" in options.system_prompt
            assert options.tools == []
            assert options.permission_mode == "dontAsk"
            schema = options.output_format["schema"]
            assert "CatalogEntityId" in schema["$defs"]
            assert entities["submit"].entity_id in schema["$defs"]["CatalogEntityId"]["enum"]
            output = answer(prompt)
            # Reproduce the reported failure: two invalid StructuredOutput calls
            # with empty anchors, followed by a corrected submission. Exercise
            # the real adapter instead of replacing _run_structured_turn.
            planning = "CURRENT PLAN" not in prompt
            for attempt in range(options.max_turns):
                candidate = json.loads(json.dumps(output))
                if planning and attempt < 2:
                    candidate["candidateDecisions"][2]["evidence"][0]["startLine"] = 0
                tool_id = f"decision-{attempt}"
                yield AssistantMessage(
                    content=[ToolUseBlock(id=tool_id, name="StructuredOutput", input=candidate)],
                    model="test-model",
                    message_id=f"message-{attempt}",
                    usage={"input_tokens": 100, "output_tokens": 20},
                )
                try:
                    TraceDecision.model_validate(candidate)
                except ValidationError:
                    yield UserMessage(content=[ToolResultBlock(tool_use_id=tool_id, is_error=True)])
                    continue
                yield ResultMessage(
                    subtype="success",
                    duration_ms=10,
                    duration_api_ms=10,
                    is_error=False,
                    num_turns=attempt + 1,
                    session_id="fixture-session",
                    structured_output=candidate,
                    usage={
                        "input_tokens": 100 * (attempt + 1),
                        "output_tokens": 20 * (attempt + 1),
                    },
                )
                return
            yield ResultMessage(
                subtype="error_max_turns",
                duration_ms=10,
                duration_api_ms=10,
                is_error=True,
                num_turns=options.max_turns + 1,
                session_id="fixture-session",
                errors=["Reached maximum number of turns"],
            )
            raise RuntimeError("SDK exits after error result")

        service = AnthropicRepositoryService("test-model", query_function=query)
        service._api_key = "fixture-key"  # pragma: allowlist secret

    with TestClient(
        create_app(settings, repository_router=RepositoryIntelligenceRouter([service]))
    ) as client:
        response = client.post(
            "/api/v1/repository/flow-maps",
            headers=auth_headers,
            json={
                "workspacePath": str(manifest.root),
                "question": "Trace seed to saved report",
                "provider": provider.value,
                "engineVersion": "2",
            },
        )
    assert response.status_code == 200, response.text
    result = FlowMapResponse.model_validate(response.json())
    assert result.feature_trace is not None
    assert result.feature_trace.status == "supported"
    assert result.graph.schema_version == "2"
    assert result.investigation.schema_version == "2"
    assert len(result.root_selection.root_entity_ids) == 6
    assert result.usage.input_tokens == (400 if provider == AgentProvider.CLAUDE else 200)
    assert len(prompts) == 2
