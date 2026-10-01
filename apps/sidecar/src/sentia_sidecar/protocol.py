from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

PROTOCOL_VERSION = "1"
SENTIA_VERSION = "0.1.0"
FLOW_MAP_SCHEMA_VERSION = "1"


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=lambda value: _to_camel(value), populate_by_name=True)


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class StrictCamelModel(CamelModel):
    model_config = ConfigDict(
        alias_generator=lambda value: _to_camel(value),
        populate_by_name=True,
        extra="forbid",
    )


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


class FlowMapRequest(CamelModel):
    workspace_path: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2_000)
    provider: AgentProvider = AgentProvider.CLAUDE
    engine_version: Literal["1", "2"] = "1"
    max_depth: int = Field(default=5, ge=0, le=25)
    max_nodes: int = Field(default=50, ge=1, le=500)
    max_edges: int = Field(default=2_000, ge=1, le=2_000)
    max_branches_per_node: int = Field(default=8, ge=1, le=100)


class SpeechRenderRequest(CamelModel):
    workspace_path: str = Field(min_length=1)
    answer: str = Field(min_length=1, max_length=30_000)
    provider: AgentProvider = AgentProvider.CLAUDE


class SpeechRenderResponse(CamelModel):
    spoken_answer: str = Field(min_length=1, max_length=3_000)


class WorkspaceAttach(CamelModel):
    workspace_path: str = Field(min_length=1)


class ProjectSnapshotStatus(CamelModel):
    repository_revision: int = Field(ge=1)
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


class FlowInvestigationUsage(StrictCamelModel):
    file_selection: TokenUsage
    initial_anchor_selection: TokenUsage
    coverage_pass: TokenUsage
    total: TokenUsage

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        expected_input = (
            self.file_selection.input_tokens
            + self.initial_anchor_selection.input_tokens
            + self.coverage_pass.input_tokens
        )
        expected_output = (
            self.file_selection.output_tokens
            + self.initial_anchor_selection.output_tokens
            + self.coverage_pass.output_tokens
        )
        if self.total.input_tokens != expected_input or self.total.output_tokens != expected_output:
            raise ValueError("Flow investigation usage total must equal its stage usage.")
        return self


class FlowEntityKind(StrEnum):
    REPOSITORY = "repository"
    DIRECTORY = "directory"
    PACKAGE = "package"
    PROCESS = "process"
    FILE = "file"
    MODULE = "module"
    CLASS = "class"
    INTERFACE = "interface"
    FUNCTION = "function"
    METHOD = "method"
    COMPONENT = "component"
    HTTP_ENDPOINT = "http_endpoint"
    MESSAGE_CHANNEL = "message_channel"
    EXTERNAL = "external"
    UNRESOLVED = "unresolved"


class FlowRelationshipKind(StrEnum):
    CONTAINS = "contains"
    IMPORTS = "imports"
    EXPORTS = "exports"
    CALLS = "calls"
    CONSTRUCTS = "constructs"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    DECORATES = "decorates"
    AWAITS = "awaits"
    RETURNS = "returns"
    RAISES = "raises"
    REGISTERS_HANDLER = "registers_handler"
    EMITS_MESSAGE = "emits_message"
    HANDLES_MESSAGE = "handles_message"
    SENDS_HTTP_REQUEST = "sends_http_request"
    HANDLES_HTTP_REQUEST = "handles_http_request"
    PUBLISHES_EVENT = "publishes_event"
    CONSUMES_EVENT = "consumes_event"


TRAVERSABLE_FLOW_RELATIONSHIP_KINDS = frozenset(
    {
        FlowRelationshipKind.CALLS,
        FlowRelationshipKind.CONSTRUCTS,
        FlowRelationshipKind.AWAITS,
        FlowRelationshipKind.REGISTERS_HANDLER,
        FlowRelationshipKind.EMITS_MESSAGE,
        FlowRelationshipKind.HANDLES_MESSAGE,
        FlowRelationshipKind.SENDS_HTTP_REQUEST,
        FlowRelationshipKind.HANDLES_HTTP_REQUEST,
        FlowRelationshipKind.PUBLISHES_EVENT,
        FlowRelationshipKind.CONSUMES_EVENT,
    }
)


