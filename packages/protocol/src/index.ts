import { z } from "zod";

export const PROTOCOL_VERSION = "1" as const;
export const FLOW_MAP_SCHEMA_VERSION = "1" as const;

export const workflowStateSchema = z.enum([
  "disconnected",
  "starting",
  "indexing",
  "ready",
  "discussing",
  "planning",
  "awaiting_approval",
  "prompting",
  "executing",
  "testing",
  "completed",
  "failed",
  "cancelled",
]);

export type WorkflowState = z.infer<typeof workflowStateSchema>;

export const evidenceRangeSchema = z.object({
  path: z.string().min(1),
  startLine: z.number().int().positive(),
  endLine: z.number().int().positive(),
  label: z.string().nullable().optional(),
});

export type EvidenceRange = z.infer<typeof evidenceRangeSchema>;

export const eventEnvelopeSchema = z.object({
  protocolVersion: z.literal(PROTOCOL_VERSION),
  eventId: z.string().min(1),
  sequence: z.number().int().nonnegative(),
  conversationId: z.string().nullable().default(null),
  runId: z.string().nullable().default(null),
  type: z.string().min(1),
  createdAt: z.string().datetime(),
  payload: z.record(z.string(), z.unknown()),
});

export type EventEnvelope = z.infer<typeof eventEnvelopeSchema>;

export const sidecarStatusSchema = z.object({
  status: z.enum(["stopped", "starting", "healthy", "failed"]),
  workflowState: workflowStateSchema,
  version: z.string().nullable(),
  protocolVersion: z.string().nullable(),
  message: z.string().nullable(),
});

export type SidecarStatus = z.infer<typeof sidecarStatusSchema>;

export const healthResponseSchema = z.object({
  status: z.literal("ok"),
  version: z.string().min(1),
  protocolVersion: z.literal(PROTOCOL_VERSION),
  workflowState: workflowStateSchema,
});

export type HealthResponse = z.infer<typeof healthResponseSchema>;

export const anthropicStatusSchema = z.object({
  connected: z.boolean(),
  model: z.string().min(1),
  message: z.string().nullable(),
});

export type AnthropicStatus = z.infer<typeof anthropicStatusSchema>;

export const deepgramStatusSchema = z.object({
  connected: z.boolean(),
  message: z.string().nullable(),
});

export type DeepgramStatus = z.infer<typeof deepgramStatusSchema>;

export const openaiVoiceStatusSchema = z.object({
  connected: z.boolean(),
  message: z.string().nullable(),
});

export type OpenAIVoiceStatus = z.infer<typeof openaiVoiceStatusSchema>;

export const voiceProviderSchema = z.enum(["deepgram", "openai"]);

export type VoiceProvider = z.infer<typeof voiceProviderSchema>;

export const fluxModelSchema = z.enum([
  "flux-general-en",
  "flux-general-multi",
]);

export type FluxModel = z.infer<typeof fluxModelSchema>;

export const voiceSessionStateSchema = z.enum([
  "connecting",
  "listening",
  "stopping",
  "thinking",
  "speaking",
  "closed",
  "error",
]);

export type VoiceSessionState = z.infer<typeof voiceSessionStateSchema>;

export const voiceCorrectionSchema = z.object({
  original: z.string().min(1),
  replacement: z.string().min(1),
  confidence: z.number().min(0).max(1),
  kind: z.string().min(1),
  path: z.string().min(1),
  applied: z.boolean(),
});

export type VoiceCorrection = z.infer<typeof voiceCorrectionSchema>;

export const voiceTranscriptSchema = z.object({
  sessionId: z.string().min(1),
  event: z.enum([
    "StartOfTurn",
    "Update",
    "EagerEndOfTurn",
    "TurnResumed",
    "EndOfTurn",
  ]),
  turnIndex: z.number().int().nonnegative(),
  transcript: z.string(),
  correctedTranscript: z.string(),
  endOfTurnConfidence: z.number().min(0).max(1).nullable(),
  corrections: z.array(voiceCorrectionSchema),
  languages: z.array(z.string()),
  isFinal: z.boolean(),
});

export type VoiceTranscript = z.infer<typeof voiceTranscriptSchema>;

export const voiceServerMessageSchema = z.discriminatedUnion("type", [
  z.object({
    type: z.literal("voice.status"),
    sessionId: z.string().min(1),
    state: voiceSessionStateSchema,
    message: z.string().nullable(),
  }),
  z.object({
    type: z.literal("voice.transcript"),
    payload: voiceTranscriptSchema,
  }),
  z.object({
    type: z.literal("voice.error"),
    sessionId: z.string().min(1),
    error: z.string().min(1),
  }),
]);

export type VoiceServerMessage = z.infer<typeof voiceServerMessageSchema>;

export const agentProviderSchema = z.enum(["claude", "codex"]);

