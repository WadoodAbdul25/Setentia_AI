import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import {
  FLOW_MAP_SCHEMA_VERSION,
  PROTOCOL_VERSION,
  eventEnvelopeSchema,
  extensionToFlowMapPanelMessageSchema,
  flowMapPanelToExtensionMessageSchema,
  extensionToWebviewMessageSchema,
  flowRootSelectionSchema,
  healthResponseSchema,
  projectSnapshotStatusSchema,
  repositoryInvestigationSchema,
  sentiaFlowGraphSchema,
  webviewToExtensionMessageSchema,
} from "./index.js";

function readFlowFixture(name: string): unknown {
  return JSON.parse(
    readFileSync(
      new URL(
        `../../../tests/fixtures/flow-maps/contracts/${name}`,
        import.meta.url,
      ),
      "utf8",
    ),
  ) as unknown;
}

describe("Sentia protocol", () => {
  it("accepts a revisioned project snapshot status", () => {
    const snapshot = projectSnapshotStatusSchema.parse({
      repositoryRevision: 3,
      workspaceName: "sentia",
      snapshotPath: ".sentia/project-structure.json",
      createdAt: "2026-08-19T12:00:00.000Z",
      updatedAt: "2026-08-19T12:01:00.000Z",
      fileCount: 12,
      directoryCount: 4,
      watching: true,
    });

    expect(snapshot.repositoryRevision).toBe(3);
  });

  it("accepts the current event envelope", () => {
    const result = eventEnvelopeSchema.parse({
      protocolVersion: PROTOCOL_VERSION,
      eventId: "evt_1",
      sequence: 1,
      conversationId: null,
      runId: null,
      type: "system.connected",
      createdAt: "2026-07-26T12:00:00.000Z",
      payload: {},
    });

    expect(result.sequence).toBe(1);
  });

  it("rejects privileged webview messages outside the allowlist", () => {
    expect(() =>
      webviewToExtensionMessageSchema.parse({
        type: "filesystem.read",
        path: "/etc/passwd",
      }),
    ).toThrow();
  });

  it("accepts a bounded repository question", () => {
    const message = webviewToExtensionMessageSchema.parse({
      type: "repository.ask",
      requestId: "req_1",
      question: "What is this codebase about?",
      responseMode: "text",
    });
    expect(message.type).toBe("repository.ask");
  });

  it("accepts Flux audio and corrected transcript messages", () => {
    expect(
      webviewToExtensionMessageSchema.parse({
        type: "voice.start",
        requestId: "req_voice",
        sessionId: "voice_1",
      }),
    ).toMatchObject({ type: "voice.start" });

    expect(
      extensionToWebviewMessageSchema.parse({
        type: "voice.transcript",
        payload: {
          sessionId: "voice_1",
          event: "EndOfTurn",
          turnIndex: 0,
          transcript: "author middleware",
          correctedTranscript: "AuthMiddleware",
          endOfTurnConfidence: 0.91,
          corrections: [
            {
              original: "author middleware",
              replacement: "AuthMiddleware",
              confidence: 0.9,
              kind: "class",
              path: "src/AuthMiddleware.ts",
              applied: true,
            },
          ],
          languages: [],
          isFinal: true,
        },
      }),
    ).toMatchObject({ type: "voice.transcript" });
  });

  it("accepts provider selection and connection state", () => {
    expect(
      webviewToExtensionMessageSchema.parse({
        type: "agent.select",
        requestId: "req_agent",
        provider: "codex",
      }),
    ).toMatchObject({ provider: "codex" });

    expect(
      extensionToWebviewMessageSchema.parse({
        type: "agent.status",
        payload: {
          selectedProvider: "codex",
          connection: {
            provider: "codex",
            connected: true,
            authentication: "codex_login",
            message: "Codex login is available.",
          },
        },
      }),
    ).toMatchObject({ type: "agent.status" });
  });

  it("accepts switching to OpenAI Voice", () => {
    expect(
      webviewToExtensionMessageSchema.parse({
        type: "voice.provider.select",
        requestId: "req_voice_provider",
        provider: "openai",
      }),
    ).toMatchObject({ provider: "openai" });

    expect(
      extensionToWebviewMessageSchema.parse({
        type: "openai_voice.status",
        payload: { connected: true, message: "OpenAI Voice is connected." },
      }),
    ).toMatchObject({ type: "openai_voice.status" });
  });

  it("accepts nullable evidence labels emitted by the Python sidecar", () => {
    const message = extensionToWebviewMessageSchema.parse({
      type: "repository.answer",
      requestId: "req_evidence",
      payload: {
        answer: "The provider is implemented here.",
        spokenAnswer: "The provider is implemented in this module.",
        evidence: [
          {
            path: "backend/apps/ai/providers/anthropic_client.py",
            startLine: 8,
            endLine: 20,
            label: null,
          },
        ],
        recommendedMode: "brainstorm",
        modeReason: "The user asked for an explanation.",
        model: "claude-haiku-4-5-20251001",
        filesScanned: 188,
        filesRead: 6,
        selectedFiles: ["backend/apps/ai/providers/anthropic_client.py"],
        usage: { inputTokens: 50, outputTokens: 4537 },
      },
    });

    expect(message.type).toBe("repository.answer");
    if (message.type === "repository.answer") {
      expect(message.payload.evidence[0]?.label).toBeNull();
    }
  });

  it("validates sidecar health responses at the network boundary", () => {
    expect(
      healthResponseSchema.parse({
        status: "ok",
        version: "0.1.0",
        protocolVersion: PROTOCOL_VERSION,
        workflowState: "ready",
      }),
    ).toMatchObject({ status: "ok", workflowState: "ready" });

    expect(() =>
      healthResponseSchema.parse({
        status: "ok",
        version: "0.1.0",
        protocolVersion: "stale",
        workflowState: "ready",
      }),
    ).toThrow();
  });

  it("accepts the shared Flow Graph fixture", () => {
    const graph = sentiaFlowGraphSchema.parse(
      readFlowFixture("valid-flow-graph.json"),
    );

    expect(graph.schemaVersion).toBe(FLOW_MAP_SCHEMA_VERSION);
    expect(graph.rootEntityIds).toEqual(["ent_ts_submit_login"]);
    expect(graph.edges.map((edge) => edge.kind)).toEqual([
      "sends_http_request",
      "handles_http_request",
    ]);
  });

  it("requires a warning when selected anchors are disconnected", () => {
    const payload = readFlowFixture("valid-flow-graph.json") as {
      nodes: Array<Record<string, unknown>>;
      rootEntityIds: string[];
      warnings: Array<Record<string, unknown>>;
    };
    payload.nodes.push({
      ...payload.nodes[0]!,
      id: "node_second_anchor",
      entityId: "ent_second_anchor",
      label: "secondAnchor",
      qualifiedName: "secondAnchor",
    });
    payload.rootEntityIds.push("ent_second_anchor");

    expect(() => sentiaFlowGraphSchema.parse(payload)).toThrow(
      "require an explicit explanation",
    );

    payload.warnings.push({
      code: "disconnected_feature_anchors",
      message: "No evidence-backed path connects the selected anchors.",
      entityIds: payload.rootEntityIds,
    });
    expect(sentiaFlowGraphSchema.parse(payload).rootEntityIds).toEqual([
      "ent_ts_submit_login",
      "ent_second_anchor",
    ]);
  });

  it("accepts the shared repository investigation fixture", () => {
    const investigation = repositoryInvestigationSchema.parse(
      readFlowFixture("valid-repository-investigation.json"),
    );

    expect(investigation.investigationId).toBe("inv_login_fixture");
    expect(investigation.candidateIdentifiers).toEqual([
      "submitLogin",
      "create_session",
    ]);
  });

  it("accepts the dedicated Flow Map editor-tab message boundary", () => {
    const graph = readFlowFixture("valid-flow-graph.json");
    const investigation = readFlowFixture(
      "valid-repository-investigation.json",
    );

    expect(
      flowMapPanelToExtensionMessageSchema.parse({ type: "flow_map.ready" }),
    ).toEqual({ type: "flow_map.ready" });
    expect(
      extensionToFlowMapPanelMessageSchema.parse({
        type: "flow_map.result",
        payload: {
          investigation,
          rootSelection: {
            schemaVersion: FLOW_MAP_SCHEMA_VERSION,
            repositoryRevision: 7,
            rootEntityIds: ["ent_ts_submit_login"],
            viewType: "feature_flow",
            direction: "forward",
            rationale: "The login submission starts the feature flow.",
            unresolvedConcepts: [],
          },
          graph,
          model: "codex-test",
          usage: { inputTokens: 120, outputTokens: 20 },
          investigationUsage: {
            fileSelection: { inputTokens: 45, outputTokens: 8 },
            initialAnchorSelection: { inputTokens: 50, outputTokens: 7 },
            coveragePass: { inputTokens: 25, outputTokens: 5 },
            total: { inputTokens: 120, outputTokens: 20 },
          },
        },
      }),
    ).toMatchObject({ type: "flow_map.result" });
  });

  it("reports an explicitly requested Flow Map back to the sidebar", () => {
    expect(
      extensionToWebviewMessageSchema.parse({
        type: "flow_map.opened",
        requestId: "request-flow-map",
        question: "Create a flow map for authentication",
      }),
    ).toMatchObject({ type: "flow_map.opened" });
  });

  it("rejects duplicate or non-entity Flow Map roots", () => {
    const base = {
      schemaVersion: FLOW_MAP_SCHEMA_VERSION,
      repositoryRevision: 7,
      viewType: "feature_flow",
      direction: "forward",
      rationale: "The login submission is the user-facing entry point.",
      unresolvedConcepts: [],
    } as const;

    expect(() =>
      flowRootSelectionSchema.parse({
        ...base,
        rootEntityIds: ["ent_login", "ent_login"],
      }),
    ).toThrow(/must be unique/);
    expect(() =>
      flowRootSelectionSchema.parse({
        ...base,
        rootEntityIds: ["invented_login"],
      }),
    ).toThrow();
  });

  it("rejects unknown edge endpoints and stale graph evidence", () => {
    const unknownEndpoint = readFlowFixture("valid-flow-graph.json") as {
      edges: Array<{ targetNodeId: string }>;
    };
    const firstUnknownEdge = unknownEndpoint.edges[0];
    if (!firstUnknownEdge) {
      throw new Error("The Flow Graph fixture must include an edge.");
    }
    firstUnknownEdge.targetNodeId = "node_missing";
    expect(() => sentiaFlowGraphSchema.parse(unknownEndpoint)).toThrow(
      /edge endpoints/,
    );

    const staleEvidence = readFlowFixture("valid-flow-graph.json") as {
      edges: Array<{ evidence: Array<{ repositoryRevision: number }> }>;
    };
    const firstStaleSpan = staleEvidence.edges[0]?.evidence[0];
    if (!firstStaleSpan) {
      throw new Error("The Flow Graph fixture must include edge evidence.");
    }
    firstStaleSpan.repositoryRevision = 6;
    expect(() => sentiaFlowGraphSchema.parse(staleEvidence)).toThrow(
      /must match its repository revision/,
    );
  });
});
