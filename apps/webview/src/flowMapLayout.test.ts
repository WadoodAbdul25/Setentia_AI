import { sentiaFlowGraphSchema } from "@sentia/protocol";
import { describe, expect, it } from "vitest";

import { layoutFlowGraph } from "./flowMapLayout";

const contentHash = "a".repeat(64);

describe("Flow Map layout", () => {
  it("lays a directed feature flow from left to right", async () => {
    const span = {
      path: "service.py",
      startLine: 1,
      startColumn: 0,
      endLine: 1,
      endColumn: 10,
      contentHash,
      repositoryRevision: 2,
    };
    const graph = sentiaFlowGraphSchema.parse({
      schemaVersion: "1",
      mapId: "map_layout",
      repositoryRevision: 2,
      rootEntityIds: ["ent_entry"],
      viewType: "feature_flow",
      nodes: [
        {
          id: "node_entry",
          entityId: "ent_entry",
          kind: "function",
          label: "entry",
          qualifiedName: "entry",
          parentNodeId: null,
          sourceSpans: [span],
          expandable: false,
          hiddenNeighborCount: 0,
          metadata: {},
        },
        {
          id: "node_worker",
          entityId: "ent_worker",
          kind: "function",
          label: "worker",
          qualifiedName: "worker",
          parentNodeId: null,
          sourceSpans: [{ ...span, startLine: 4, endLine: 4 }],
          expandable: false,
          hiddenNeighborCount: 0,
          metadata: {},
        },
      ],
      edges: [
        {
          id: "edge_entry_worker",
          sourceNodeId: "node_entry",
          targetNodeId: "node_worker",
          kind: "calls",
          resolution: "statically_resolved",
          provenance: {
            extractor: "python_ast",
            extractorVersion: "1",
            ruleId: "direct_call",
          },
          evidence: [span],
          label: null,
          conditional: false,
          asynchronous: false,
          metadata: {
            relationshipSignals: {
              rel_worker: {
                targetProvenance: "project_owned",
                targetProvenanceBasis: "indexed_target_entity",
              },
            },
          },
        },
      ],
      frontiers: [],
      warnings: [],
      generatedAt: "2026-08-21T12:00:00.000Z",
    });

    const layout = await layoutFlowGraph(graph);
    const entry = layout.nodes.find((node) => node.id === "node_entry");
    const worker = layout.nodes.find((node) => node.id === "node_worker");

    expect(entry?.data.root).toBe(true);
    expect(entry?.data.targetProvenance).toEqual([]);
    expect(worker?.position.x).toBeGreaterThan(entry?.position.x ?? 0);
    expect(worker?.data.targetProvenance).toEqual([
      {
        targetProvenance: "project_owned",
        targetProvenanceBasis: "indexed_target_entity",
      },
    ]);
    expect(layout.edges).toHaveLength(1);
    expect(layout.edges[0]?.label).toBe("calls");
  });
});