export type AgentProvider = z.infer<typeof agentProviderSchema>;

export const agentConnectionStatusSchema = z.object({
  provider: agentProviderSchema,
  connected: z.boolean(),
  authentication: z.string().min(1),
  message: z.string().min(1),
});

export type AgentConnectionStatus = z.infer<typeof agentConnectionStatusSchema>;

export const agentLoginStartSchema = z.object({
  provider: z.literal("codex"),
  authUrl: z.string().url(),
});

export type AgentLoginStart = z.infer<typeof agentLoginStartSchema>;

export const agentOnboardingSchema = z.object({
  selectedProvider: agentProviderSchema.nullable(),
  connection: agentConnectionStatusSchema.nullable(),
});

export type AgentOnboarding = z.infer<typeof agentOnboardingSchema>;

export const projectSnapshotStatusSchema = z.object({
  repositoryRevision: z.number().int().positive(),
  workspaceName: z.string().min(1),
  snapshotPath: z.string().min(1),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
  fileCount: z.number().int().nonnegative(),
  directoryCount: z.number().int().nonnegative(),
  watching: z.boolean(),
});

export type ProjectSnapshotStatus = z.infer<typeof projectSnapshotStatusSchema>;

export const workingModeSchema = z.enum(["brainstorm", "build"]);

export type WorkingMode = z.infer<typeof workingModeSchema>;

export const tokenUsageSchema = z.object({
  inputTokens: z.number().int().nonnegative(),
  outputTokens: z.number().int().nonnegative(),
});

export type TokenUsage = z.infer<typeof tokenUsageSchema>;

export const flowInvestigationUsageSchema = z
  .object({
    fileSelection: tokenUsageSchema,
    initialAnchorSelection: tokenUsageSchema,
    coveragePass: tokenUsageSchema,
    total: tokenUsageSchema,
  })
  .strict()
  .superRefine((usage, context) => {
    const inputTokens =
      usage.fileSelection.inputTokens +
      usage.initialAnchorSelection.inputTokens +
      usage.coveragePass.inputTokens;
    const outputTokens =
      usage.fileSelection.outputTokens +
      usage.initialAnchorSelection.outputTokens +
      usage.coveragePass.outputTokens;
    if (
      usage.total.inputTokens !== inputTokens ||
      usage.total.outputTokens !== outputTokens
    ) {
      context.addIssue({
        code: "custom",
        message: "Flow investigation usage total must equal its stage usage.",
        path: ["total"],
      });
    }
  });

export type FlowInvestigationUsage = z.infer<
  typeof flowInvestigationUsageSchema
>;

export const repositoryAnswerSchema = z.object({
  answer: z.string().min(1),
  spokenAnswer: z.string().min(1).max(3_000),
  evidence: z.array(evidenceRangeSchema),
  recommendedMode: workingModeSchema,
  modeReason: z.string().min(1).max(240),
  model: z.string().min(1),
  filesScanned: z.number().int().nonnegative(),
  filesRead: z.number().int().nonnegative(),
  selectedFiles: z.array(z.string().min(1)),
  usage: tokenUsageSchema,
});

export type RepositoryAnswer = z.infer<typeof repositoryAnswerSchema>;

// Sidecar/extension-only controls. PCM audio is sent in binary WebSocket frames,
// never exposed to the webview; acknowledgments come from the native device.
export const liveKitServerControlSchema = z.discriminatedUnion("type", [
  z.object({
    type: z.literal("voice.answer"),
    sessionId: z.string().min(1),
    requestId: z.string().min(1),
    payload: repositoryAnswerSchema,
  }),
  z.object({
    type: z.enum([
      "voice.audio.start",
      "voice.audio.flush",
      "voice.audio.clear",
    ]),
    sessionId: z.string().min(1),
    segmentId: z.string().min(1),
  }),
  z.object({ type: z.literal("voice.complete"), sessionId: z.string().min(1) }),
]);

export const flowEntityKindSchema = z.enum([
  "repository",
  "directory",
  "package",
  "process",
  "file",
  "module",
  "class",
  "interface",
  "function",
  "method",
  "component",
  "http_endpoint",
  "message_channel",
  "external",
  "unresolved",
]);

export type FlowEntityKind = z.infer<typeof flowEntityKindSchema>;

export const flowRelationshipKindSchema = z.enum([
  "contains",
  "imports",
  "exports",
  "calls",
  "constructs",
  "inherits",
  "implements",
  "decorates",
  "awaits",
  "returns",
  "raises",
  "registers_handler",
  "emits_message",
  "handles_message",
  "sends_http_request",
  "handles_http_request",
  "publishes_event",
  "consumes_event",
]);

export type FlowRelationshipKind = z.infer<typeof flowRelationshipKindSchema>;

