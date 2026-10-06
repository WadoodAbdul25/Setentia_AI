from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import structlog
from anyio import CancelScope
from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from starlette import status
from starlette.responses import StreamingResponse

from sentia_sidecar.agent_runtime import (
    AgentRouter,
    CodexLogin,
    CodexLoginManager,
    create_agent_router,
)
from sentia_sidecar.anchor_reconciliation import reconcile_anchors
from sentia_sidecar.answer_stream import AnswerStreamEvent
from sentia_sidecar.anthropic_service import AnthropicRepositoryService, AnthropicServiceError
from sentia_sidecar.codex_service import CodexRepositoryService
from sentia_sidecar.database import Database
from sentia_sidecar.eot_model import EOTModel
from sentia_sidecar.events import EventStore
from sentia_sidecar.flow_graph import FlowTraversalPolicy, build_feature_flow
from sentia_sidecar.logging_config import configure_logging
from sentia_sidecar.models import Conversation
from sentia_sidecar.protocol import (
    AgentConnectionStatus,
    AgentLoginStart,
    AgentProvider,
    AnthropicCredentialSet,
    AnthropicCredentialStatus,
    ConversationCreate,
    ConversationResponse,
    DeepgramCredentialSet,
    DeepgramCredentialStatus,
    FlowMapRequest,
    FlowMapResponse,
    HealthResponse,
    OpenAIVoiceCredentialSet,
    OpenAIVoiceCredentialStatus,
    ProjectSnapshotStatus,
    RepositoryAnswer,
    RepositoryQuestion,
    SpeechRenderRequest,
    SpeechRenderResponse,
    VersionResponse,
    WorkflowState,
    WorkspaceAttach,
)
from sentia_sidecar.repository import RepositoryError, build_repository_manifest
from sentia_sidecar.repository_intelligence import (
    RepositoryIntelligenceError,
    RepositoryIntelligenceRouter,
)
from sentia_sidecar.settings import Settings, get_settings
from sentia_sidecar.snapshot import (
    ProjectSnapshotService,
    SnapshotError,
    load_snapshot_project_paths,
)
from sentia_sidecar.structural_index import StructuralIndex
from sentia_sidecar.turn_detection import LocalEOTScorer
from sentia_sidecar.voice import (
    FLUX_EVENTS,
    DeepgramFluxGateway,
    FluxConnection,
    FluxGateway,
    OpenAIRealtimeTranscriptionGateway,
    OpenAIVoiceGateway,
    OpenAIVoiceOptions,
    RepositoryTranscriptCorrector,
    VoiceError,
    VoiceOptions,
    build_vocabulary,
    select_keyterms,
)

logger = structlog.get_logger(__name__)


def _bearer_token(value: str | None) -> str | None:
    if value is None:
        return None
    scheme, separator, token = value.partition(" ")
    if separator and scheme.lower() == "bearer":
        return token
    return None


