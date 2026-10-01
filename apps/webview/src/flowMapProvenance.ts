import type { FlowEdge, SentiaFlowGraph } from "@sentia/protocol";

export const TARGET_PROVENANCE_VALUES = [
  "project_owned",
  "dependency_owned",
  "standard_library",
  "unresolved",
] as const;

export type TargetProvenance = (typeof TARGET_PROVENANCE_VALUES)[number];

export interface TargetProvenanceSignal {
  targetProvenance: TargetProvenance;
  targetProvenanceBasis: string;
  targetModule?: string;
}

export function edgeTargetProvenance(edge: FlowEdge): TargetProvenanceSignal[] {
  const relationshipSignals = edge.metadata.relationshipSignals;
  if (!isRecord(relationshipSignals)) {
    return [];
  }

  const signals: TargetProvenanceSignal[] = [];
  for (const candidate of Object.values(relationshipSignals)) {
    if (!isRecord(candidate)) {
      continue;
    }
    const targetProvenance = candidate.targetProvenance;
    const targetProvenanceBasis = candidate.targetProvenanceBasis;
    const targetModule = candidate.targetModule;
    if (
      !isTargetProvenance(targetProvenance) ||
      typeof targetProvenanceBasis !== "string" ||
      (targetModule !== undefined && typeof targetModule !== "string")
    ) {
      continue;
    }
    signals.push({
      targetProvenance,
      targetProvenanceBasis,
      ...(targetModule === undefined ? {} : { targetModule }),
    });
  }
  return uniqueSignals(signals);
}

export function nodeTargetProvenance(
  graph: SentiaFlowGraph,
  nodeId: string,
): TargetProvenanceSignal[] {
  return uniqueSignals(
    graph.edges
      .filter((edge) => edge.targetNodeId === nodeId)
      .flatMap((edge) => edgeTargetProvenance(edge)),
  );
}

export function targetProvenanceLabel(provenance: TargetProvenance): string {
  switch (provenance) {
    case "project_owned":
      return "Project owned";
    case "dependency_owned":
      return "Dependency owned";
    case "standard_library":
      return "Standard library";
    case "unresolved":
      return "Unresolved";
  }
}

export function targetProvenanceBadge(provenance: TargetProvenance): string {
  switch (provenance) {
    case "project_owned":
      return "Project";
    case "dependency_owned":
      return "Dependency";
    case "standard_library":
      return "Stdlib";
    case "unresolved":
      return "Unknown";
  }
}

function uniqueSignals(
  signals: TargetProvenanceSignal[],
): TargetProvenanceSignal[] {
  const seen = new Set<string>();
  return signals.filter((signal) => {
    const key = [
      signal.targetProvenance,
      signal.targetProvenanceBasis,
      signal.targetModule ?? "",
    ].join("\0");
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function isTargetProvenance(value: unknown): value is TargetProvenance {
  return TARGET_PROVENANCE_VALUES.some((candidate) => candidate === value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