class FlowResolution(StrEnum):
    COMPILER_RESOLVED = "compiler_resolved"
    STATICALLY_RESOLVED = "statically_resolved"
    FRAMEWORK_INFERRED = "framework_inferred"
    UNRESOLVED = "unresolved"
    RUNTIME_OBSERVED = "runtime_observed"


class FlowViewType(StrEnum):
    FEATURE_FLOW = "feature_flow"


class FlowDirection(StrEnum):
    FORWARD = "forward"


class FlowFrontierDirection(StrEnum):
    INCOMING = "incoming"
    OUTGOING = "outgoing"


FlowEntityId = Annotated[str, Field(pattern=r"^ent_[A-Za-z0-9_-]+$")]
FlowNodeId = Annotated[str, Field(pattern=r"^node_[A-Za-z0-9_-]+$")]


class FlowSourceSpan(StrictCamelModel):
    path: str = Field(min_length=1, max_length=500)
    start_line: int = Field(ge=1)
    start_column: int = Field(ge=0)
    end_line: int = Field(ge=1)
    end_column: int = Field(ge=0)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    repository_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        path = PurePosixPath(self.path)
        if path.is_absolute() or ".." in path.parts or "\\" in self.path:
            raise ValueError("Flow source paths must be workspace-relative POSIX paths.")
        start = (self.start_line, self.start_column)
        end = (self.end_line, self.end_column)
        if end < start:
            raise ValueError("Flow source span end must not precede its start.")
        return self


class FlowProvenance(StrictCamelModel):
    extractor: str = Field(min_length=1, max_length=120)
    extractor_version: str = Field(min_length=1, max_length=80)
    rule_id: str = Field(min_length=1, max_length=160)


class RepositoryInvestigation(StrictCamelModel):
    schema_version: Literal["1", "2"] = "1"
    investigation_id: str = Field(pattern=r"^inv_[A-Za-z0-9_-]+$")
    repository_revision: int = Field(ge=1)
    question: str = Field(min_length=1, max_length=2_000)
    provider: AgentProvider
    selected_files: list[str] = Field(default_factory=list, max_length=250)
    read_spans: list[FlowSourceSpan] = Field(default_factory=list, max_length=500)
    evidence: list[FlowSourceSpan] = Field(default_factory=list, max_length=100)
    search_queries: list[str] = Field(default_factory=list, max_length=100)
    candidate_identifiers: list[str] = Field(default_factory=list, max_length=500)
    initial_anchor_entity_ids: list[FlowEntityId] = Field(default_factory=list, max_length=8)
    coverage_anchor_entity_ids: list[FlowEntityId] = Field(default_factory=list, max_length=8)
    created_at: datetime

    @model_validator(mode="after")
    def validate_revision(self) -> Self:
        if self.schema_version == "1" and (
            len(self.initial_anchor_entity_ids) > 3 or len(self.coverage_anchor_entity_ids) > 2
        ):
            raise ValueError("V1 investigations allow at most 3 initial and 2 coverage anchors.")
        spans = [*self.read_spans, *self.evidence]
        if any(span.repository_revision != self.repository_revision for span in spans):
            raise ValueError("Investigation spans must match its repository revision.")
        if len(set(self.selected_files)) != len(self.selected_files):
            raise ValueError("Investigation selected files must be unique.")
        if len(set(self.initial_anchor_entity_ids)) != len(self.initial_anchor_entity_ids):
            raise ValueError("Initial Flow Map anchor entity IDs must be unique.")
        if len(set(self.coverage_anchor_entity_ids)) != len(self.coverage_anchor_entity_ids):
            raise ValueError("Coverage-pass Flow Map anchor entity IDs must be unique.")
        if set(self.initial_anchor_entity_ids) & set(self.coverage_anchor_entity_ids):
            raise ValueError("Coverage-pass Flow Map anchors must be additional entities.")
        return self


