import { z } from "zod";

export const PROTOCOL_VERSION = "1" as const;

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

export const repositoryGraphNodeSchema = z.object({
  id: z.string().min(1),
  label: z.string().min(1),
  kind: z.string().min(1),
  path: z.string().min(1),
  summary: z.string().min(1),
  functions: z.array(
    z.object({
      name: z.string().min(1),
      startLine: z.number().int().positive(),
      endLine: z.number().int().positive(),
    }),
  ),
  functionRanges: z.array(
    z.object({
      name: z.string().min(1),
      startLine: z.number().int().positive(),
      endLine: z.number().int().positive(),
    }),
  ),
});
export const repositoryGraphEdgeSchema = z.object({
  source: z.string().min(1),
  target: z.string().min(1),
  edgeType: z.enum(["import", "call"]),
  resolution: z.enum(["exact", "best_effort", "unresolved"]),
});
export const repositoryGraphSchema = z.object({
  nodes: z.array(repositoryGraphNodeSchema),
  edges: z.array(repositoryGraphEdgeSchema),
});
export type RepositoryGraph = z.infer<typeof repositoryGraphSchema>;

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
  requestBase.extend({ type: z.literal("repository.graph") }),
  requestBase.extend({
    type: z.literal("editor.open_file_functions"),
    path: z.string().min(1),
    functions: z.array(
      z.object({
        startLine: z.number().int().positive(),
        endLine: z.number().int().positive(),
      }),
    ),
  }),
  requestBase.extend({
    type: z.literal("repository.ask"),
    question: z.string().trim().min(1).max(2_000),
    responseMode: z.enum(["text", "voice"]),
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
    type: z.literal("repository.graph"),
    requestId: z.string().min(1),
    payload: repositoryGraphSchema,
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