const traversableFlowRelationshipKinds: ReadonlySet<FlowRelationshipKind> =
  new Set([
    "calls",
    "constructs",
    "awaits",
    "registers_handler",
    "emits_message",
    "handles_message",
    "sends_http_request",
    "handles_http_request",
    "publishes_event",
    "consumes_event",
  ]);

export const flowResolutionSchema = z.enum([
  "compiler_resolved",
  "statically_resolved",
  "framework_inferred",
  "unresolved",
  "runtime_observed",
]);

export type FlowResolution = z.infer<typeof flowResolutionSchema>;

export const flowViewTypeSchema = z.literal("feature_flow");
export type FlowViewType = z.infer<typeof flowViewTypeSchema>;

export const flowDirectionSchema = z.literal("forward");
export type FlowDirection = z.infer<typeof flowDirectionSchema>;

export const flowFrontierDirectionSchema = z.enum(["incoming", "outgoing"]);
export type FlowFrontierDirection = z.infer<typeof flowFrontierDirectionSchema>;

const flowEntityIdSchema = z.string().regex(/^ent_[A-Za-z0-9_-]+$/);
const flowNodeIdSchema = z.string().regex(/^node_[A-Za-z0-9_-]+$/);

export const flowSourceSpanSchema = z
  .object({
    path: z
      .string()
      .min(1)
      .max(500)
      .refine(
        (path) =>
          !path.startsWith("/") &&
          !path.includes("\\") &&
          !path.split("/").includes(".."),
        "Flow source paths must be workspace-relative POSIX paths.",
      ),
    startLine: z.number().int().positive(),
    startColumn: z.number().int().nonnegative(),
    endLine: z.number().int().positive(),
    endColumn: z.number().int().nonnegative(),
    contentHash: z.string().regex(/^[a-f0-9]{64}$/),
    repositoryRevision: z.number().int().positive(),
  })
  .strict()
  .superRefine((span, context) => {
    const endPrecedesStart =
      span.endLine < span.startLine ||
      (span.endLine === span.startLine && span.endColumn < span.startColumn);
    if (endPrecedesStart) {
      context.addIssue({
        code: "custom",
        message: "Flow source span end must not precede its start.",
        path: ["endLine"],
      });
    }
  });

export type FlowSourceSpan = z.infer<typeof flowSourceSpanSchema>;

export const flowProvenanceSchema = z
  .object({
    extractor: z.string().min(1).max(120),
    extractorVersion: z.string().min(1).max(80),
    ruleId: z.string().min(1).max(160),
  })
  .strict();

export type FlowProvenance = z.infer<typeof flowProvenanceSchema>;

export const repositoryInvestigationSchema = z
  .object({
    schemaVersion: z.enum(["1", "2"]),
    investigationId: z.string().regex(/^inv_[A-Za-z0-9_-]+$/),
    repositoryRevision: z.number().int().positive(),
    question: z.string().min(1).max(2_000),
    provider: agentProviderSchema,
    selectedFiles: z.array(z.string().min(1).max(500)).max(250).default([]),
    readSpans: z.array(flowSourceSpanSchema).max(500).default([]),
    evidence: z.array(flowSourceSpanSchema).max(100).default([]),
    searchQueries: z.array(z.string().min(1).max(2_000)).max(100).default([]),
    candidateIdentifiers: z
      .array(z.string().min(1).max(1_000))
      .max(500)
      .default([]),
    initialAnchorEntityIds: z.array(flowEntityIdSchema).max(8).default([]),
    coverageAnchorEntityIds: z.array(flowEntityIdSchema).max(8).default([]),
    createdAt: z.string().datetime(),
  })
  .strict()
  .superRefine((investigation, context) => {
    if (
      investigation.schemaVersion === "1" &&
      (investigation.initialAnchorEntityIds.length > 3 ||
        investigation.coverageAnchorEntityIds.length > 2)
    ) {
      context.addIssue({
        code: "custom",
        message:
          "V1 investigations allow at most 3 initial and 2 coverage anchors.",
      });
    }
    const spans = [...investigation.readSpans, ...investigation.evidence];
    if (
      spans.some(
        (span) => span.repositoryRevision !== investigation.repositoryRevision,
      )
    ) {
      context.addIssue({
        code: "custom",
        message: "Investigation spans must match its repository revision.",
      });
    }
    if (
      new Set(investigation.selectedFiles).size !==
      investigation.selectedFiles.length
    ) {
      context.addIssue({
        code: "custom",
        message: "Investigation selected files must be unique.",
        path: ["selectedFiles"],
      });
    }
    if (
      new Set(investigation.initialAnchorEntityIds).size !==
      investigation.initialAnchorEntityIds.length
    ) {
      context.addIssue({
        code: "custom",
        message: "Initial Flow Map anchor entity IDs must be unique.",
        path: ["initialAnchorEntityIds"],
      });
    }
    if (
      new Set(investigation.coverageAnchorEntityIds).size !==
      investigation.coverageAnchorEntityIds.length
    ) {
      context.addIssue({
        code: "custom",
        message: "Coverage-pass Flow Map anchor entity IDs must be unique.",
        path: ["coverageAnchorEntityIds"],
      });
    }
    if (
      investigation.coverageAnchorEntityIds.some((entityId) =>
        investigation.initialAnchorEntityIds.includes(entityId),
      )
    ) {
      context.addIssue({
        code: "custom",
        message: "Coverage-pass Flow Map anchors must be additional entities.",
        path: ["coverageAnchorEntityIds"],
      });
    }
  });