class FlowRootSelection(StrictCamelModel):
    schema_version: Literal["1", "2"] = "1"
    repository_revision: int = Field(ge=1)
    root_entity_ids: list[FlowEntityId] = Field(min_length=1, max_length=8)
    view_type: FlowViewType = FlowViewType.FEATURE_FLOW
    direction: FlowDirection = FlowDirection.FORWARD
    rationale: str = Field(min_length=1, max_length=1_000)
    unresolved_concepts: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_unique_roots(self) -> Self:
        if self.schema_version == "1" and len(self.root_entity_ids) > 3:
            raise ValueError("V1 root selections allow at most 3 anchors.")
        if len(set(self.root_entity_ids)) != len(self.root_entity_ids):
            raise ValueError("Flow root entity IDs must be unique.")
        return self


class FlowNode(StrictCamelModel):
    id: FlowNodeId
    entity_id: FlowEntityId
    kind: FlowEntityKind
    label: str = Field(min_length=1, max_length=240)
    qualified_name: str | None = Field(default=None, max_length=1_000)
    parent_node_id: FlowNodeId | None = None
    source_spans: list[FlowSourceSpan] = Field(default_factory=list, max_length=100)
    expandable: bool = False
    hidden_neighbor_count: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FlowEdge(StrictCamelModel):
    id: str = Field(pattern=r"^edge_[A-Za-z0-9_-]+$")
    source_node_id: FlowNodeId
    target_node_id: FlowNodeId
    kind: FlowRelationshipKind
    resolution: FlowResolution
    provenance: FlowProvenance
    evidence: list[FlowSourceSpan] = Field(min_length=1, max_length=100)
    label: str | None = Field(default=None, max_length=240)
    conditional: bool = False
    asynchronous: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class FlowFrontier(StrictCamelModel):
    node_id: FlowNodeId
    direction: FlowFrontierDirection
    hidden_neighbor_count: int = Field(ge=1)
    relationship_kinds: list[FlowRelationshipKind] = Field(min_length=1)


class FlowWarning(StrictCamelModel):
    code: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=1_000)
    entity_ids: list[FlowEntityId] = Field(default_factory=list, max_length=100)


