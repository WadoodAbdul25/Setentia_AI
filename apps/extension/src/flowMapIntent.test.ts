import { describe, expect, it } from "vitest";

import { explicitFlowMapQuestion } from "./flowMapIntent";

describe("explicit Flow Map intent", () => {
  it.each([
    "Create a flow map for repository questions",
    "I need a flowmap showing the login path",
    "Please show me the auth feature as a flow-map",
    "flow map for voice submission",
    "flowmap: snapshot refresh",
    "flowmap authentication",
  ])("routes an explicit request: %s", (question) => {
    expect(explicitFlowMapQuestion(question)).toBe(question);
  });

  it.each([
    "How does repository question handling work?",
    "What is a flow map?",
    "Explain the FlowMapResponse schema",
    "Do not create a flow map; just explain authentication",
  ])("leaves an ordinary question alone: %s", (question) => {
    expect(explicitFlowMapQuestion(question)).toBeNull();
  });
});