export type RepositoryInvestigation = z.infer<
  typeof repositoryInvestigationSchema
>;

export const flowRootSelectionSchema = z
  .object({
    schemaVersion: z.enum(["1", "2"]),
    repositoryRevision: z.number().int().positive(),
    rootEntityIds: z.array(flowEntityIdSchema).min(1).max(8),
    viewType: flowViewTypeSchema.default("feature_flow"),
    direction: flowDirectionSchema.default("forward"),
    rationale: z.string().min(1).max(1_000),
    unresolvedConcepts: z.array(z.string().min(1).max(500)).max(20).default([]),
  })
  .strict()
  .superRefine((selection, context) => {
    if (selection.schemaVersion === "1" && selection.rootEntityIds.length > 3) {
      context.addIssue({
        code: "custom",
        message: "V1 root selections allow at most 3 anchors.",
      });
    }
    if (
      new Set(selection.rootEntityIds).size !== selection.rootEntityIds.length
    ) {
      context.addIssue({
        code: "custom",
        message: "Flow root entity IDs must be unique.",
        path: ["rootEntityIds"],
      });
    }
  });

export type FlowRootSelection = z.infer<typeof flowRootSelectionSchema>;

export const flowNodeSchema = z
  .object({
    id: flowNodeIdSchema,
    entityId: flowEntityIdSchema,
    kind: flowEntityKindSchema,
    label: z.string().min(1).max(240),
    qualifiedName: z.string().max(1_000).nullable().default(null),
    parentNodeId: flowNodeIdSchema.nullable().default(null),
    sourceSpans: z.array(flowSourceSpanSchema).max(100).default([]),
    expandable: z.boolean().default(false),
    hiddenNeighborCount: z.number().int().nonnegative().default(0),
    metadata: z.record(z.string(), z.unknown()).default({}),
  })
  .strict();

export type FlowNode = z.infer<typeof flowNodeSchema>;

export const flowEdgeSchema = z
  .object({
    id: z.string().regex(/^edge_[A-Za-z0-9_-]+$/),
    sourceNodeId: flowNodeIdSchema,
    targetNodeId: flowNodeIdSchema,
    kind: flowRelationshipKindSchema,
    resolution: flowResolutionSchema,
    provenance: flowProvenanceSchema,
    evidence: z.array(flowSourceSpanSchema).min(1).max(100),
    label: z.string().max(240).nullable().default(null),
    conditional: z.boolean().default(false),
    asynchronous: z.boolean().default(false),
    metadata: z.record(z.string(), z.unknown()).default({}),
  })
  .strict();

export type FlowEdge = z.infer<typeof flowEdgeSchema>;

export const flowFrontierSchema = z
  .object({
    nodeId: flowNodeIdSchema,
    direction: flowFrontierDirectionSchema,
    hiddenNeighborCount: z.number().int().positive(),
    relationshipKinds: z.array(flowRelationshipKindSchema).min(1),
  })
  .strict();

export type FlowFrontier = z.infer<typeof flowFrontierSchema>;

export const flowWarningSchema = z
  .object({
    code: z.string().min(1).max(120),
    message: z.string().min(1).max(1_000),
    entityIds: z.array(flowEntityIdSchema).max(100).default([]),
  })
  .strict();

export type FlowWarning = z.infer<typeof flowWarningSchema>;

