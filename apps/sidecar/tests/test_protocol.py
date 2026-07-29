from sentia_sidecar.protocol import PROTOCOL_VERSION, EventEnvelope, HealthResponse


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
