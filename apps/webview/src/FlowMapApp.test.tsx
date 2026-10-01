// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { flowMapResponseSchema } from "@sentia/protocol";
import { FlowMapApp } from "./FlowMapApp";

vi.mock("./flowMapLayout", () => ({
  layoutFlowGraph: vi.fn(() => Promise.resolve({ nodes: [], edges: [] })),
}));

afterEach(cleanup);

function payload() {
  return flowMapResponseSchema.parse(
    JSON.parse(
      readFileSync(
        resolve(
          process.cwd(),
          "../../tests/fixtures/feature-traces/valid-response.json",
        ),
        "utf8",
      ),
    ),
  );
}

describe("Feature trace view", () => {
  it("opens compact stages and exposes source evidence on selection", () => {
    const post = vi.fn();
    const response = payload();
    render(
      <FlowMapApp
        post={post}
        message={{ type: "flow_map.result", payload: response }}
      />,
    );
    expect(
      screen
        .getByRole("button", { name: "Feature trace" })
        .getAttribute("aria-pressed"),
    ).toBe("true");
    expect(screen.getByRole("region", { name: "Trace stages" })).toBeTruthy();
    expect(screen.getByText("LLM tokens by stage")).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: /outcome Save report/ }),
    );
    fireEvent.click(
      within(screen.getByRole("complementary")).getByRole("button", {
        name: /report.py lines 5/,
      }),
    );
    expect(post).toHaveBeenCalledWith({
      type: "flow_map.open_source",
      span: response.featureTrace!.stages[1]!.evidence[0],
    });
  });

  it("shows gaps without adding connections between unconnected stages", () => {
    const response = payload();
    const trace = response.featureTrace!;
    trace.status = "partial";
    trace.stopReason = "budget_exhausted";
    trace.transitions = [];
    trace.gaps = [
      {
        code: "unresolved_transition",
        message: "Task dispatch could not be resolved.",
        stageIds: ["outcome"],
      },
    ];
    render(
      <FlowMapApp
        post={vi.fn()}
        message={{ type: "flow_map.result", payload: response }}
      />,
    );
    expect(
      screen.getByText("Task dispatch could not be resolved."),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "No connection between these stages has been established.",
      ),
    ).toBeTruthy();
  });

  it("keeps unverified lifecycle labels out of the main map", () => {
    const response = payload();
    const trace = response.featureTrace!;
    trace.status = "partial";
    trace.stopReason = "review_failed";
    for (const stage of trace.stages) stage.reviewStatus = "unverified";
    render(
      <FlowMapApp
        post={vi.fn()}
        message={{ type: "flow_map.result", payload: response }}
      />,
    );
    expect(
      screen.queryByRole("button", { name: /outcome Save report/ }),
    ).toBeNull();
    expect(screen.getByText("0 stages")).toBeTruthy();
    expect(screen.getByText("0 connections")).toBeTruthy();
    expect(screen.getByText("Unverified proposal: Save report")).toBeTruthy();
  });

  it("exposes helper paths without presenting them as direct calls", () => {
    const response = payload();
    const transition = response.featureTrace!.transitions[0]!;
    transition.viaEntityIds = ["ent_helper"];
    transition.viaNames = ["report.run_task"];
    transition.asynchronous = true;
    transition.conditional = true;
    render(
      <FlowMapApp
        post={vi.fn()}
        message={{ type: "flow_map.result", payload: response }}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /via 1 intermediate functions/ }),
    );
    expect(screen.getByText("report.run_task")).toBeTruthy();
    expect(screen.getByText(/not a direct call/)).toBeTruthy();
    expect(screen.getByText(/async boundary/)).toBeTruthy();
  });
});