export const sentiaFlowGraphSchema = z
  .object({
    schemaVersion: z.enum(["1", "2"]),
    mapId: z.string().regex(/^map_[A-Za-z0-9_-]+$/),
    repositoryRevision: z.number().int().positive(),
    rootEntityIds: z.array(flowEntityIdSchema).min(1).max(8),
    viewType: flowViewTypeSchema.default("feature_flow"),
    nodes: z.array(flowNodeSchema).min(1).max(500),
    edges: z.array(flowEdgeSchema).max(2_000).default([]),
    frontiers: z.array(flowFrontierSchema).max(500).default([]),
    warnings: z.array(flowWarningSchema).max(100).default([]),
    generatedAt: z.string().datetime(),
  })
  .strict()
  .superRefine((graph, context) => {
    if (graph.schemaVersion === "1" && graph.rootEntityIds.length > 3) {
      context.addIssue({
        code: "custom",
        message: "V1 Flow Graphs allow at most 3 anchors.",
      });
    }
    const nodeIds = graph.nodes.map((node) => node.id);
    const entityIds = graph.nodes.map((node) => node.entityId);
    const knownNodes = new Set(nodeIds);
    const knownEntities = new Set(entityIds);

    if (knownNodes.size !== nodeIds.length) {
      context.addIssue({
        code: "custom",
        message: "Flow node IDs must be unique.",
      });
    }
    if (knownEntities.size !== entityIds.length) {
      context.addIssue({
        code: "custom",
        message: "Flow entity IDs may appear only once in a Flow Graph.",
      });
    }
    const edgeIds = graph.edges.map((edge) => edge.id);
    if (new Set(edgeIds).size !== edgeIds.length) {
      context.addIssue({
        code: "custom",
        message: "Flow edge IDs must be unique.",
      });
    }
    if (new Set(graph.rootEntityIds).size !== graph.rootEntityIds.length) {
      context.addIssue({
        code: "custom",
        message: "Flow root entity IDs must be unique.",
        path: ["rootEntityIds"],
      });
    }
    if (graph.rootEntityIds.some((root) => !knownEntities.has(root))) {
      context.addIssue({
        code: "custom",
        message: "Every Flow Graph root must be represented by a node.",
        path: ["rootEntityIds"],
      });
    }
    if (
      graph.nodes.some(
        (node) => node.parentNodeId && !knownNodes.has(node.parentNodeId),
      )
    ) {
      context.addIssue({
        code: "custom",
        message: "Flow node parents must resolve within the graph.",
        path: ["nodes"],
      });
    }
    if (graph.nodes.some((node) => node.parentNodeId === node.id)) {
      context.addIssue({
        code: "custom",
        message: "A Flow node may not be its own parent.",
        path: ["nodes"],
      });
    }
    if (
      graph.edges.some(
        (edge) =>
          !knownNodes.has(edge.sourceNodeId) ||
          !knownNodes.has(edge.targetNodeId),
      )
    ) {
      context.addIssue({
        code: "custom",
        message: "Flow edge endpoints must resolve within the graph.",
        path: ["edges"],
      });
    }
    if (graph.frontiers.some((frontier) => !knownNodes.has(frontier.nodeId))) {
      context.addIssue({
        code: "custom",
        message: "Flow frontiers must reference nodes in the graph.",
        path: ["frontiers"],
      });
    }
    if (graph.rootEntityIds.length > 1) {
      const nodeIdByEntity = new Map(
        graph.nodes.map((node) => [node.entityId, node.id]),
      );
      const rootNodeIds = graph.rootEntityIds
        .map((rootEntityId) => nodeIdByEntity.get(rootEntityId))
        .filter((nodeId): nodeId is string => nodeId !== undefined);
      const adjacency = new Map(
        nodeIds.map((nodeId) => [nodeId, new Set<string>()]),
      );
      for (const edge of graph.edges) {
        if (!traversableFlowRelationshipKinds.has(edge.kind)) {
          continue;
        }
        adjacency.get(edge.sourceNodeId)?.add(edge.targetNodeId);
        adjacency.get(edge.targetNodeId)?.add(edge.sourceNodeId);
      }
      const firstRootNodeId = rootNodeIds[0];
      if (
        rootNodeIds.length === graph.rootEntityIds.length &&
        firstRootNodeId
      ) {
        const reachable = new Set([firstRootNodeId]);
        const pending = [firstRootNodeId];
        while (pending.length > 0) {
          const current = pending.pop();
          if (!current) {
            continue;
          }
          for (const neighbor of adjacency.get(current) ?? []) {
            if (!reachable.has(neighbor)) {
              reachable.add(neighbor);
              pending.push(neighbor);
            }
          }
        }
        if (
          rootNodeIds.some((rootNodeId) => !reachable.has(rootNodeId)) &&
          !graph.warnings.some(
            (warning) => warning.code === "disconnected_feature_anchors",
          )
        ) {
          context.addIssue({
            code: "custom",
            message:
              "Disconnected Flow Graph anchors require an explicit explanation warning.",
            path: ["warnings"],
          });
        }
      }
    }
    const spans = [
      ...graph.nodes.flatMap((node) => node.sourceSpans),
      ...graph.edges.flatMap((edge) => edge.evidence),
    ];
    if (
      spans.some((span) => span.repositoryRevision !== graph.repositoryRevision)
    ) {
      context.addIssue({
        code: "custom",
        message: "Flow Graph evidence must match its repository revision.",
      });
    }
  });

export type SentiaFlowGraph = z.infer<typeof sentiaFlowGraphSchema>;