class SentiaFlowGraph(StrictCamelModel):
    schema_version: Literal["1", "2"] = "1"
    map_id: str = Field(pattern=r"^map_[A-Za-z0-9_-]+$")
    repository_revision: int = Field(ge=1)
    root_entity_ids: list[FlowEntityId] = Field(min_length=1, max_length=8)
    view_type: FlowViewType = FlowViewType.FEATURE_FLOW
    nodes: list[FlowNode] = Field(min_length=1, max_length=500)
    edges: list[FlowEdge] = Field(default_factory=list, max_length=2_000)
    frontiers: list[FlowFrontier] = Field(default_factory=list, max_length=500)
    warnings: list[FlowWarning] = Field(default_factory=list, max_length=100)
    generated_at: datetime

    @model_validator(mode="after")
    def validate_graph_references(self) -> Self:
        if self.schema_version == "1" and len(self.root_entity_ids) > 3:
            raise ValueError("V1 Flow Graphs allow at most 3 anchors.")
        node_ids = [node.id for node in self.nodes]
        entity_ids = [node.entity_id for node in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("Flow node IDs must be unique.")
        if len(set(entity_ids)) != len(entity_ids):
            raise ValueError("Flow entity IDs may appear only once in a Flow Graph.")
        edge_ids = [edge.id for edge in self.edges]
        if len(set(edge_ids)) != len(edge_ids):
            raise ValueError("Flow edge IDs must be unique.")
        if len(set(self.root_entity_ids)) != len(self.root_entity_ids):
            raise ValueError("Flow root entity IDs must be unique.")

        known_nodes = set(node_ids)
        known_entities = set(entity_ids)
        if any(root not in known_entities for root in self.root_entity_ids):
            raise ValueError("Every Flow Graph root must be represented by a node.")
        if any(
            node.parent_node_id is not None and node.parent_node_id not in known_nodes
            for node in self.nodes
        ):
            raise ValueError("Flow node parents must resolve within the graph.")
        if any(node.parent_node_id == node.id for node in self.nodes):
            raise ValueError("A Flow node may not be its own parent.")
        if any(
            edge.source_node_id not in known_nodes or edge.target_node_id not in known_nodes
            for edge in self.edges
        ):
            raise ValueError("Flow edge endpoints must resolve within the graph.")
        if any(frontier.node_id not in known_nodes for frontier in self.frontiers):
            raise ValueError("Flow frontiers must reference nodes in the graph.")
        if len(self.root_entity_ids) > 1:
            node_id_by_entity = {node.entity_id: node.id for node in self.nodes}
            adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
            for edge in self.edges:
                if edge.kind not in TRAVERSABLE_FLOW_RELATIONSHIP_KINDS:
                    continue
                adjacency[edge.source_node_id].add(edge.target_node_id)
                adjacency[edge.target_node_id].add(edge.source_node_id)
            first_root_node_id = node_id_by_entity[self.root_entity_ids[0]]
            reachable = {first_root_node_id}
            pending = [first_root_node_id]
            while pending:
                current = pending.pop()
                for neighbor in adjacency[current] - reachable:
                    reachable.add(neighbor)
                    pending.append(neighbor)
            disconnected_roots = [
                root_id
                for root_id in self.root_entity_ids
                if node_id_by_entity[root_id] not in reachable
            ]
            if disconnected_roots and not any(
                warning.code == "disconnected_feature_anchors" for warning in self.warnings
            ):
                raise ValueError(
                    "Disconnected Flow Graph anchors require an explicit explanation warning."
                )
        spans = [span for node in self.nodes for span in node.source_spans]
        spans.extend(span for edge in self.edges for span in edge.evidence)
        if any(span.repository_revision != self.repository_revision for span in spans):
            raise ValueError("Flow Graph evidence must match its repository revision.")
        return self


class StackTechnology(StrictCamelModel):
    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    status: Literal["declared", "observed"]
    paths: list[str]
    adapter_support: Literal["partial", "unsupported"]


class StackProfile(StrictCamelModel):
    repository_revision: int = Field(ge=1)
    technologies: list[StackTechnology]
    languages: list[str]
    limitations: list[str]


class FeatureSpecification(StrictCamelModel):
    actor: str = Field(min_length=1, max_length=500)
    input: str = Field(min_length=1, max_length=500)
    behavior: str = Field(min_length=1, max_length=1000)
    outcome: str = Field(min_length=1, max_length=500)
    alternatives: list[str] = Field(max_length=8)
    open_questions: list[str] = Field(max_length=8)


class TraceCandidate(StrictCamelModel):
    entity_id: FlowEntityId
    name: str
    decision: Literal["include", "supporting", "exclude", "uncertain"]
    responsibility: str
    reason: str
    question: str
    source_reviewed: bool
    evidence: list[FlowSourceSpan]


class TraceStage(StrictCamelModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    role: Literal["entry", "input", "orchestration", "transformation", "persistence", "outcome"]
    entity_ids: list[FlowEntityId] = Field(min_length=1)
    evidence: list[FlowSourceSpan]
    responsibility: str = ""
    inclusion_reason: str = ""
    review_status: Literal["unverified", "source_reviewed"] = "unverified"


class TraceTransition(StrictCamelModel):
    id: str = Field(min_length=1)
    source_stage_id: str = Field(min_length=1)
    target_stage_id: str = Field(min_length=1)
    relationship_ids: list[str] = Field(min_length=1)
    evidence: list[FlowSourceSpan] = Field(min_length=1)
    kind: Literal["execution", "registration", "structural"]
    via_entity_ids: list[FlowEntityId] = Field(default_factory=list)
    via_names: list[str] = Field(default_factory=list)
    asynchronous: bool = False
    conditional: bool = False


class TraceGap(StrictCamelModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    stage_ids: list[str]


class TraceUsage(StrictCamelModel):
    stage: Literal["planning", "investigation", "review"]
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class FeatureTrace(StrictCamelModel):
    schema_version: Literal["2"] = "2"
    trace_id: str = Field(pattern=r"^trace_[A-Za-z0-9_-]+$")
    repository_revision: int = Field(ge=1)
    scope: Literal["feature", "subsystem", "overview"]
    requested_outcome: str = Field(min_length=1)
    stack_profile: StackProfile
    stages: list[TraceStage]
    transitions: list[TraceTransition]
    gaps: list[TraceGap]
    status: Literal["partial", "supported"]
    stop_reason: Literal["coverage_satisfied", "budget_exhausted", "no_progress", "review_failed"]
    usage: list[TraceUsage]
    limitations: list[str]
    feature_specification: FeatureSpecification | None = None
    candidates: list[TraceCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_trace_references(self) -> Self:
        stage_ids = {stage.id for stage in self.stages}
        if len(stage_ids) != len(self.stages):
            raise ValueError("Feature trace stage IDs must be unique.")
        if len({transition.id for transition in self.transitions}) != len(self.transitions):
            raise ValueError("Feature trace transition IDs must be unique.")
        if any(
            transition.source_stage_id not in stage_ids
            or transition.target_stage_id not in stage_ids
            for transition in self.transitions
        ):
            raise ValueError("Feature trace transitions must reference known stages.")
        if any(stage_id not in stage_ids for gap in self.gaps for stage_id in gap.stage_ids):
            raise ValueError("Feature trace gaps must reference known stages.")
        spans = [span for stage in self.stages for span in stage.evidence]
        spans.extend(span for transition in self.transitions for span in transition.evidence)
        spans.extend(span for candidate in self.candidates for span in candidate.evidence)
        if len({candidate.entity_id for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("Feature candidate IDs must be unique.")
        if any(
            candidate.source_reviewed and not candidate.evidence for candidate in self.candidates
        ):
            raise ValueError("Reviewed candidates require evidence.")
        accepted = {
            candidate.entity_id
            for candidate in self.candidates
            if candidate.source_reviewed and candidate.decision == "include"
        }
        if self.feature_specification is not None and any(
            stage.review_status != "source_reviewed"
            or not stage.inclusion_reason
            or not stage.evidence
            or any(eid not in accepted for eid in stage.entity_ids)
            for stage in self.stages
        ):
            raise ValueError("Feature stages require a reviewed inclusion decision.")
        if self.stack_profile.repository_revision != self.repository_revision or any(
            span.repository_revision != self.repository_revision for span in spans
        ):
            raise ValueError("Feature trace evidence and stack must match its repository revision.")
        if self.status == "supported" and (
            self.gaps
            or self.stop_reason != "coverage_satisfied"
            or not self.stages
            or any(not stage.evidence for stage in self.stages)
        ):
            raise ValueError("Supported feature traces require satisfied coverage and no gaps.")
        return self


class FlowMapResponse(StrictCamelModel):
    investigation: RepositoryInvestigation
    root_selection: FlowRootSelection
    graph: SentiaFlowGraph
    model: str = Field(min_length=1, max_length=200)
    usage: TokenUsage
    investigation_usage: FlowInvestigationUsage
    feature_trace: FeatureTrace | None = None

    @model_validator(mode="after")
    def validate_trace_revision(self) -> Self:
        if self.feature_trace is not None and any(
            revision != self.feature_trace.repository_revision
            for revision in (
                self.investigation.repository_revision,
                self.root_selection.repository_revision,
                self.graph.repository_revision,
            )
        ):
            raise ValueError("Feature trace must match the response repository revision.")
        return self


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
