import { beforeEach, describe, expect, it } from "vitest";

import { useSentiaStore } from "./store";

describe("Sentia webview store", () => {
  beforeEach(() => {
    useSentiaStore.setState({
      events: [],
      flowMapOpened: null,
      workspaceName: null,
      workspaceTrusted: false,
      repositoryAnswer: null,
      repositoryDraft: null,
      requestError: null,
      latestRepositoryRequestId: null,
    });
  });

  it("records an explicitly requested Flow Map without a repository answer", () => {
    useSentiaStore
      .getState()
      .setFlowMapOpened("request-1", "Create a flow map for authentication");

    expect(useSentiaStore.getState().flowMapOpened).toEqual({
      requestId: "request-1",
      question: "Create a flow map for authentication",
    });
    expect(useSentiaStore.getState().repositoryAnswer).toBeNull();
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

  it("accumulates draft text and ignores stale request updates", () => {
    const store = useSentiaStore.getState();
    store.beginRepositoryRequest("new");
    store.appendRepositoryDelta("new", "First ");
    store.appendRepositoryDelta("old", "stale");
    store.setRepositoryProgress("new", "Searching the files.");
    store.setRepositoryProgress("old", "stale stage");
    store.appendRepositoryDelta("new", "paragraph.");
    expect(useSentiaStore.getState().repositoryDraft).toEqual({
      requestId: "new",
      text: "First paragraph.",
      message: "Searching the files.",
      failed: false,
      activities: [],
    });
  });

  it("keeps interrupted drafts visibly incomplete and ignores further deltas", () => {
    const store = useSentiaStore.getState();
    store.beginRepositoryRequest("request");
    store.appendRepositoryDelta("request", "Partial answer.");
    store.setRequestError("request", "Connection lost");
    store.appendRepositoryDelta("request", "late text");
    expect(useSentiaStore.getState().repositoryDraft?.text).toBe(
      "Partial answer.",
    );
    expect(useSentiaStore.getState().repositoryDraft?.failed).toBe(true);
  });

  it("does not let an older request error replace the active draft", () => {
    const store = useSentiaStore.getState();
    store.beginRepositoryRequest("active");
    store.appendRepositoryDelta("active", "Current answer");
    store.setRequestError("old", "Stale error");
    expect(useSentiaStore.getState().requestError).toBeNull();
    expect(useSentiaStore.getState().repositoryDraft?.failed).toBe(false);
  });

  it("updates work-log entries in place and ignores stale and post-final activity", () => {
    const store = useSentiaStore.getState();
    const entry = {
      id: "file_selection",
      stage: "file_selection",
      status: "working" as const,
      message: "Choosing relevant files.",
      details: [],
    };
    store.beginRepositoryRequest("active");
    store.setRepositoryActivity("active", entry);
    store.setRepositoryActivity("old", { ...entry, message: "Stale" });
    store.setRepositoryActivity("active", {
      ...entry,
      status: "completed",
      message: "Selected 8 files.",
      details: ["src/main.py"],
    });
    expect(useSentiaStore.getState().repositoryDraft?.activities).toEqual([
      {
        ...entry,
        status: "completed",
        message: "Selected 8 files.",
        details: ["src/main.py"],
      },
    ]);
    const answer = {
      answer: "Final answer",
      spokenAnswer: "Final answer",
      evidence: [],
      recommendedMode: "brainstorm" as const,
      modeReason: "Explanation",
      model: "fixture",
      filesScanned: 8,
      filesRead: 8,
      selectedFiles: [],
      usage: { inputTokens: 1, outputTokens: 1 },
    };
    store.setRepositoryAnswer("active", answer);
    store.setRepositoryActivity("active", entry);
    store.appendRepositoryDelta("active", "late draft");
    expect(useSentiaStore.getState().repositoryDraft).toBeNull();
    expect(useSentiaStore.getState().repositoryAnswer?.payload).toEqual(answer);
  });
});
