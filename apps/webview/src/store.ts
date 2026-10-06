import type {
  AgentOnboarding,
  AnthropicStatus,
  DeepgramStatus,
  EventEnvelope,
  RepositoryAnswer,
  RepositoryActivity,
  SidecarStatus,
  OpenAIVoiceStatus,
  VoiceProvider,
  VoiceSessionState,
  VoiceTranscript,
} from "@sentia/protocol";
import { create } from "zustand";

interface SentiaState {
  agent: AgentOnboarding;
  events: EventEnvelope[];
  anthropic: AnthropicStatus;
  deepgram: DeepgramStatus;
  openaiVoice: OpenAIVoiceStatus;
  voiceProvider: VoiceProvider;
  extensionVersion: string | null;
  sidecar: SidecarStatus;
  workspaceName: string | null;
  workspaceTrusted: boolean;
  repositoryAnswer: { requestId: string; payload: RepositoryAnswer } | null;
  repositoryDraft: {
    requestId: string;
    text: string;
    message: string;
    failed: boolean;
    activities: RepositoryActivity[];
  } | null;
  latestRepositoryRequestId: string | null;
  flowMapOpened: { requestId: string; question: string } | null;
  requestError: { requestId: string; error: string } | null;
  voiceStatus: {
    sessionId: string;
    state: VoiceSessionState;
    message: string | null;
  } | null;
  voiceTranscript: VoiceTranscript | null;
  voiceError: { sessionId: string; error: string } | null;
  addEvent(event: EventEnvelope): void;
  bootstrap(payload: {
    extensionVersion: string;
    sidecar: SidecarStatus;
    workspaceName: string | null;
    workspaceTrusted: boolean;
    anthropic: AnthropicStatus;
    deepgram: DeepgramStatus;
    openaiVoice: OpenAIVoiceStatus;
    voiceProvider: VoiceProvider;
    agent: AgentOnboarding;
  }): void;
  setAgent(agent: AgentOnboarding): void;
  setAnthropic(anthropic: AnthropicStatus): void;
  setDeepgram(deepgram: DeepgramStatus): void;
  setOpenAIVoice(openaiVoice: OpenAIVoiceStatus): void;
  setVoiceProvider(voiceProvider: VoiceProvider): void;
  setRepositoryAnswer(requestId: string, payload: RepositoryAnswer): void;
  beginRepositoryRequest(requestId: string): void;
  appendRepositoryDelta(requestId: string, delta: string): void;
  setRepositoryProgress(requestId: string, message: string): void;
  setRepositoryActivity(requestId: string, activity: RepositoryActivity): void;
  setFlowMapOpened(requestId: string, question: string): void;
  setRequestError(requestId: string, error: string): void;
  setSidecar(sidecar: SidecarStatus): void;
  setVoiceStatus(
    sessionId: string,
    state: VoiceSessionState,
    message: string | null,
  ): void;
  setVoiceTranscript(transcript: VoiceTranscript): void;
  setVoiceError(sessionId: string, error: string): void;
}

const disconnected: SidecarStatus = {
  status: "stopped",
  workflowState: "disconnected",
  version: null,
  protocolVersion: null,
  message: null,
};

export const useSentiaStore = create<SentiaState>((set) => ({
  agent: {
    selectedProvider: null,
    connection: null,
  },
  events: [],
  anthropic: {
    connected: false,
    model: "claude-haiku-4-5-20251001",
    message: null,
  },
  deepgram: { connected: false, message: null },
  openaiVoice: { connected: false, message: null },
  voiceProvider: "deepgram",
  extensionVersion: null,
  sidecar: disconnected,
  workspaceName: null,
  workspaceTrusted: false,
  repositoryAnswer: null,
  repositoryDraft: null,
  latestRepositoryRequestId: null,
  flowMapOpened: null,
  requestError: null,
  voiceStatus: null,
  voiceTranscript: null,
  voiceError: null,
  addEvent: (event) =>
    set((state) => ({ events: [...state.events.slice(-49), event] })),
  bootstrap: (payload) => set(payload),
  setAgent: (agent) => set({ agent }),
  setAnthropic: (anthropic) => set({ anthropic }),
  setDeepgram: (deepgram) => set({ deepgram }),
  setOpenAIVoice: (openaiVoice) => set({ openaiVoice }),
  setVoiceProvider: (voiceProvider) => set({ voiceProvider }),
  setRepositoryAnswer: (requestId, payload) =>
    set((state) => {
      if (
        state.latestRepositoryRequestId &&
        state.latestRepositoryRequestId !== requestId
      )
        return {};
      return {
        repositoryAnswer: { requestId, payload },
        repositoryDraft:
          state.repositoryDraft?.requestId === requestId
            ? null
            : state.repositoryDraft,
        flowMapOpened: null,
        requestError: null,
      };
    }),
  beginRepositoryRequest: (requestId) =>
    set({
      latestRepositoryRequestId: requestId,
      repositoryDraft: {
        requestId,
        text: "",
        message: "Checking the repository…",
        failed: false,
        activities: [],
      },
      requestError: null,
    }),
  appendRepositoryDelta: (requestId, delta) =>
    set((state) => {
      if (
        state.repositoryDraft?.requestId !== requestId ||
        state.repositoryDraft.failed
      )
        return {};
      return {
        repositoryDraft: {
          ...state.repositoryDraft,
          text: state.repositoryDraft.text + delta,
        },
      };
    }),
  setRepositoryProgress: (requestId, message) =>
    set((state) => {
      if (
        state.repositoryDraft?.requestId !== requestId ||
        state.repositoryDraft.failed
      )
        return {};
      return { repositoryDraft: { ...state.repositoryDraft, message } };
    }),
  setRepositoryActivity: (requestId, activity) =>
    set((state) => {
      const draft = state.repositoryDraft;
      if (draft?.requestId !== requestId || draft.failed) return {};
      const activities = [...draft.activities];
      const index = activities.findIndex((entry) => entry.id === activity.id);
      if (index < 0) activities.push(activity);
      else activities[index] = activity;
      return {
        repositoryDraft: {
          ...draft,
          message: activity.message,
          activities: activities.slice(-40),
        },
      };
    }),
  setFlowMapOpened: (requestId, question) =>
    set({
      flowMapOpened: { requestId, question },
      repositoryAnswer: null,
      repositoryDraft: null,
      requestError: null,
    }),
  setRequestError: (requestId, error) =>
    set((state) => {
      if (
        state.latestRepositoryRequestId &&
        state.latestRepositoryRequestId !== requestId
      )
        return {};
      return {
        requestError: { requestId, error },
        repositoryDraft:
          state.repositoryDraft?.requestId === requestId
            ? {
                ...state.repositoryDraft,
                failed: true,
                message: "Incomplete answer—the stream stopped.",
              }
            : state.repositoryDraft,
      };
    }),
  setSidecar: (sidecar) => set({ sidecar }),
  setVoiceStatus: (sessionId, state, message) =>
    set({ voiceStatus: { sessionId, state, message }, voiceError: null }),
  setVoiceTranscript: (voiceTranscript) => set({ voiceTranscript }),
  setVoiceError: (sessionId, error) =>
    set({
      voiceError: { sessionId, error },
      voiceStatus: { sessionId, state: "error", message: error },
    }),
}));
