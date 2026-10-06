import type { VoiceProvider } from "@sentia/protocol";

// The turn detector has already waited for silence; do not add another grace period.
export const VOICE_AUTO_SUBMIT_DELAY_MS = 0;

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
  return "queue";
}