export const stackTechnologySchema = z
  .object({
    name: z.string().min(1),
    category: z.string().min(1),
    status: z.enum(["declared", "observed"]),
    paths: z.array(z.string()),
    adapterSupport: z.enum(["partial", "unsupported"]),
  })
  .strict();
export type StackTechnology = z.infer<typeof stackTechnologySchema>;

export const stackProfileSchema = z
  .object({
    repositoryRevision: z.number().int().positive(),
    technologies: z.array(stackTechnologySchema),
    languages: z.array(z.string()),
    limitations: z.array(z.string()),
  })
  .strict();
export type StackProfile = z.infer<typeof stackProfileSchema>;

export const featureSpecificationSchema = z
  .object({
    actor: z.string().min(1).max(500),
    input: z.string().min(1).max(500),
    behavior: z.string().min(1).max(1000),
    outcome: z.string().min(1).max(500),
    alternatives: z.array(z.string()).max(8),
    openQuestions: z.array(z.string()).max(8),
  })
  .strict();
export type FeatureSpecification = z.infer<typeof featureSpecificationSchema>;

export const traceCandidateSchema = z
  .object({
    entityId: flowEntityIdSchema,
    name: z.string(),
    decision: z.enum(["include", "supporting", "exclude", "uncertain"]),
    responsibility: z.string(),
    reason: z.string(),
    question: z.string(),
    sourceReviewed: z.boolean(),
    evidence: z.array(flowSourceSpanSchema),
  })
  .strict();
export type TraceCandidate = z.infer<typeof traceCandidateSchema>;

export const traceStageSchema = z
  .object({
    id: z.string().min(1),
    label: z.string().min(1),
    role: z.enum([
      "entry",
      "input",
      "orchestration",
      "transformation",
      "persistence",
      "outcome",
    ]),
    entityIds: z.array(flowEntityIdSchema).min(1),
    evidence: z.array(flowSourceSpanSchema),
    responsibility: z.string().default(""),
    inclusionReason: z.string().default(""),
    reviewStatus: z
      .enum(["unverified", "source_reviewed"])
      .default("unverified"),
  })
  .strict();
export type TraceStage = z.infer<typeof traceStageSchema>;

export const traceTransitionSchema = z
  .object({
    id: z.string().min(1),
    sourceStageId: z.string().min(1),
    targetStageId: z.string().min(1),
    relationshipIds: z.array(z.string()).min(1),
    evidence: z.array(flowSourceSpanSchema).min(1),
    kind: z.enum(["execution", "registration", "structural"]),
    viaEntityIds: z.array(flowEntityIdSchema).default([]),
    viaNames: z.array(z.string()).default([]),
    asynchronous: z.boolean().default(false),
    conditional: z.boolean().default(false),
  })
  .strict();
export type TraceTransition = z.infer<typeof traceTransitionSchema>;

export const traceGapSchema = z
  .object({
    code: z.string().min(1),
    message: z.string().min(1),
    stageIds: z.array(z.string()),
  })
  .strict();
export type TraceGap = z.infer<typeof traceGapSchema>;

export const traceUsageSchema = z
  .object({
    stage: z.enum(["planning", "investigation", "review"]),
    inputTokens: z.number().int().nonnegative(),
    outputTokens: z.number().int().nonnegative(),
  })
  .strict();
export type TraceUsage = z.infer<typeof traceUsageSchema>;

