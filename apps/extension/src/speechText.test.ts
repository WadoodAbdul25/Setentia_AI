import { describe, expect, it } from "vitest";

import { prepareSpeechText } from "./speechText.js";

describe("prepareSpeechText", () => {
  it("gives the Sentia brand name its intended pronunciation", () => {
    expect(prepareSpeechText("Sentia explains how SENTIA's voice works.")).toBe(
      "Sen-shia explains how Sen-shia's voice works.",
    );
  });

  it("does not alter the name when it is part of another word", () => {
    expect(prepareSpeechText("sentience remains unchanged")).toBe(
      "sentience remains unchanged",
    );
  });
});
