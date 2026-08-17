import type {
  AgentOnboarding,
  AnthropicStatus,
  DeepgramStatus,
  EventEnvelope,
  RepositoryAnswer,
  RepositoryGraph,
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
  repositoryGraph: RepositoryGraph | null;
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
  setRepositoryGraph(graph: RepositoryGraph): void;
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
  repositoryGraph: null,
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
    set({ repositoryAnswer: { requestId, payload }, requestError: null }),
  setRepositoryGraph: (repositoryGraph) => set({ repositoryGraph }),
  setRequestError: (requestId, error) =>
    set({ requestError: { requestId, error } }),
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
