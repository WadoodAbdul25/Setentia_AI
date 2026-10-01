import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  flowMapResponseSchema,
  flowRootSelectionSchema,
  featureTraceSchema,
} from "./index.js";

function response() {
  return flowMapResponseSchema.parse(
    JSON.parse(
      readFileSync(
        new URL(
          "../../../tests/fixtures/feature-traces/valid-response.json",
          import.meta.url,
        ),
        "utf8",
      ),
    ),
  );
}

describe("Feature trace V2 contract", () => {
  it("reads the same response fixture as the Python validator", () => {
    const result = response();
    expect(result.featureTrace?.schemaVersion).toBe("2");
    expect(result.graph.schemaVersion).toBe("2");
    expect(result.featureTrace?.transitions).toHaveLength(1);
  });

  it("allows eight V2 anchors while preserving the V1 limit", () => {
    const selection = {
      ...response().rootSelection,
      rootEntityIds: Array.from({ length: 8 }, (_, i) => `ent_${String(i)}`),
    };
    expect(flowRootSelectionSchema.parse(selection).rootEntityIds).toHaveLength(
      8,
    );
    expect(
      flowRootSelectionSchema.safeParse({ ...selection, schemaVersion: "1" })
        .success,
    ).toBe(false);
  });

  it("rejects missing stages, stale evidence, and unsupported completeness claims", () => {
    const trace = response().featureTrace!;
    expect(featureTraceSchema.safeParse({ ...trace, stages: [] }).success).toBe(
      false,
    );
    expect(
      featureTraceSchema.safeParse({ ...trace, repositoryRevision: 2 }).success,
    ).toBe(false);
    expect(
      featureTraceSchema.safeParse({
        ...trace,
        gaps: [{ code: "missing", message: "Unknown trigger", stageIds: [] }],
      }).success,
    ).toBe(false);
    expect(
      featureTraceSchema.safeParse({
        ...trace,
        transitions: [{ ...trace.transitions[0], targetStageId: "missing" }],
      }).success,
    ).toBe(false);
  });

  it("preserves legacy partial stages but rejects unreviewed stages in new traces", () => {
    const trace = response().featureTrace!;
    const partial = {
      ...trace,
      featureSpecification: null,
      status: "partial",
      stopReason: "budget_exhausted",
      stages: trace.stages.map((stage) => ({ ...stage, evidence: [] })),
      gaps: [
        {
          code: "incomplete_source",
          message: "Outcome source not read",
          stageIds: ["outcome"],
        },
      ],
    };
    expect(featureTraceSchema.safeParse(partial).success).toBe(true);
    expect(
      featureTraceSchema.safeParse({
        ...partial,
        featureSpecification: trace.featureSpecification,
      }).success,
    ).toBe(false);
    expect(
      featureTraceSchema.safeParse({
        ...partial,
        status: "supported",
        gaps: [],
        stopReason: "coverage_satisfied",
      }).success,
    ).toBe(false);
  });

  it("requires an inspected inclusion decision behind each new stage", () => {
    const trace = response().featureTrace!;
    expect(
      featureTraceSchema.safeParse({ ...trace, candidates: [] }).success,
    ).toBe(false);
    expect(
      featureTraceSchema.safeParse({
        ...trace,
        candidates: trace.candidates.map((candidate) => ({
          ...candidate,
          decision: "exclude",
        })),
      }).success,
    ).toBe(false);
    expect(
      featureTraceSchema.safeParse({
        ...trace,
        candidates: trace.candidates.map((candidate) => ({
          ...candidate,
          evidence: [],
        })),
      }).success,
    ).toBe(false);
  });
});
