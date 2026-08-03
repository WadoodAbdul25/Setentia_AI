import { describe, expect, it } from "vitest";

import { buildOpenAISpeechUrl } from "./openaiSpeech.js";

describe("buildOpenAISpeechUrl", () => {
  it("uses the OpenAI speech endpoint", () => {
    expect(buildOpenAISpeechUrl("https://api.openai.com")).toBe(
      "https://api.openai.com/v1/audio/speech",
    );
  });

  it("normalizes an existing realtime path", () => {
    expect(
      buildOpenAISpeechUrl("https://voice.example.com/gateway/v1/realtime"),
    ).toBe("https://voice.example.com/gateway/v1/audio/speech");
  });

  it("rejects WebSocket origins", () => {
    expect(() => buildOpenAISpeechUrl("wss://api.openai.com")).toThrow();
  });
});