export const featureTraceSchema = z
  .object({
    schemaVersion: z.literal("2"),
    traceId: z.string().regex(/^trace_[A-Za-z0-9_-]+$/),
    repositoryRevision: z.number().int().positive(),
    scope: z.enum(["feature", "subsystem", "overview"]),
    requestedOutcome: z.string().min(1),
    stackProfile: stackProfileSchema,
    stages: z.array(traceStageSchema),
    transitions: z.array(traceTransitionSchema),
    gaps: z.array(traceGapSchema),
    status: z.enum(["partial", "supported"]),
    stopReason: z.enum([
      "coverage_satisfied",
      "budget_exhausted",
      "no_progress",
      "review_failed",
    ]),
    usage: z.array(traceUsageSchema),
    limitations: z.array(z.string()),
    featureSpecification: featureSpecificationSchema.nullable().optional(),
    candidates: z.array(traceCandidateSchema).default([]),
  })
  .strict()
  .superRefine((trace, context) => {
    const stageIds = new Set(trace.stages.map((stage) => stage.id));
    if (stageIds.size !== trace.stages.length) {
      context.addIssue({
        code: "custom",
        message: "Feature trace stage IDs must be unique.",
      });
    }
    if (
      new Set(trace.transitions.map((transition) => transition.id)).size !==
      trace.transitions.length
    ) {
      context.addIssue({
        code: "custom",
        message: "Feature trace transition IDs must be unique.",
      });
    }
    if (
      trace.transitions.some(
        (transition) =>
          !stageIds.has(transition.sourceStageId) ||
          !stageIds.has(transition.targetStageId),
      )
    ) {
      context.addIssue({
        code: "custom",
        message: "Feature trace transitions must reference known stages.",
      });
    }
    if (
      trace.gaps.some((gap) => gap.stageIds.some((id) => !stageIds.has(id)))
    ) {
      context.addIssue({
        code: "custom",
        message: "Feature trace gaps must reference known stages.",
      });
    }
    const spans = [
      ...trace.stages.flatMap((stage) => stage.evidence),
      ...trace.transitions.flatMap((transition) => transition.evidence),
      ...trace.candidates.flatMap((candidate) => candidate.evidence),
    ];
    if (
      new Set(trace.candidates.map((candidate) => candidate.entityId)).size !==
        trace.candidates.length ||
      trace.candidates.some(
        (candidate) => candidate.sourceReviewed && !candidate.evidence.length,
      )
    ) {
      context.addIssue({
        code: "custom",
        message:
          "Candidates need unique IDs and evidence for reviewed decisions.",
      });
    }
    const accepted = new Set(
      trace.candidates
        .filter(
          (candidate) =>
            candidate.sourceReviewed && candidate.decision === "include",
        )
        .map((candidate) => candidate.entityId),
    );
    if (
      trace.featureSpecification &&
      trace.stages.some(
        (stage) =>
          stage.reviewStatus !== "source_reviewed" ||
          !stage.inclusionReason ||
          !stage.evidence.length ||
          stage.entityIds.some((id) => !accepted.has(id)),
      )
    ) {
      context.addIssue({
        code: "custom",
        message: "Feature stages require a reviewed inclusion decision.",
      });
    }
    if (
      trace.stackProfile.repositoryRevision !== trace.repositoryRevision ||
      spans.some((span) => span.repositoryRevision !== trace.repositoryRevision)
    ) {
      context.addIssue({
        code: "custom",
        message:
          "Feature trace evidence and stack must match its repository revision.",
      });
    }
    if (
      trace.status === "supported" &&
      (trace.gaps.length > 0 ||
        trace.stopReason !== "coverage_satisfied" ||
        trace.stages.length === 0 ||
        trace.stages.some((stage) => stage.evidence.length === 0))
    ) {
      context.addIssue({
        code: "custom",
        message:
          "Supported feature traces require satisfied coverage and no gaps.",
      });
    }
  });
export type FeatureTrace = z.infer<typeof featureTraceSchema>;

export const flowMapRequestSchema = z
  .object({
    workspacePath: z.string().min(1),
    question: z.string().min(1).max(2_000),
    provider: agentProviderSchema.default("claude"),
    engineVersion: z.enum(["1", "2"]).default("1"),
    maxDepth: z.number().int().min(0).max(25).default(5),
    maxNodes: z.number().int().min(1).max(500).default(50),
    maxEdges: z.number().int().min(1).max(2_000).default(2_000),
    maxBranchesPerNode: z.number().int().min(1).max(100).default(8),
  })
  .strict();

export type FlowMapRequest = z.infer<typeof flowMapRequestSchema>;

export const flowMapResponseSchema = z
  .object({
    investigation: repositoryInvestigationSchema,
    rootSelection: flowRootSelectionSchema,
    graph: sentiaFlowGraphSchema,
    model: z.string().min(1).max(200),
    usage: tokenUsageSchema,
    investigationUsage: flowInvestigationUsageSchema,
    featureTrace: featureTraceSchema.nullable().optional(),
  })
  .strict()
  .superRefine((response, context) => {
    if (
      response.featureTrace &&
      [
        response.investigation.repositoryRevision,
        response.rootSelection.repositoryRevision,
        response.graph.repositoryRevision,
      ].some(
        (revision) => revision !== response.featureTrace?.repositoryRevision,
      )
    ) {
      context.addIssue({
        code: "custom",
        message: "Feature trace must match the response repository revision.",
      });
    }
  });

export type FlowMapResponse = z.infer<typeof flowMapResponseSchema>;

export const flowMapPanelToExtensionMessageSchema = z.discriminatedUnion(
  "type",
  [
    z.object({ type: z.literal("flow_map.ready") }),
    z.object({
      type: z.literal("flow_map.open_source"),
      span: flowSourceSpanSchema,
    }),
    z.object({ type: z.literal("flow_map.retry") }),
    z
      .object({
        type: z.literal("flow_map.export"),
        destination: z.enum(["clipboard", "file"]),
      })
      .strict(),
  ],
);

export type FlowMapPanelToExtensionMessage = z.infer<
  typeof flowMapPanelToExtensionMessageSchema
>;

