import { describe, expect, it } from "vitest";

import {
  getActivityLabel,
  getJourneyIndex,
  resolveSentiaMode,
} from "./StateJourney";

describe("visible Sentia journey", () => {
  it("shows Plan mode when the user selects Plan & Build while ready", () => {
    expect(getJourneyIndex("ready", "build")).toBe(1);
    expect(getActivityLabel("ready", "build")).toBe("Plan mode");
  });

  it("distinguishes prompting from implementation", () => {
    expect(getJourneyIndex("prompting", "build")).toBe(2);
    expect(getJourneyIndex("executing", "build")).toBe(3);
    expect(getActivityLabel("prompting", "build")).toBe(
      "Prompting coding agent",
    );
    expect(getActivityLabel("executing", "build")).toBe("Implementing");
  });

  it("uses the model recommendation by default but honors a user override", () => {
    expect(resolveSentiaMode("auto", "build")).toBe("build");
    expect(resolveSentiaMode("brainstorm", "build")).toBe("brainstorm");
    expect(resolveSentiaMode("build", "brainstorm")).toBe("build");
  });
});
