import { sentiaFlowGraphSchema, type SentiaFlowGraph } from "@sentia/protocol";
import { describe, expect, it } from "vitest";

import { flowMapEvidenceSummary } from "./flowMapEvidence";

function graphWith(options: {
  rootEntityIds: string[];
  warningCodes?: string[];
  edgeCount?: number;
  frontierCount?: number;
}): SentiaFlowGraph {
  const span = {
    path: "feature.py",
    startLine: 1,
    startColumn: 0,
    endLine: 1,
    endColumn: 10,
    contentHash: "a".repeat(64),
    repositoryRevision: 1,
  };
  const nodeIds = options.rootEntityIds.map(
    (_, index) => `node_${String(index)}`,
  );
  const nodes = options.rootEntityIds.map((entityId, index) => ({
    id: nodeIds[index],
    entityId,
    kind: "function" as const,
    label: entityId,
    qualifiedName: entityId,
    parentNodeId: null,
    sourceSpans: [span],
    expandable: false,
    hiddenNeighborCount: 0,
    metadata: {},
  }));
  const edges = Array.from({ length: options.edgeCount ?? 0 }, (_, index) => ({
    id: `edge_${String(index)}`,
    sourceNodeId: nodeIds[index] ?? nodeIds[0],
    targetNodeId: nodeIds[index + 1] ?? nodeIds[0],
    kind: "calls" as const,
    resolution: "statically_resolved" as const,
    provenance: {
      extractor: "test",
      extractorVersion: "1",
      ruleId: "call",
    },
    evidence: [span],
    label: null,
    conditional: false,
    asynchronous: false,
    metadata: {},
  }));
  return sentiaFlowGraphSchema.parse({
    schemaVersion: "1",
    mapId: "map_test",
    repositoryRevision: 1,
    rootEntityIds: options.rootEntityIds,
    viewType: "feature_flow",
    nodes,
    edges,
    frontiers:
      options.frontierCount && options.frontierCount > 0
        ? [
            {
              nodeId: nodeIds[0],
              direction: "outgoing",
              hiddenNeighborCount: options.frontierCount,
              relationshipKinds: ["calls"],
            },
          ]
        : [],
    warnings: (options.warningCodes ?? []).map((code) => ({
      code,
      message: code,
      entityIds: options.rootEntityIds,
    })),
    generatedAt: "2026-08-21T12:00:00.000Z",
  });
}

describe("Flow Map evidence summary", () => {
  it("does not call an isolated anchor a complete flow", () => {
    expect(
      flowMapEvidenceSummary(graphWith({ rootEntityIds: ["ent_entry"] })),
    ).toBe(
      "Shows one selected anchor. No source-backed runtime relationship is displayed.",
    );
  });

  it("states when selected anchors remain disconnected", () => {
    expect(
      flowMapEvidenceSummary(
        graphWith({
          rootEntityIds: ["ent_entry", "ent_outcome"],
          warningCodes: ["disconnected_feature_anchors"],
        }),
      ),
    ).toContain("some anchors remain disconnected");
  });

  it("states when the displayed view is bounded", () => {
    expect(
      flowMapEvidenceSummary(
        graphWith({
          rootEntityIds: ["ent_entry"],
          edgeCount: 1,
          frontierCount: 2,
        }),
      ),
    ).toContain("outside this bounded view");
  });
});
