import { describe, expect, it } from "vitest";

import { resolveVoiceTurnAction } from "./voiceSubmission";

describe("voice turn submission policy", () => {
  it("queues a completed Deepgram turn for automatic voice submission", () => {
    expect(
      resolveVoiceTurnAction({
        provider: "deepgram",
        submitRequested: false,
        isFinal: true,
        transcript: "explain the authentication flow",
      }),
    ).toBe("queue");
  });

  it("keeps OpenAI transcription in manual push-to-talk mode", () => {
    expect(
      resolveVoiceTurnAction({
        provider: "openai",
        submitRequested: false,
        isFinal: true,
        transcript: "explain the authentication flow",
      }),
    ).toBe("ignore");
  });

  it("submits either provider after an explicit stop", () => {
    expect(
      resolveVoiceTurnAction({
        provider: "openai",
        submitRequested: true,
        isFinal: true,
        transcript: "explain the authentication flow",
      }),
    ).toBe("submit");
  });

  it("ignores incomplete and empty turns", () => {
    expect(
      resolveVoiceTurnAction({
        provider: "deepgram",
        submitRequested: false,
        isFinal: false,
        transcript: "partial",
      }),
    ).toBe("ignore");
    expect(
      resolveVoiceTurnAction({
        provider: "deepgram",
        submitRequested: false,
        isFinal: true,
        transcript: "   ",
      }),
    ).toBe("ignore");
  });
});
