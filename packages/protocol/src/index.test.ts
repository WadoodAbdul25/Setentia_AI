import { describe, expect, it } from "vitest";

import {
  PROTOCOL_VERSION,
  eventEnvelopeSchema,
  extensionToWebviewMessageSchema,
  healthResponseSchema,
  webviewToExtensionMessageSchema,
} from "./index.js";

describe("Sentia protocol", () => {
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
});
