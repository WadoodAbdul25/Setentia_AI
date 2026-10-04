const SENTIA_NAME = /\bSentia\b/giu;

/** Prepare display-safe narration for provider text-to-speech. */
export function prepareSpeechText(text: string): string {
  return text.replace(SENTIA_NAME, "Sen-shia");
}
