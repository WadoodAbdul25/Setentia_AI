import { beforeEach, describe, expect, it } from "vitest";

import { useSentiaStore } from "./store";

describe("Sentia webview store", () => {
  beforeEach(() => {
    useSentiaStore.setState({
      events: [],
      workspaceName: null,
      workspaceTrusted: false,
    });
  });

  it("applies extension bootstrap state", () => {
    useSentiaStore.getState().bootstrap({
      extensionVersion: "0.1.0",
      workspaceName: "fixture",
      workspaceTrusted: true,
      agent: {
        selectedProvider: "codex",
        connection: {
          provider: "codex",
          connected: true,
          authentication: "codex_login",
          message: "Codex login is available.",
        },
      },
      anthropic: {
        connected: true,
        model: "claude-haiku-4-5-20251001",
        message: null,
      },
      deepgram: {
        connected: true,
        message: null,
      },
      openaiVoice: {
        connected: false,
        message: null,
      },
      voiceProvider: "deepgram",
      sidecar: {
        status: "healthy",
        workflowState: "ready",
        version: "0.1.0",
        protocolVersion: "1",
        message: null,
      },
    });

    expect(useSentiaStore.getState().workspaceName).toBe("fixture");
    expect(useSentiaStore.getState().sidecar.status).toBe("healthy");
    expect(useSentiaStore.getState().agent.selectedProvider).toBe("codex");
  });
});
