import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from sentia_sidecar.protocol import (
    FLOW_MAP_SCHEMA_VERSION,
    PROTOCOL_VERSION,
    EventEnvelope,
    FlowInvestigationUsage,
    FlowRootSelection,
    HealthResponse,
    RepositoryInvestigation,
    SentiaFlowGraph,
    TokenUsage,
)

FLOW_FIXTURES = Path(__file__).parents[3] / "tests/fixtures/flow-maps"


def test_python_protocol_uses_shared_camel_case_contract() -> None:
    schema = EventEnvelope.model_json_schema(by_alias=True)
    assert schema["properties"].keys() >= {
        "protocolVersion",
        "eventId",
        "sequence",
        "conversationId",
        "runId",
        "type",
        "createdAt",
        "payload",
    }


def test_health_contract_reports_current_protocol_version() -> None:
    payload = HealthResponse().model_dump(by_alias=True, mode="json")
    assert payload["protocolVersion"] == PROTOCOL_VERSION
    assert payload["workflowState"] == "ready"


def test_python_accepts_shared_flow_graph_fixture() -> None:
    payload = json.loads(
        (FLOW_FIXTURES / "contracts/valid-flow-graph.json").read_text(encoding="utf-8")
    )

    graph = SentiaFlowGraph.model_validate(payload)

    assert graph.schema_version == FLOW_MAP_SCHEMA_VERSION
    assert graph.root_entity_ids == ["ent_ts_submit_login"]
    assert [edge.kind for edge in graph.edges] == [
        "sends_http_request",
        "handles_http_request",
    ]


def test_python_accepts_shared_repository_investigation_fixture() -> None:
    payload = json.loads(
        (FLOW_FIXTURES / "contracts/valid-repository-investigation.json").read_text(
            encoding="utf-8"
        )
    )

    investigation = RepositoryInvestigation.model_validate(payload)

    assert investigation.investigation_id == "inv_login_fixture"
    assert investigation.candidate_identifiers == ["submitLogin", "create_session"]


def test_flow_fixture_hashes_match_its_source_files() -> None:
    payload = json.loads(
        (FLOW_FIXTURES / "contracts/valid-flow-graph.json").read_text(encoding="utf-8")
    )
    graph = SentiaFlowGraph.model_validate(payload)
    source_root = FLOW_FIXTURES / "login-cross-language"

    expected_hashes = {
        span.path: span.content_hash for node in graph.nodes for span in node.source_spans
    }

    for relative, expected in expected_hashes.items():
        actual = hashlib.sha256((source_root / relative).read_bytes()).hexdigest()
        assert actual == expected


def test_flow_root_selection_rejects_duplicate_or_non_entity_roots() -> None:
    base = {
        "schemaVersion": FLOW_MAP_SCHEMA_VERSION,
        "repositoryRevision": 7,
        "viewType": "feature_flow",
        "direction": "forward",
        "rationale": "The login submission is the user-facing entry point.",
        "unresolvedConcepts": [],
    }

    with pytest.raises(ValidationError, match="must be unique"):
        FlowRootSelection.model_validate({**base, "rootEntityIds": ["ent_login", "ent_login"]})
    with pytest.raises(ValidationError):
        FlowRootSelection.model_validate({**base, "rootEntityIds": ["invented_login"]})


def test_flow_investigation_usage_requires_stage_total() -> None:
    with pytest.raises(ValidationError, match="must equal its stage usage"):
        FlowInvestigationUsage(
            file_selection=TokenUsage(input_tokens=10, output_tokens=2),
            initial_anchor_selection=TokenUsage(input_tokens=20, output_tokens=3),
            coverage_pass=TokenUsage(input_tokens=5, output_tokens=1),
            total=TokenUsage(input_tokens=34, output_tokens=6),
        )


def test_flow_graph_rejects_unknown_edge_endpoint_and_stale_evidence() -> None:
    payload = json.loads(
        (FLOW_FIXTURES / "contracts/valid-flow-graph.json").read_text(encoding="utf-8")
    )
    payload["edges"][0]["targetNodeId"] = "node_missing"
    with pytest.raises(ValidationError, match="edge endpoints"):
        SentiaFlowGraph.model_validate(payload)

    payload = json.loads(
        (FLOW_FIXTURES / "contracts/valid-flow-graph.json").read_text(encoding="utf-8")
    )
    payload["edges"][0]["evidence"][0]["repositoryRevision"] = 6
    with pytest.raises(ValidationError, match="must match its repository revision"):
        SentiaFlowGraph.model_validate(payload)


def test_disconnected_flow_anchors_require_an_explanation_warning() -> None:
    payload = json.loads(
        (FLOW_FIXTURES / "contracts/valid-flow-graph.json").read_text(encoding="utf-8")
    )
    second_root = {
        **payload["nodes"][0],
        "id": "node_second_anchor",
        "entityId": "ent_second_anchor",
        "label": "secondAnchor",
        "qualifiedName": "secondAnchor",
    }
    payload["nodes"].append(second_root)
    payload["rootEntityIds"].append("ent_second_anchor")

    with pytest.raises(ValidationError, match="require an explicit explanation"):
        SentiaFlowGraph.model_validate(payload)

    payload["warnings"].append(
        {
            "code": "disconnected_feature_anchors",
            "message": "No evidence-backed path connects the selected anchors.",
            "entityIds": payload["rootEntityIds"],
        }
    )
    graph = SentiaFlowGraph.model_validate(payload)
    assert graph.root_entity_ids == ["ent_ts_submit_login", "ent_second_anchor"]