def create_app(
    settings: Settings | None = None,
    anthropic_service: AnthropicRepositoryService | None = None,
    repository_router: RepositoryIntelligenceRouter | None = None,
    agent_router: AgentRouter | None = None,
    codex_login_manager: CodexLogin | None = None,
    flux_gateway: FluxGateway | None = None,
    openai_voice_gateway: OpenAIVoiceGateway | None = None,
    livekit_turn_factory: Callable[..., Any] | None = None,
) -> FastAPI:
    resolved = settings or get_settings()
    expected_token = resolved.token.get_secret_value()
    configure_logging(resolved.log_level)
    database = Database(resolved.database_url)
    events = EventStore(database.sessions)
    anthropic = anthropic_service or AnthropicRepositoryService(resolved.anthropic_model)
    intelligence = repository_router or RepositoryIntelligenceRouter(
        [
            anthropic,
            CodexRepositoryService(resolved.codex_model),
        ]
    )
    agents = agent_router or create_agent_router()
    codex_login = codex_login_manager or CodexLoginManager()
    deepgram_voice_gateway = flux_gateway or DeepgramFluxGateway()
    openai_transcription_gateway = openai_voice_gateway or OpenAIRealtimeTranscriptionGateway(
        LocalEOTScorer(
            lambda: EOTModel(
                model_id=resolved.openai_eot_model_id,
                max_input_tokens=resolved.openai_eot_max_input_tokens,
            )
        )
    )
    deepgram_api_key: str | None = None
    openai_voice_api_key: str | None = (
        resolved.openai_api_key.get_secret_value() if resolved.openai_api_key else None
    )

    async def snapshot_updated(status: ProjectSnapshotStatus, triggers: list[str]) -> None:
        await events.publish(
            "repository.snapshot_updated",
            {
                "repositoryRevision": status.repository_revision,
                "workspaceName": status.workspace_name,
                "snapshotPath": status.snapshot_path,
                "updatedAt": status.updated_at.isoformat(),
                "fileCount": status.file_count,
                "directoryCount": status.directory_count,
                "triggers": triggers,
            },
        )

    snapshots = ProjectSnapshotService(on_update=snapshot_updated)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await database.initialize()
        app.state.database = database
        app.state.events = events
        app.state.anthropic = anthropic
        app.state.repository_intelligence = intelligence
        app.state.agents = agents
        app.state.snapshots = snapshots
        logger.info("sidecar_started", host=resolved.host, port=resolved.port)
        try:
            yield
        finally:
            if isinstance(openai_transcription_gateway, OpenAIRealtimeTranscriptionGateway):
                openai_transcription_gateway.close()
            await snapshots.stop()
            await intelligence.close()
            await codex_login.close()
            await database.close()
            logger.info("sidecar_stopped")

    app = FastAPI(
        title="Sentia Local Sidecar",
        description="Private loopback API supervised by the Sentia editor extension.",
        version="0.1.0",
        lifespan=lifespan,
    )

    async def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        supplied = _bearer_token(authorization)
        if supplied is None or not hmac.compare_digest(supplied, expected_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    auth_dependencies = [Depends(authorize)]

    @app.get("/health", response_model=HealthResponse, dependencies=auth_dependencies)
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/version", response_model=VersionResponse, dependencies=auth_dependencies)
    async def version() -> VersionResponse:
        return VersionResponse()

    @app.get("/api/v1/state", response_model=HealthResponse, dependencies=auth_dependencies)
    async def state() -> HealthResponse:
        return HealthResponse(workflow_state=WorkflowState.READY)

    @app.get(
        "/api/v1/auth/anthropic",
        response_model=AnthropicCredentialStatus,
        dependencies=auth_dependencies,
    )
    async def anthropic_status() -> AnthropicCredentialStatus:
        return AnthropicCredentialStatus(connected=anthropic.connected, model=anthropic.model)

    @app.get(
        "/api/v1/agents/{provider}/status",
        response_model=AgentConnectionStatus,
        dependencies=auth_dependencies,
    )
    async def agent_status(provider: AgentProvider) -> AgentConnectionStatus:
        availability = await agents.adapter(provider).availability()
        connected = availability.authentication in {"api_key", "codex_login"}
        if provider == AgentProvider.CLAUDE and anthropic.connected:
            connected = True
        return AgentConnectionStatus(
            provider=provider,
            connected=connected,
            authentication=availability.authentication,
            message=availability.detail,
        )

    @app.post(
        "/api/v1/agents/codex/login",
        response_model=AgentLoginStart,
        dependencies=auth_dependencies,
    )
    async def start_codex_login() -> AgentLoginStart:
        try:
            auth_url = await codex_login.start()
        except Exception as error:
            logger.exception("codex_login_start_failed", error_type=type(error).__name__)
            raise HTTPException(
                status_code=503,
                detail="Sentia could not start Codex login. Open Diagnostics for details.",
            ) from error
        return AgentLoginStart(provider=AgentProvider.CODEX, auth_url=auth_url)

    @app.put(
        "/api/v1/auth/anthropic",
        response_model=AnthropicCredentialStatus,
        dependencies=auth_dependencies,
    )
    async def connect_anthropic(body: AnthropicCredentialSet) -> AnthropicCredentialStatus:
        api_key = body.api_key.get_secret_value()
        try:
            await anthropic.connect(api_key, validate=body.verify)
        except AnthropicServiceError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error
        agents.set_anthropic_api_key(api_key)
        return AnthropicCredentialStatus(
            connected=True,
            model=anthropic.model,
            message="Anthropic is connected.",
        )

    @app.delete(
        "/api/v1/auth/anthropic",
        response_model=AnthropicCredentialStatus,
        dependencies=auth_dependencies,
    )
    async def disconnect_anthropic() -> AnthropicCredentialStatus:
        await anthropic.disconnect()
        agents.set_anthropic_api_key(None)
        return AnthropicCredentialStatus(
            connected=False,
            model=anthropic.model,
            message="Anthropic is disconnected.",
        )

    @app.get(
        "/api/v1/auth/deepgram",
        response_model=DeepgramCredentialStatus,
        dependencies=auth_dependencies,
    )
    async def deepgram_status() -> DeepgramCredentialStatus:
        return DeepgramCredentialStatus(connected=deepgram_api_key is not None)

    @app.put(
        "/api/v1/auth/deepgram",
        response_model=DeepgramCredentialStatus,
        dependencies=auth_dependencies,
    )
    async def connect_deepgram(body: DeepgramCredentialSet) -> DeepgramCredentialStatus:
        nonlocal deepgram_api_key
        deepgram_api_key = body.api_key.get_secret_value()
        return DeepgramCredentialStatus(
            connected=True,
            message="Deepgram Flux is connected.",
        )

    @app.delete(
        "/api/v1/auth/deepgram",
        response_model=DeepgramCredentialStatus,
        dependencies=auth_dependencies,
    )
    async def disconnect_deepgram() -> DeepgramCredentialStatus:
        nonlocal deepgram_api_key
        deepgram_api_key = None
        return DeepgramCredentialStatus(
            connected=False,
            message="Deepgram is disconnected.",
        )

    @app.get(
        "/api/v1/auth/openai-voice",
        response_model=OpenAIVoiceCredentialStatus,
        dependencies=auth_dependencies,
    )
    async def openai_voice_status() -> OpenAIVoiceCredentialStatus:
        return OpenAIVoiceCredentialStatus(connected=openai_voice_api_key is not None)

    @app.put(
        "/api/v1/auth/openai-voice",
        response_model=OpenAIVoiceCredentialStatus,
        dependencies=auth_dependencies,
    )
    async def connect_openai_voice(
        body: OpenAIVoiceCredentialSet,
    ) -> OpenAIVoiceCredentialStatus:
        nonlocal openai_voice_api_key
        openai_voice_api_key = body.api_key.get_secret_value()
        return OpenAIVoiceCredentialStatus(
            connected=True,
            message="OpenAI Voice is connected.",
        )

    @app.delete(
        "/api/v1/auth/openai-voice",
        response_model=OpenAIVoiceCredentialStatus,
        dependencies=auth_dependencies,
    )
    async def disconnect_openai_voice() -> OpenAIVoiceCredentialStatus:
        nonlocal openai_voice_api_key
        openai_voice_api_key = None
        return OpenAIVoiceCredentialStatus(
            connected=False,
            message="OpenAI Voice is disconnected.",
        )

    @app.put(
        "/api/v1/repository/snapshot",
        response_model=ProjectSnapshotStatus,
        dependencies=auth_dependencies,
    )
    async def attach_repository_snapshot(body: WorkspaceAttach) -> ProjectSnapshotStatus:
        try:
            snapshot = await snapshots.attach(body.workspace_path)
        except RepositoryError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except SnapshotError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        await events.publish(
            "repository.snapshot_attached",
            {
                "workspaceName": snapshot.workspace_name,
                "snapshotPath": snapshot.snapshot_path,
                "createdAt": snapshot.created_at.isoformat(),
                "updatedAt": snapshot.updated_at.isoformat(),
                "fileCount": snapshot.file_count,
                "directoryCount": snapshot.directory_count,
            },
        )
        return snapshot

    @app.post(
        "/api/v1/repository/questions",
        response_model=RepositoryAnswer,
        dependencies=auth_dependencies,
    )
    async def ask_repository(body: RepositoryQuestion) -> RepositoryAnswer:
        await events.publish("workflow.state", {"state": WorkflowState.INDEXING})
        try:
            repository = await snapshots.manifest(body.workspace_path)
            if repository is None:
                project_paths = await asyncio.to_thread(
                    load_snapshot_project_paths,
                    body.workspace_path,
                )
                repository = await asyncio.to_thread(
                    build_repository_manifest,
                    body.workspace_path,
                    project_paths,
                )
            await events.publish(
                "repository.indexed",
                {
                    "filesScanned": repository.files_scanned,
                    "cacheSource": repository.cache_source,
                },
            )
            await events.publish("workflow.state", {"state": WorkflowState.DISCUSSING})
            answer = await intelligence.answer(body.provider, body.question, repository)
        except RepositoryError as error:
            await events.publish("workflow.state", {"state": WorkflowState.FAILED})
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RepositoryIntelligenceError as error:
            await events.publish("workflow.state", {"state": WorkflowState.FAILED})
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error
        await events.publish(
            "mode.recommended",
            {"mode": answer.recommended_mode, "reason": answer.mode_reason},
        )
        await events.publish(
            "repository.answered",
            {
                "provider": body.provider,
                "evidenceCount": len(answer.evidence),
            },
        )
        await events.publish("workflow.state", {"state": WorkflowState.READY})
        return answer

    async def repository_answer_events(
        body: RepositoryQuestion,
    ) -> AsyncIterator[AnswerStreamEvent]:
        await events.publish("workflow.state", {"state": WorkflowState.INDEXING})
        try:
            repository = await snapshots.manifest(body.workspace_path)
            if repository is None:
                paths = await asyncio.to_thread(load_snapshot_project_paths, body.workspace_path)
                repository = await asyncio.to_thread(
                    build_repository_manifest, body.workspace_path, paths
                )
            await events.publish(
                "repository.indexed",
                {
                    "filesScanned": repository.files_scanned,
                    "cacheSource": repository.cache_source,
                },
            )
            await events.publish("workflow.state", {"state": WorkflowState.DISCUSSING})
            async for event in intelligence.stream_answer(body.provider, body.question, repository):
                if event.type == "complete" and event.answer is not None:
                    await events.publish(
                        "mode.recommended",
                        {
                            "mode": event.answer.recommended_mode,
                            "reason": event.answer.mode_reason,
                        },
                    )
                    await events.publish(
                        "repository.answered",
                        {
                            "provider": body.provider,
                            "evidenceCount": len(event.answer.evidence),
                        },
                    )
                    await events.publish("workflow.state", {"state": WorkflowState.READY})
                yield event
        except Exception:
            await events.publish("workflow.state", {"state": WorkflowState.FAILED})
            raise

    @app.post("/api/v1/repository/questions/stream", dependencies=auth_dependencies)
    async def stream_repository_question(body: RepositoryQuestion) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            try:
                async for event in repository_answer_events(body):
                    yield f"data: {event.model_dump_json(by_alias=True)}\n\n"
            except Exception:
                logger.warning("repository_answer_stream_failed")
                message = {"type": "error", "message": "Answer streaming failed. Please retry."}
                yield f"data: {json.dumps(message)}\n\n"

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @app.post(
        "/api/v1/repository/flow-maps",
        response_model=FlowMapResponse,
        dependencies=auth_dependencies,
    )
    async def create_flow_map(body: FlowMapRequest) -> FlowMapResponse:
        flow_map_started = time.perf_counter()
        feature_trace = None
        await events.publish("workflow.state", {"state": WorkflowState.INDEXING})
        try:
            repository = await snapshots.manifest(body.workspace_path)
            if repository is None:
                project_paths = await asyncio.to_thread(
                    load_snapshot_project_paths,
                    body.workspace_path,
                )
                repository = await asyncio.to_thread(
                    build_repository_manifest,
                    body.workspace_path,
                    project_paths,
                )
            index = StructuralIndex(
                repository.root,
                repository.repository_revision,
                repository.entities,
                repository.relationships,
            )
            await events.publish(
                "repository.indexed",
                {
                    "filesScanned": repository.files_scanned,
                    "entityCount": len(index.entities),
                    "relationshipCount": len(index.relationships),
                    "cacheSource": repository.cache_source,
                },
            )
            await events.publish("workflow.state", {"state": WorkflowState.DISCUSSING})
            if body.engine_version == "2":
                trace_result = await intelligence.trace_feature(
                    body.provider, body.question, repository, index
                )
                roots = trace_result.roots
                feature_trace = trace_result.trace
            else:
                roots = await intelligence.select_flow_roots(
                    body.provider, body.question, repository, index
                )
            reconciliation = reconcile_anchors(index, roots.selection)
            graph = build_feature_flow(
                index,
                roots.selection,
                question=body.question,
                candidate_identifiers=roots.investigation.candidate_identifiers,
                policy=FlowTraversalPolicy(
                    max_depth=body.max_depth,
                    max_nodes=body.max_nodes,
                    max_edges=body.max_edges,
                    max_branches_per_node=body.max_branches_per_node,
                ),
                reconciliation=reconciliation,
            )
        except RepositoryError as error:
            await events.publish("workflow.state", {"state": WorkflowState.FAILED})
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RepositoryIntelligenceError as error:
            await events.publish("workflow.state", {"state": WorkflowState.FAILED})
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error
        except ValueError as error:
            await events.publish("workflow.state", {"state": WorkflowState.FAILED})
            raise HTTPException(status_code=422, detail=str(error)) from error

        result = FlowMapResponse(
            investigation=roots.investigation,
            root_selection=roots.selection,
            graph=graph,
            model=roots.model,
            usage=roots.usage,
            investigation_usage=roots.investigation_usage,
            feature_trace=feature_trace,
        )
        logger.info(
            "flow_map_generated",
            provider=body.provider,
            repository_revision=graph.repository_revision,
            selected_files=roots.investigation.selected_files,
            initial_anchor_entity_ids=roots.investigation.initial_anchor_entity_ids,
            coverage_anchor_entity_ids=roots.investigation.coverage_anchor_entity_ids,
            final_anchor_entity_ids=roots.selection.root_entity_ids,
            anchor_reconciliation=reconciliation.diagnostics_metadata(),
            visible_graph_entity_ids=[node.entity_id for node in graph.nodes],
            visible_graph_edge_ids=[edge.id for edge in graph.edges],
            visible_graph_relationship_kinds=[edge.kind for edge in graph.edges],
            input_tokens=roots.usage.input_tokens,
            output_tokens=roots.usage.output_tokens,
            generation_elapsed_ms=round((time.perf_counter() - flow_map_started) * 1_000, 2),
        )
        await events.publish(
            "repository.flow_map_generated",
            {
                "provider": body.provider,
                "mapId": graph.map_id,
                "repositoryRevision": graph.repository_revision,
                "rootEntityIds": graph.root_entity_ids,
                "nodeCount": len(graph.nodes),
                "edgeCount": len(graph.edges),
            },
        )
        await events.publish("workflow.state", {"state": WorkflowState.READY})
        return result

    @app.post(
        "/api/v1/voice/render",
        response_model=SpeechRenderResponse,
        dependencies=auth_dependencies,
    )
    async def render_speech(body: SpeechRenderRequest) -> SpeechRenderResponse:
        try:
            rendered = await intelligence.render_speech(
                body.provider,
                body.answer,
                Path(body.workspace_path),
            )
        except RepositoryIntelligenceError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error
        return SpeechRenderResponse(spoken_answer=rendered.spoken_answer)

    @app.post(
        "/api/v1/voice/render/stream",
        dependencies=auth_dependencies,
    )
    async def stream_speech(body: SpeechRenderRequest) -> StreamingResponse:
        async def events() -> AsyncIterator[bytes]:
            try:
                async for delta in intelligence.stream_speech(
                    body.provider,
                    body.answer,
                    Path(body.workspace_path),
                ):
                    if delta:
                        yield (json.dumps({"type": "delta", "text": delta}) + "\n").encode()
                yield b'{"type":"done"}\n'
            except RepositoryIntelligenceError as error:
                logger.warning(
                    "speech_render_stream_failed",
                    provider=body.provider,
                    status_code=error.status_code,
                    error_type=type(error).__name__,
                )
                yield (
                    json.dumps(
                        {
                            "type": "error",
                            "message": "Sentia could not prepare the spoken response.",
                        }
                    )
                    + "\n"
                ).encode()
            except Exception as error:
                logger.exception(
                    "speech_render_stream_failed",
                    provider=body.provider,
                    error_type=type(error).__name__,
                )
                yield (
                    b'{"type":"error","message":"Sentia could not prepare the spoken response."}\n'
                )

        return StreamingResponse(
            events(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post(
        "/api/v1/conversations",
        response_model=ConversationResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=auth_dependencies,
    )
    async def create_conversation(body: ConversationCreate) -> ConversationResponse:
        conversation = Conversation(
            id=f"conv_{uuid4().hex}",
            workspace_id=body.workspace_id,
            title=body.title,
            created_at=datetime.now(UTC),
        )
        async with database.sessions.begin() as session:
            session.add(conversation)
        await events.publish(
            "conversation.created",
            {"title": conversation.title},
            conversation_id=conversation.id,
        )
        return ConversationResponse.model_validate(conversation, from_attributes=True)

    @app.get(
        "/api/v1/conversations",
        response_model=list[ConversationResponse],
        dependencies=auth_dependencies,
    )
    async def list_conversations() -> list[ConversationResponse]:
        async with database.sessions() as session:
            rows = await session.scalars(
                select(Conversation).order_by(Conversation.created_at.desc())
            )
            return [
                ConversationResponse.model_validate(item, from_attributes=True) for item in rows
            ]

    @app.websocket("/ws")
    async def websocket_events(
        websocket: WebSocket,
        last_sequence: Annotated[int, Query(alias="lastSequence", ge=0)] = 0,
    ) -> None:
        supplied = _bearer_token(websocket.headers.get("authorization"))
        if supplied is None or not hmac.compare_digest(supplied, expected_token):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized")
            return

        await websocket.accept()
        for event in await events.replay_after(last_sequence):
            await websocket.send_json(event.model_dump(by_alias=True, mode="json"))

        connected = await events.publish("system.connected", {"state": "ready"})
        await websocket.send_json(connected.model_dump(by_alias=True, mode="json"))

        try:
            while True:
                try:
                    message: dict[str, Any] = await asyncio.wait_for(
                        websocket.receive_json(), timeout=15
                    )
                except TimeoutError:
                    await websocket.send_json(
                        {
                            "protocolVersion": "1",
                            "eventId": f"heartbeat_{uuid4().hex}",
                            "sequence": connected.sequence,
                            "conversationId": None,
                            "runId": None,
                            "type": "transport.heartbeat",
                            "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                            "payload": {},
                        }
                    )
                    continue

                if message.get("type") == "transport.ping":
                    await websocket.send_json({"type": "transport.pong"})
                elif message.get("type") == "transport.ack":
                    continue
                else:
                    await websocket.send_json(
                        {"type": "transport.error", "message": "Unsupported transport message"}
                    )
        except WebSocketDisconnect:
            logger.info("websocket_disconnected")

    @app.websocket("/api/v1/voice/deepgram/transcribe")
    @app.websocket("/api/v1/voice/openai/transcribe")
    async def transcribe_voice(
        websocket: WebSocket,
        session_id: Annotated[str, Query(alias="sessionId", min_length=1)],
        workspace_path: Annotated[str, Query(alias="workspacePath", min_length=1)],
        model: Annotated[str | None, Query()] = None,
        endpoint: Annotated[str | None, Query()] = None,
        encoding: Annotated[str | None, Query()] = None,
        sample_rate: Annotated[int | None, Query(alias="sampleRate", gt=0)] = None,
        active_file: Annotated[str | None, Query(alias="activeFile")] = None,
        language_hints: Annotated[list[str] | None, Query(alias="languageHint")] = None,
    ) -> None:
        selected_provider = (
            "deepgram" if websocket.url.path == "/api/v1/voice/deepgram/transcribe" else "openai"
        )
        selected_model = model or (
            "flux-general-en" if selected_provider == "deepgram" else "gpt-live-transcribe"
        )
        selected_endpoint = endpoint or (
            "wss://api.deepgram.com"
            if selected_provider == "deepgram"
            else "wss://api.openai.com/v1/realtime?model=gpt-realtime"
        )
        supplied = _bearer_token(websocket.headers.get("authorization"))
        if supplied is None or not hmac.compare_digest(supplied, expected_token):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized")
            return
        if selected_provider not in {"deepgram", "openai"}:
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Unsupported voice provider",
            )
            return
        selected_api_key = (
            deepgram_api_key if selected_provider == "deepgram" else openai_voice_api_key
        )
        if selected_api_key is None:
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason=f"Connect {selected_provider.title()} before starting voice input",
            )
            return

        await websocket.accept()
        await websocket.send_json(
            {
                "type": "voice.status",
                "sessionId": session_id,
                "state": "connecting",
                "message": "Connecting to Deepgram Flux…"
                if selected_provider == "deepgram"
                else "Connecting to OpenAI live transcription…",
            }
        )
        connection: FluxConnection | None = None
        try:
            repository = await snapshots.manifest(workspace_path)
            if repository is None:
                repository = await asyncio.to_thread(build_repository_manifest, workspace_path)
            vocabulary = build_vocabulary(repository, active_file)
            keyterms = select_keyterms(vocabulary)
            corrector = RepositoryTranscriptCorrector(vocabulary)
            logger.info(
                "voice_pipeline_selected",
                provider=selected_provider,
                model=selected_model,
                route=websocket.url.path,
                keyterm_count=len(keyterms),
                sends_transcription_prompt=False,
            )
            if selected_provider == "deepgram":
                options = VoiceOptions(
                    model=selected_model,
                    endpoint=selected_endpoint,
                    language_hints=tuple(language_hints or ()),
                    encoding=encoding,
                    sample_rate=sample_rate,
                )
                active_connection = await deepgram_voice_gateway.connect(
                    selected_api_key, options, keyterms
                )
            else:
                openai_options = OpenAIVoiceOptions(
                    model=selected_model,
                    endpoint=selected_endpoint,
                    sample_rate=sample_rate or 24_000,
                    semantic_eot_enabled=resolved.openai_eot_enabled,
                    semantic_silence_ms=resolved.openai_eot_min_silence_ms,
                    eot_threshold=resolved.openai_eot_threshold,
                    eot_inference_timeout_ms=resolved.openai_eot_inference_timeout_ms,
                )
                active_connection = await openai_transcription_gateway.connect(
                    selected_api_key, openai_options, keyterms
                )
            connection = active_connection
            await websocket.send_json(
                {
                    "type": "voice.status",
                    "sessionId": session_id,
                    "state": "listening",
                    "message": (
                        f"Flux is listening with {len(keyterms)} repository keyterms."
                        if selected_provider == "deepgram"
                        else f"OpenAI is listening with {len(keyterms)} repository terms."
                    ),
                }
            )

            async def forward_audio() -> str:
                while True:
                    incoming = await websocket.receive()
                    if incoming["type"] == "websocket.disconnect":
                        return "disconnect"
                    audio = incoming.get("bytes")
                    if isinstance(audio, bytes) and audio:
                        await active_connection.send_audio(audio)
                        continue
                    text = incoming.get("text")
                    if not isinstance(text, str):
                        continue
                    try:
                        control = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if control.get("type") == "voice.stop":
                        await websocket.send_json(
                            {
                                "type": "voice.status",
                                "sessionId": session_id,
                                "state": "stopping",
                                "message": "Finishing the current turn…",
                            }
                        )
                        await active_connection.close_stream()
                        return "stop"
                    if control.get("type") == "voice.cancel":
                        return "cancel"

            async def forward_results() -> bool:
                async for payload in active_connection.messages():
                    message_type = payload.get("type")
                    if message_type == "Error":
                        description = payload.get("description")
                        default_error = (
                            f"{selected_provider.title()} transcription returned an error."
                        )
                        await websocket.send_json(
                            {
                                "type": "voice.error",
                                "sessionId": session_id,
                                "error": description
                                if isinstance(description, str)
                                else default_error,
                            }
                        )
                        return False
                    if message_type != "TurnInfo" or payload.get("event") not in FLUX_EVENTS:
                        continue
                    transcript = payload.get("transcript", "")
                    if not isinstance(transcript, str):
                        transcript = ""
                    corrected = corrector.correct(transcript)
                    confidence = payload.get("end_of_turn_confidence")
                    if not isinstance(confidence, int | float):
                        confidence = None
                    languages = payload.get("languages", [])
                    if not isinstance(languages, list):
                        languages = []
                    event = str(payload["event"])
                    await websocket.send_json(
                        {
                            "type": "voice.transcript",
                            "payload": {
                                "sessionId": session_id,
                                "event": event,
                                "turnIndex": int(payload.get("turn_index", 0)),
                                "transcript": transcript,
                                "correctedTranscript": corrected.text,
                                "endOfTurnConfidence": confidence,
                                "corrections": [
                                    correction.as_dict() for correction in corrected.corrections
                                ],
                                "languages": [
                                    language for language in languages if isinstance(language, str)
                                ],
                                "isFinal": event == "EndOfTurn",
                            },
                        }
                    )
                return True

            audio_task = asyncio.create_task(forward_audio())
            result_task = asyncio.create_task(forward_results())
            done, _ = await asyncio.wait(
                {audio_task, result_task}, return_when=asyncio.FIRST_COMPLETED
            )
            result_ok = True
            if result_task in done:
                audio_task.cancel()
                result_ok = await result_task
                await active_connection.close()
            else:
                outcome = await audio_task
                if outcome == "stop":
                    result_ok = await result_task
                else:
                    result_task.cancel()
                    await active_connection.close()
            if not result_ok:
                return
            await websocket.send_json(
                {
                    "type": "voice.status",
                    "sessionId": session_id,
                    "state": "closed",
                    "message": None,
                }
            )
        except WebSocketDisconnect:
            logger.info("voice_websocket_disconnected", session_id=session_id)
        except (RepositoryError, VoiceError) as error:
            await websocket.send_json(
                {"type": "voice.error", "sessionId": session_id, "error": str(error)}
            )
        except Exception as error:
            detail = str(error) or type(error).__name__
            logger.exception(
                "voice_session_failed",
                error_type=type(error).__name__,
                error=detail,
            )
            await websocket.send_json(
                {
                    "type": "voice.error",
                    "sessionId": session_id,
                    "error": f"The voice session failed: {detail}",
                }
            )
        finally:
            if connection is not None:
                await connection.close()

    @app.websocket("/api/v1/voice/livekit/session")
    async def livekit_voice_session(
        websocket: WebSocket,
        session_id: Annotated[str, Query(alias="sessionId", min_length=1)],
        workspace_path: Annotated[str, Query(alias="workspacePath", min_length=1)],
        active_file: Annotated[str | None, Query(alias="activeFile")] = None,
        model: str = "gpt-live-transcribe",
        endpoint: str = "wss://api.openai.com/v1/realtime?model=gpt-realtime",
        tts_model: Annotated[str, Query(alias="ttsModel")] = "gpt-4o-mini-tts",
        tts_voice: Annotated[str, Query(alias="ttsVoice")] = "marin",
        tts_endpoint: Annotated[str, Query(alias="ttsEndpoint")] = "https://api.openai.com",
    ) -> None:
        supplied = _bearer_token(websocket.headers.get("authorization"))
        if supplied is None or not hmac.compare_digest(supplied, expected_token):
            await websocket.close(code=1008, reason="Unauthorized")
            return
        if not openai_voice_api_key or not resolved.cerebras_api_key:
            await websocket.close(
                code=1008,
                reason="LiveKit needs OPENAI_API_KEY and CEREBRAS_API_KEY in the sidecar .env.",
            )
            return
        await websocket.accept()
        turn: Any = None
        connection: FluxConnection | None = None
        tasks: set[asyncio.Task[Any]] = set()
        worker_failed: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        try:
            factory = livekit_turn_factory
            if factory is None:
                # Load heavy audio/ML packages only when this pipeline is selected.
                from sentia_sidecar.livekit_voice import LiveKitVoiceTurn

                factory = LiveKitVoiceTurn
            repository = await snapshots.manifest(workspace_path)
            if repository is None:
                repository = await asyncio.to_thread(build_repository_manifest, workspace_path)
            vocabulary = build_vocabulary(repository, active_file)
            connection = await openai_transcription_gateway.connect(
                openai_voice_api_key,
                OpenAIVoiceOptions(
                    model=model,
                    endpoint=endpoint,
                    sample_rate=24_000,
                    semantic_eot_enabled=resolved.openai_eot_enabled,
                    semantic_silence_ms=resolved.openai_eot_min_silence_ms,
                    eot_threshold=resolved.openai_eot_threshold,
                    eot_inference_timeout_ms=resolved.openai_eot_inference_timeout_ms,
                ),
                select_keyterms(vocabulary),
            )

            async def analyze(question: str, provider: str) -> RepositoryAnswer:
                return await ask_repository(
                    RepositoryQuestion(
                        workspace_path=workspace_path,
                        question=question,
                        provider=AgentProvider(provider),
                    )
                )

            async def analyze_stream(
                question: str, provider: str
            ) -> AsyncIterator[AnswerStreamEvent]:
                body = RepositoryQuestion(
                    workspace_path=workspace_path,
                    question=question,
                    provider=AgentProvider(provider),
                )
                async for event in repository_answer_events(body):
                    yield event

            turn = factory(
                session_id=session_id,
                connection=connection,
                corrector=RepositoryTranscriptCorrector(vocabulary),
                openai_key=openai_voice_api_key,
                cerebras_key=resolved.cerebras_api_key.get_secret_value(),
                cerebras_url=resolved.cerebras_url,
                cerebras_model=resolved.cerebras_model,
                tts_model=tts_model,
                tts_voice=tts_voice,
                tts_url=tts_endpoint.rstrip("/") + "/v1",
                analyze=analyze,
                analyze_stream=analyze_stream,
                progress_phrases=resolved.voice_progress_phrases,
            )

            async def write_output() -> None:
                while True:
                    payload = await turn.outgoing.get()
                    if isinstance(payload, bytes):
                        await websocket.send_bytes(payload)
                    else:
                        await websocket.send_json(payload)
                        if payload.get("type") in {"voice.complete", "voice.error"}:
                            return

            async def read_input() -> None:
                finish_task: asyncio.Task[Any] | None = None
                answer_task: asyncio.Task[Any] | None = None
                while True:
                    incoming = await asyncio.wait_for(websocket.receive(), timeout=180)
                    if incoming["type"] == "websocket.disconnect":
                        return
                    audio = incoming.get("bytes")
                    if isinstance(audio, bytes):
                        if not turn.finished_recording:
                            await turn.audio_input.push(audio)
                        continue
                    text = incoming.get("text")
                    if not isinstance(text, str) or len(text) > 8_000:
                        continue
                    try:
                        control = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(control, dict):
                        continue
                    kind = control.get("type")
                    if kind == "voice.cancel":
                        return
                    if kind == "voice.stop" and finish_task is None:
                        finish_task = asyncio.create_task(turn.finish_recording())
                        tasks.add(finish_task)
                        finish_task.add_done_callback(check_worker)
                    elif kind == "voice.ask" and answer_task is None:
                        question = control.get("question")
                        request_id = control.get("requestId")
                        provider = control.get("provider")
                        if (
                            not isinstance(question, str)
                            or not 1 <= len(question.strip()) <= 2000
                            or not isinstance(request_id, str)
                            or not 1 <= len(request_id) <= 200
                            or provider not in {"claude", "codex"}
                        ):
                            raise VoiceError("Invalid repository question for the voice turn.")

                        async def respond(
                            submitted_id: str = request_id,
                            submitted_question: str = question.strip(),
                            submitted_provider: str = provider,
                            finishing: asyncio.Task[Any] | None = finish_task,
                        ) -> None:
                            if finishing is not None:
                                await finishing
                            await turn.answer(submitted_id, submitted_question, submitted_provider)

                        answer_task = asyncio.create_task(respond())
                        tasks.add(answer_task)
                        answer_task.add_done_callback(check_worker)
                    elif kind == "voice.playback_finished":
                        segment_id = control.get("segmentId")
                        if isinstance(segment_id, str):
                            turn.audio_output.acknowledge(segment_id)

                    # Surface task failures without blocking microphone or device acknowledgments.
                    for task in (finish_task, answer_task):
                        if task is not None and task.done():
                            task.result()

            def check_worker(task: asyncio.Task[Any]) -> None:
                if not task.cancelled() and not worker_failed.done():
                    error = task.exception()
                    if error is not None:
                        worker_failed.set_exception(error)

            await turn.start()
            reader = asyncio.create_task(read_input())
            writer = asyncio.create_task(write_output())
            tasks.update((reader, writer))
            done, _ = await asyncio.wait(
                {reader, writer, worker_failed}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
        except WebSocketDisconnect:
            pass
        except Exception as error:
            # Provider exceptions can contain request bodies/headers: never echo or log them.
            logger.warning("livekit_voice_failed", error_type=type(error).__name__)
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await websocket.send_json(
                    {
                        "type": "voice.error",
                        "sessionId": session_id,
                        "error": "LiveKit voice failed. Check provider credentials and connection.",
                    }
                )
        finally:
            if worker_failed.done() and not worker_failed.cancelled():
                worker_failed.exception()
            else:
                worker_failed.cancel()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            # A disconnected WebSocket can cancel the handler's scope. Shield
            # bounded cleanup so provider connections do not survive that turn.
            with CancelScope(shield=True):
                if turn is not None:
                    await asyncio.wait_for(turn.close(), timeout=10)
                elif connection is not None:
                    await connection.close()

    return app
