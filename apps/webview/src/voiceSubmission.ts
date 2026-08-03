import type { VoiceProvider } from "@sentia/protocol";

export const DEEPGRAM_AUTO_SUBMIT_DELAY_MS = 2_000;

export type VoiceTurnAction = "ignore" | "queue" | "submit";

export function resolveVoiceTurnAction(input: {
  provider: VoiceProvider;
  submitRequested: boolean;
  isFinal: boolean;
  transcript: string;
}): VoiceTurnAction {
  if (!input.isFinal || !input.transcript.trim()) {
    return "ignore";
  }
  if (input.submitRequested) {
    return "submit";
  }
  return input.provider === "deepgram" ? "queue" : "ignore";
}