export const extensionToFlowMapPanelMessageSchema = z.discriminatedUnion(
  "type",
  [
    z.object({
      type: z.literal("flow_map.loading"),
      question: z.string().min(1).max(2_000),
    }),
    z.object({
      type: z.literal("flow_map.result"),
      payload: flowMapResponseSchema,
    }),
    z.object({
      type: z.literal("flow_map.error"),
      error: z.string().min(1),
    }),
  ],
);

export type ExtensionToFlowMapPanelMessage = z.infer<
  typeof extensionToFlowMapPanelMessageSchema
>;

export const spokenAnswerSchema = z.object({
  spokenAnswer: z.string().min(1).max(3_000),
});

export type SpokenAnswer = z.infer<typeof spokenAnswerSchema>;

const requestBase = z.object({ requestId: z.string().min(1) });

export const webviewToExtensionMessageSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("ui.ready") }),
  requestBase.extend({ type: z.literal("sidecar.get_status") }),
  requestBase.extend({ type: z.literal("sidecar.restart") }),
  requestBase.extend({ type: z.literal("diagnostics.open") }),
  requestBase.extend({ type: z.literal("anthropic.connect") }),
  requestBase.extend({ type: z.literal("anthropic.disconnect") }),
  requestBase.extend({ type: z.literal("deepgram.connect") }),
  requestBase.extend({ type: z.literal("deepgram.disconnect") }),
  requestBase.extend({ type: z.literal("openai_voice.connect") }),
  requestBase.extend({ type: z.literal("openai_voice.disconnect") }),
  requestBase.extend({
    type: z.literal("voice.provider.select"),
    provider: voiceProviderSchema,
  }),
  requestBase.extend({
    type: z.literal("agent.select"),
    provider: agentProviderSchema,
  }),
  requestBase.extend({ type: z.literal("agent.connect") }),
  requestBase.extend({ type: z.literal("agent.refresh") }),
  requestBase.extend({ type: z.literal("agent.clear_selection") }),
  requestBase.extend({
    type: z.literal("repository.ask"),
    question: z.string().trim().min(1).max(2_000),
    responseMode: z.enum(["text", "voice"]),
    voiceSessionId: z.string().min(1).optional(),
  }),
  requestBase.extend({
    type: z.literal("voice.start"),
    sessionId: z.string().min(1),
  }),
  requestBase.extend({
    type: z.literal("voice.stop"),
    sessionId: z.string().min(1),
  }),
  requestBase.extend({
    type: z.literal("voice.cancel"),
    sessionId: z.string().min(1),
  }),
  requestBase.extend({
    type: z.literal("editor.open_evidence"),
    evidence: evidenceRangeSchema,
  }),
]);

export type WebviewToExtensionMessage = z.infer<
  typeof webviewToExtensionMessageSchema
>;

export const extensionToWebviewMessageSchema = z.discriminatedUnion("type", [
  z.object({
    type: z.literal("app.bootstrap"),
    payload: z.object({
      extensionVersion: z.string(),
      workspaceName: z.string().nullable(),
      workspaceTrusted: z.boolean(),
      sidecar: sidecarStatusSchema,
      anthropic: anthropicStatusSchema,
      deepgram: deepgramStatusSchema,
      openaiVoice: openaiVoiceStatusSchema,
      voiceProvider: voiceProviderSchema,
      agent: agentOnboardingSchema,
    }),
  }),
  z.object({ type: z.literal("sidecar.status"), payload: sidecarStatusSchema }),
  z.object({
    type: z.literal("anthropic.status"),
    payload: anthropicStatusSchema,
  }),
  z.object({
    type: z.literal("deepgram.status"),
    payload: deepgramStatusSchema,
  }),
  z.object({
    type: z.literal("openai_voice.status"),
    payload: openaiVoiceStatusSchema,
  }),
  z.object({
    type: z.literal("voice.provider.status"),
    payload: voiceProviderSchema,
  }),
  voiceServerMessageSchema,
  z.object({ type: z.literal("agent.status"), payload: agentOnboardingSchema }),
  z.object({ type: z.literal("sidecar.event"), payload: eventEnvelopeSchema }),
  z.object({
    type: z.literal("repository.answer"),
    requestId: z.string().min(1),
    payload: repositoryAnswerSchema,
  }),
  z.object({
    type: z.literal("flow_map.opened"),
    requestId: z.string().min(1),
    question: z.string().min(1).max(2_000),
  }),
  z.object({
    type: z.literal("request.error"),
    requestId: z.string().min(1),
    error: z.string().min(1),
  }),
  z.object({
    type: z.literal("response"),
    requestId: z.string().min(1),
    ok: z.boolean(),
    payload: z.unknown().optional(),
    error: z.string().optional(),
  }),
]);

export type ExtensionToWebviewMessage = z.infer<
  typeof extensionToWebviewMessageSchema
>;
