import { MarkerType, type Edge, type Node } from "@xyflow/react";
import ELK from "elkjs/lib/elk.bundled.js";

import type { FlowEdge, FlowNode, SentiaFlowGraph } from "@sentia/protocol";

import {
  nodeTargetProvenance,
  type TargetProvenanceSignal,
} from "./flowMapProvenance";

export interface FlowNodeData extends Record<string, unknown> {
  flowNode: FlowNode;
  root: boolean;
  targetProvenance: TargetProvenanceSignal[];
}

export interface FlowEdgeData extends Record<string, unknown> {
  flowEdge: FlowEdge;
}

export type FlowCanvasNode = Node<FlowNodeData, "sentiaFlowNode">;
export type FlowCanvasEdge = Edge<FlowEdgeData>;

const elk = new ELK();
const NODE_WIDTH = 250;
const NODE_HEIGHT = 92;

export async function layoutFlowGraph(graph: SentiaFlowGraph): Promise<{
  nodes: FlowCanvasNode[];
  edges: FlowCanvasEdge[];
}> {
  const layout = await elk.layout({
    id: "sentia-flow-map",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.layered.spacing.nodeNodeBetweenLayers": "90",
      "elk.spacing.nodeNode": "45",
    },
    children: graph.nodes.map((node) => ({
      id: node.id,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    })),
    edges: graph.edges.map((edge) => ({
      id: edge.id,
      sources: [edge.sourceNodeId],
      targets: [edge.targetNodeId],
    })),
  });
  const positions = new Map(
    (layout.children ?? []).map((node) => [
      node.id,
      { x: node.x ?? 0, y: node.y ?? 0 },
    ]),
  );

  return {
    nodes: graph.nodes.map((node) => ({
      id: node.id,
      type: "sentiaFlowNode",
      position: positions.get(node.id) ?? { x: 0, y: 0 },
      data: {
        flowNode: node,
        root: graph.rootEntityIds.includes(node.entityId),
        targetProvenance: nodeTargetProvenance(graph, node.id),
      },
    })),
    edges: graph.edges.map((edge) => ({
      id: edge.id,
      source: edge.sourceNodeId,
      target: edge.targetNodeId,
      label: edge.kind.replaceAll("_", " "),
      animated: edge.asynchronous,
      data: { flowEdge: edge },
      markerEnd: { type: MarkerType.ArrowClosed },
      className: `flow-edge flow-edge--${edge.resolution}`,
    })),
  };
}
