from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

PROTOCOL_VERSION = "1"
SENTIA_VERSION = "0.1.0"


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=lambda value: _to_camel(value), populate_by_name=True)


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class WorkflowState(StrEnum):
    DISCONNECTED = "disconnected"
    STARTING = "starting"
    INDEXING = "indexing"
    READY = "ready"
    DISCUSSING = "discussing"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    PROMPTING = "prompting"
    EXECUTING = "executing"
    TESTING = "testing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkingMode(StrEnum):
    BRAINSTORM = "brainstorm"
    BUILD = "build"


class AgentProvider(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"


class EvidenceRange(CamelModel):
    model_config = ConfigDict(
        alias_generator=lambda value: _to_camel(value),
        populate_by_name=True,
        extra="forbid",
    )

    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    label: str | None = None


class EventEnvelope(CamelModel):
    protocol_version: str = PROTOCOL_VERSION
    event_id: str
    sequence: int = Field(ge=0)
    conversation_id: str | None = None
    run_id: str | None = None
    type: str
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(CamelModel):
    status: str = "ok"
    version: str = SENTIA_VERSION
    protocol_version: str = PROTOCOL_VERSION
    workflow_state: WorkflowState = WorkflowState.READY


class VersionResponse(CamelModel):
    version: str = SENTIA_VERSION
    protocol_version: str = PROTOCOL_VERSION


class ConversationCreate(CamelModel):
    title: str = Field(default="New conversation", min_length=1, max_length=200)
    workspace_id: str | None = None


class ConversationResponse(CamelModel):
    id: str
    title: str
    workspace_id: str | None
    created_at: datetime


class AnthropicCredentialSet(CamelModel):
    api_key: SecretStr = Field(min_length=20)
    verify: bool = Field(default=True, alias="validate")


class AnthropicCredentialStatus(CamelModel):
    connected: bool
    model: str
    message: str | None = None


class DeepgramCredentialSet(CamelModel):
    api_key: SecretStr = Field(min_length=20)


class DeepgramCredentialStatus(CamelModel):
    connected: bool
    message: str | None = None


class OpenAIVoiceCredentialSet(CamelModel):
    api_key: SecretStr = Field(min_length=20)


class OpenAIVoiceCredentialStatus(CamelModel):
    connected: bool
    message: str | None = None


class AgentConnectionStatus(CamelModel):
    provider: AgentProvider
    connected: bool
    authentication: str
    message: str


class AgentLoginStart(CamelModel):
    provider: AgentProvider
    auth_url: str


class RepositoryQuestion(CamelModel):
    workspace_path: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2_000)
    provider: AgentProvider = AgentProvider.CLAUDE


class RepositorySearchQuery(CamelModel):
    workspace_path: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=8, ge=1, le=30)


class RepositorySearchHit(CamelModel):
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    text: str
    score: float


class RepositorySearchResponse(CamelModel):
    hits: list[RepositorySearchHit] = Field(default_factory=list)


class RepositoryGraphRequest(CamelModel):
    workspace_path: str = Field(min_length=1)
    path: str | None = None
    depth: int = Field(default=1, ge=0, le=3)


class RepositoryGraphNode(CamelModel):
    id: str
    label: str
    kind: str
    path: str
    summary: str
    functions: list["RepositoryGraphFunction"] = Field(default_factory=list)
    function_ranges: list["RepositoryGraphFunction"] = Field(default_factory=list)


class RepositoryGraphFunction(CamelModel):
    name: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class RepositoryGraphEdge(CamelModel):
    source: str
    target: str
    edge_type: str
    resolution: str


class RepositoryGraphResponse(CamelModel):
    nodes: list[RepositoryGraphNode] = Field(default_factory=list)
    edges: list[RepositoryGraphEdge] = Field(default_factory=list)


class SpeechRenderRequest(CamelModel):
    workspace_path: str = Field(min_length=1)
    answer: str = Field(min_length=1, max_length=30_000)
    provider: AgentProvider = AgentProvider.CLAUDE


class SpeechRenderResponse(CamelModel):
    spoken_answer: str = Field(min_length=1, max_length=3_000)


class WorkspaceAttach(CamelModel):
    workspace_path: str = Field(min_length=1)


class ProjectSnapshotStatus(CamelModel):
    workspace_name: str
    snapshot_path: str
    created_at: datetime
    updated_at: datetime
    file_count: int = Field(ge=0)
    directory_count: int = Field(ge=0)
    watching: bool


class TokenUsage(CamelModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class RepositoryAnswer(CamelModel):
    answer: str
    spoken_answer: str = Field(min_length=1, max_length=3_000)
    evidence: list[EvidenceRange] = Field(default_factory=list)
    recommended_mode: WorkingMode
    mode_reason: str = Field(min_length=1, max_length=240)
    model: str
    files_scanned: int = Field(ge=0)
    files_read: int = Field(ge=0)
    selected_files: list[str] = Field(default_factory=list)
    usage: TokenUsage
