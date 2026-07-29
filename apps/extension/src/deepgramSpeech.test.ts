import { describe, expect, it } from "vitest";

import { buildDeepgramSpeechUrl } from "./deepgramSpeech.js";

describe("buildDeepgramSpeechUrl", () => {
  it("uses the Flux TTS v2 endpoint with raw 24 kHz audio", () => {
    expect(
      buildDeepgramSpeechUrl(
        "https://api.eu.deepgram.com/v2/listen",
        "flux-marcus-en",
      ),
    ).toBe(
      "wss://api.eu.deepgram.com/v2/speak?model=flux-marcus-en&encoding=linear16&sample_rate=24000",
    );
  });

  it("preserves a dedicated endpoint base path", () => {
    expect(
      buildDeepgramSpeechUrl(
        "wss://deepgram.example.com/sentia",
        "flux-haley-en",
      ),
    ).toContain("/sentia/v2/speak?model=flux-haley-en");
  });
});
