import type { SentiaFlowGraph } from "@sentia/protocol";

export function flowMapEvidenceSummary(graph: SentiaFlowGraph): string {
  const warningCodes = new Set(graph.warnings.map((warning) => warning.code));
  const anchorCount = graph.rootEntityIds.length;
  let summary: string;

  if (anchorCount === 1 && graph.edges.length === 0) {
    summary =
      "Shows one selected anchor. No source-backed runtime relationship is displayed.";
  } else if (anchorCount === 1) {
    summary =
      "Shows source-backed relationships reachable from one selected anchor.";
  } else if (warningCodes.has("disconnected_feature_anchors")) {
    summary = `Shows source-backed relationships for ${String(anchorCount)} selected anchors, but some anchors remain disconnected.`;
  } else if (warningCodes.has("weak_anchor_connection")) {
    summary = `Connects ${String(anchorCount)} selected anchors through source-backed structural relationships; some connections are not a single directed execution path.`;
  } else {
    summary = `Connects ${String(anchorCount)} selected anchors through displayed source-backed relationships.`;
  }

  if (
    graph.frontiers.length > 0 ||
    warningCodes.has("edge_budget_reached") ||
    warningCodes.has("anchor_bridge_budget_reached") ||
    warningCodes.has("anchor_bridge_search_bounded")
  ) {
    summary += " Additional relationships may exist outside this bounded view.";
  }
  return summary;
}
