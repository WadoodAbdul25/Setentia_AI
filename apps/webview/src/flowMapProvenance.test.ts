import { sentiaFlowGraphSchema, type FlowEdge } from "@sentia/protocol";
import { describe, expect, it } from "vitest";

import {
  edgeTargetProvenance,
  nodeTargetProvenance,
  targetProvenanceBadge,
  targetProvenanceLabel,
} from "./flowMapProvenance";

const span = {
  path: "feature.py",
  startLine: 1,
  startColumn: 0,
  endLine: 1,
  endColumn: 10,
  contentHash: "a".repeat(64),
  repositoryRevision: 1,
};

function edgeWithMetadata(metadata: Record<string, unknown>): FlowEdge {
  return {
    id: "edge_test",
    sourceNodeId: "node_source",
    targetNodeId: "node_target",
    kind: "calls",
    resolution: "unresolved",
    provenance: {
      extractor: "sentia.python.ast",
      extractorVersion: "1.0.0",
      ruleId: "python.call.dynamic",
    },
    evidence: [span],
    label: null,
    conditional: false,
    asynchronous: false,
    metadata,
  };
}

describe("Flow Map target provenance", () => {
  it("reads inspectable relationship signals from edge metadata", () => {
    const signals = edgeTargetProvenance(
      edgeWithMetadata({
        relationshipSignals: {
          rel_one: {
            targetProvenance: "standard_library",
            targetProvenanceBasis: "standard_library_import",
            targetModule: "asyncio",
          },
        },
      }),
    );

    expect(signals).toEqual([
      {
        targetProvenance: "standard_library",
        targetProvenanceBasis: "standard_library_import",
        targetModule: "asyncio",
      },
    ]);
    expect(targetProvenanceLabel(signals[0]!.targetProvenance)).toBe(
      "Standard library",
    );
    expect(targetProvenanceBadge(signals[0]!.targetProvenance)).toBe("Stdlib");
  });

  it("derives node provenance from displayed incoming edges", () => {
    const graph = sentiaFlowGraphSchema.parse({
      schemaVersion: "1",
      mapId: "map_test",
      repositoryRevision: 1,
      rootEntityIds: ["ent_source"],
      viewType: "feature_flow",
      nodes: [
        {
          id: "node_source",
          entityId: "ent_source",
          kind: "function",
          label: "source",
          sourceSpans: [span],
        },
        {
          id: "node_target",
          entityId: "ent_target",
          kind: "unresolved",
          label: "asyncio.run",
          sourceSpans: [span],
        },
      ],
      edges: [
        edgeWithMetadata({
          relationshipSignals: {
            rel_one: {
              targetProvenance: "standard_library",
              targetProvenanceBasis: "standard_library_import",
              targetModule: "asyncio",
            },
          },
        }),
      ],
      frontiers: [],
      warnings: [],
      generatedAt: "2026-08-30T12:00:00.000Z",
    });

    expect(nodeTargetProvenance(graph, "node_target")).toEqual([
      {
        targetProvenance: "standard_library",
        targetProvenanceBasis: "standard_library_import",
        targetModule: "asyncio",
      },
    ]);
    expect(nodeTargetProvenance(graph, "node_source")).toEqual([]);
  });

  it("ignores missing or malformed metadata for older graphs", () => {
    expect(edgeTargetProvenance(edgeWithMetadata({}))).toEqual([]);
    expect(
      edgeTargetProvenance(
        edgeWithMetadata({
          relationshipSignals: {
            rel_bad: { targetProvenance: "probably_external" },
          },
        }),
      ),
    ).toEqual([]);
  });
});
