import { useEffect, useMemo, useState } from "react";

import {
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  type EdgeMouseHandler,
  type NodeMouseHandler,
  type NodeProps,
  type ReactFlowInstance,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import type {
  ExtensionToFlowMapPanelMessage,
  FlowEdge,
  FlowMapPanelToExtensionMessage,
  FlowMapResponse,
  FlowNode,
  FlowSourceSpan,
  FeatureTrace,
  TraceStage,
  TraceTransition,
} from "@sentia/protocol";

import {
  layoutFlowGraph,
  type FlowCanvasEdge,
  type FlowCanvasNode,
} from "./flowMapLayout";
import { flowMapEvidenceSummary } from "./flowMapEvidence";
import {
  edgeTargetProvenance,
  nodeTargetProvenance,
  targetProvenanceBadge,
  targetProvenanceLabel,
  type TargetProvenanceSignal,
} from "./flowMapProvenance";

interface FlowMapAppProps {
  message: ExtensionToFlowMapPanelMessage | null;
  post: (message: FlowMapPanelToExtensionMessage) => void;
}

type Selection =
  { kind: "node"; value: FlowNode } | { kind: "edge"; value: FlowEdge } | null;

const nodeTypes = { sentiaFlowNode: SentiaFlowNode };

export function FlowMapApp({ message, post }: FlowMapAppProps) {
  if (!message || message.type === "flow_map.loading") {
    return <FlowMapLoading question={message?.question} />;
  }
  if (message.type === "flow_map.error") {
    return (
      <main className="flow-map-state">
        <div className="flow-map-state__mark">!</div>
        <h1>Flow Map needs attention</h1>
        <p>{message.error}</p>
        <button onClick={() => post({ type: "flow_map.retry" })} type="button">
          Try again
        </button>
      </main>
    );
  }
  return (
    <FlowMapResult
      key={message.payload.featureTrace?.traceId ?? message.payload.graph.mapId}
      post={post}
      response={message.payload}
    />
  );
}

function FlowMapResult({
  post,
  response,
}: {
  post: (message: FlowMapPanelToExtensionMessage) => void;
  response: FlowMapResponse;
}) {
  const [view, setView] = useState<"trace" | "code">(
    response.featureTrace ? "trace" : "code",
  );
  return (
    <div className="flow-map-result">
      <nav className="flow-map-view-switch" aria-label="Map view and export">
        {response.featureTrace ? (
          <>
            <button
              type="button"
              aria-pressed={view === "trace"}
              onClick={() => setView("trace")}
            >
              Feature trace
            </button>
            <button
              type="button"
              aria-pressed={view === "code"}
              onClick={() => setView("code")}
            >
              Code evidence
            </button>
          </>
        ) : null}
        <button
          type="button"
          className="flow-map-copy"
          onClick={() =>
            post({ type: "flow_map.export", destination: "clipboard" })
          }
          title="Copy both maps, source evidence, agent, model, and reported token usage"
        >
          Copy JSON
        </button>
        <button
          type="button"
          onClick={() => post({ type: "flow_map.export", destination: "file" })}
          title="Save both maps, source evidence, agent, model, and reported token usage"
        >
          Export JSON
        </button>
      </nav>
      {view === "trace" && response.featureTrace ? (
        <FeatureTraceView
          post={post}
          response={response}
          trace={response.featureTrace}
        />
      ) : (
        <FlowMapCanvas post={post} response={response} />
      )}
    </div>
  );
}

function FeatureTraceView({
  post,
  response,
  trace,
}: {
  post: (message: FlowMapPanelToExtensionMessage) => void;
  response: FlowMapResponse;
  trace: FeatureTrace;
}) {
  const [selection, setSelection] = useState<
    | { kind: "stage"; value: TraceStage }
    | { kind: "transition"; value: TraceTransition }
    | null
  >(null);
  const stages = trace.stages.filter(
    (stage) => stage.reviewStatus === "source_reviewed",
  );
  const stageIds = new Set(stages.map((stage) => stage.id));
  const transitions = trace.transitions.filter(
    (transition) =>
      stageIds.has(transition.sourceStageId) &&
      stageIds.has(transition.targetStageId),
  );
  const stageLabels = new Map(stages.map((stage) => [stage.id, stage.label]));
  const transitionLabel = (transition: TraceTransition) =>
    `${stageLabels.get(transition.sourceStageId) ?? transition.sourceStageId} → ${stageLabels.get(transition.targetStageId) ?? transition.targetStageId}`;
  const stopLabels: Record<FeatureTrace["stopReason"], string> = {
    coverage_satisfied:
      "Requested stages are supported by the inspected source.",
    budget_exhausted:
      "The investigation reached its budget with gaps remaining.",
    no_progress:
      "The investigation could not establish more connections from the available evidence.",
    review_failed: "The final coverage review could not be completed.",
  };
  const spans = selection?.value.evidence ?? [];
  return (
    <main className="flow-map-shell feature-trace-shell">
      <header className="flow-map-header">
        <div>
          <span className="flow-map-eyebrow">
            Feature trace ·{" "}
            {trace.status === "supported" ? "Source supported" : "Partial"}
          </span>
          <h1>{response.investigation.question}</h1>
          <p>{trace.requestedOutcome}</p>
        </div>
        <div className="flow-map-stats" aria-label="Feature trace statistics">
          <span>{stages.length} stages</span>
          <span>{transitions.length} connections</span>
          <span>{trace.gaps.length} gaps</span>
        </div>
      </header>
      <section className="flow-map-workspace">
        <div className="feature-trace-content">
          {trace.featureSpecification ? (
            <section aria-label="Requested behavior">
              <h2>Requested behavior</h2>
              <p>{trace.featureSpecification.behavior}</p>
              <dl>
                <dt>Actor</dt>
                <dd>{trace.featureSpecification.actor}</dd>
                <dt>Input</dt>
                <dd>{trace.featureSpecification.input}</dd>
                <dt>Outcome</dt>
                <dd>{trace.featureSpecification.outcome}</dd>
              </dl>
              <p className="flow-map-muted">
                Interpretation of your request; source review determines what is
                implemented.
              </p>
            </section>
          ) : null}
          <section className="feature-trace-stack" aria-label="Detected stack">
            <span className="flow-map-eyebrow">Detected stack</span>
            <div className="feature-trace-stack__items">
              {trace.stackProfile.technologies.map((technology) => (
                <span
                  key={`${technology.category}:${technology.name}:${technology.status}`}
                  title={`${technology.status === "observed" ? "Observed in source" : "Declared dependency"}. ${technology.adapterSupport === "partial" ? "Some framework patterns can be traced." : "Framework tracing is not supported yet."}`}
                >
                  {technology.name} <small>{technology.status}</small>
                </span>
              ))}
              {trace.stackProfile.technologies.length === 0 ? (
                <p>No framework was identified in the indexed files.</p>
              ) : null}
            </div>
          </section>
          <section aria-label="Trace stages">
            <h2>Stages</h2>
            <p className="flow-map-muted">
              Select a stage to inspect its source. Connections are listed
              separately below.
            </p>
            <div className="feature-trace-stages">
              {stages.map((stage) => (
                <button
                  type="button"
                  key={stage.id}
                  className="feature-trace-stage"
                  aria-pressed={
                    selection?.kind === "stage" &&
                    selection.value.id === stage.id
                  }
                  onClick={() => setSelection({ kind: "stage", value: stage })}
                >
                  <span className="flow-map-eyebrow">{stage.role}</span>
                  <strong>{stage.label}</strong>
                  <span>{stage.inclusionReason}</span>
                  <span className="feature-trace-stage__source">
                    {stage.evidence[0]?.path}
                  </span>
                </button>
              ))}
            </div>
            {stages.length === 0 ? (
              <p>No stages have been established yet.</p>
            ) : null}
          </section>
          <section aria-label="Trace connections">
            <h2>Connections</h2>
            <div className="feature-trace-connections">
              {transitions.map((transition) => (
                <button
                  type="button"
                  key={transition.id}
                  aria-pressed={
                    selection?.kind === "transition" &&
                    selection.value.id === transition.id
                  }
                  onClick={() =>
                    setSelection({ kind: "transition", value: transition })
                  }
                >
                  <span>{transitionLabel(transition)}</span>
                  <small>
                    {transition.kind === "registration"
                      ? "Registers"
                      : transition.kind === "execution"
                        ? "Execution"
                        : "Code relationship"}
                    {transition.viaEntityIds.length > 0
                      ? ` · via ${String(transition.viaEntityIds.length)} intermediate functions`
                      : ""}
                    {transition.asynchronous ? " · async boundary" : ""}
                    {transition.conditional ? " · conditional" : ""}
                  </small>
                </button>
              ))}
            </div>
            {transitions.length === 0 ? (
              <p>No connection between these stages has been established.</p>
            ) : null}
          </section>
          <details className="feature-trace-candidates">
            <summary>
              Investigation details · {trace.candidates.length} candidates
            </summary>
            <p>
              Supporting helpers and competing implementations. Unverified
              proposals are not established feature stages.
            </p>
            {trace.featureSpecification?.alternatives.length ? (
              <p>
                Alternatives considered:{" "}
                {trace.featureSpecification.alternatives.join("; ")}
              </p>
            ) : null}
            {trace.candidates.map((candidate) => (
              <details key={candidate.entityId}>
                <summary>
                  {candidate.name} ·{" "}
                  {candidate.sourceReviewed ? candidate.decision : "unverified"}
                </summary>
                <p>
                  {candidate.sourceReviewed
                    ? candidate.responsibility
                    : `Proposed responsibility: ${candidate.responsibility}`}
                </p>
                <p>{candidate.reason}</p>
                {candidate.question ? (
                  <p>Unresolved: {candidate.question}</p>
                ) : null}
                <SourceEvidence spans={candidate.evidence} post={post} />
              </details>
            ))}
            {trace.stages
              .filter((stage) => stage.reviewStatus !== "source_reviewed")
              .map((stage) => (
                <details key={stage.id}>
                  <summary>Unverified proposal: {stage.label}</summary>
                  <SourceEvidence spans={stage.evidence} post={post} />
                </details>
              ))}
          </details>
          {trace.gaps.length > 0 ? (
            <section
              className="feature-trace-gaps"
              aria-label="Unresolved gaps"
            >
              <h2>Still unresolved</h2>
              <ul>
                {trace.gaps.map((gap, index) => (
                  <li key={`${gap.code}:${String(index)}`}>{gap.message}</li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
        <aside className="flow-map-inspector">
          <span className="flow-map-eyebrow">
            {selection ? `${selection.kind} evidence` : "Investigation"}
          </span>
          <h2>
            {selection?.kind === "stage"
              ? selection.value.label
              : selection?.kind === "transition"
                ? transitionLabel(selection.value)
                : "What this trace establishes"}
          </h2>
          {selection?.kind === "transition" ? (
            <p>
              {selection.value.kind === "registration"
                ? "This source registers a handler or workflow function. Registration alone does not prove execution."
                : selection.value.kind === "structural"
                  ? "This is a source relationship. It does not establish execution order or data movement."
                  : "The indexed source supports this execution connection. This is not a recorded runtime execution."}
            </p>
          ) : !selection ? (
            <p>{stopLabels[trace.stopReason]}</p>
          ) : null}
          {selection ? <SourceEvidence spans={spans} post={post} /> : null}
          {selection?.kind === "stage" ? (
            <p>
              {selection.value.inclusionReason} Behavioral relevance was
              reviewed by the model against source; it is not runtime
              verification.
            </p>
          ) : null}
          {selection?.kind === "transition" &&
          selection.value.viaNames.length > 0 ? (
            <details>
              <summary>Intermediate functions</summary>
              <ol>
                {selection.value.viaNames.map((name, i) => (
                  <li key={`${name}:${String(i)}`}>{name}</li>
                ))}
              </ol>
              <p>
                The connection follows this source path; it is not a direct
                call.
              </p>
            </details>
          ) : null}
          <dl>
            <div>
              <dt>Revision</dt>
              <dd>{trace.repositoryRevision}</dd>
            </div>
            <div>
              <dt>Files read</dt>
              <dd>{response.investigation.selectedFiles.length}</dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd>{response.model}</dd>
            </div>
          </dl>
          <TraceTokenUsage trace={trace} />
          <details>
            <summary>Stack detection details</summary>
            <p>
              {trace.stackProfile.languages.join(", ") ||
                "No language identified"}
            </p>
            {trace.stackProfile.technologies.map((technology) => (
              <div
                key={`${technology.category}:${technology.name}:${technology.status}`}
              >
                <p>
                  <strong>{technology.name}</strong> · {technology.status}
                  <br />
                  {technology.adapterSupport === "partial"
                    ? "Some framework patterns are supported."
                    : "Framework tracing is not supported yet."}
                </p>
                <ul>
                  {technology.paths.map((path) => (
                    <li key={path}>{path}</li>
                  ))}
                </ul>
              </div>
            ))}
            {trace.stackProfile.limitations.map((limitation, index) => (
              <p key={index}>{limitation}</p>
            ))}
          </details>
          {trace.limitations.length > 0 ? (
            <details open>
              <summary>Trace limitations</summary>
              <ul>
                {trace.limitations.map((limitation, index) => (
                  <li key={index}>{limitation}</li>
                ))}
              </ul>
            </details>
          ) : null}
        </aside>
      </section>
    </main>
  );
}

function SourceEvidence({
  spans,
  post,
}: {
  spans: FlowSourceSpan[];
  post: (message: FlowMapPanelToExtensionMessage) => void;
}) {
  return (
    <div className="flow-map-sources">
      {spans.map((span, index) => (
        <button
          key={`${span.path}:${String(span.startLine)}:${String(index)}`}
          type="button"
          onClick={() => post({ type: "flow_map.open_source", span })}
        >
          <strong>{span.path}</strong>
          <span>
            lines {span.startLine}–{span.endLine}
          </span>
        </button>
      ))}
    </div>
  );
}

function TraceTokenUsage({ trace }: { trace: FeatureTrace }) {
  const stages = ["planning", "investigation", "review"] as const;
  return (
    <details open className="feature-trace-usage">
      <summary>LLM tokens by stage</summary>
      <table>
        <thead>
          <tr>
            <th>Stage</th>
            <th>Input</th>
            <th>Output</th>
          </tr>
        </thead>
        <tbody>
          {stages.map((stage) => {
            const usage = trace.usage.filter((item) => item.stage === stage);
            return (
              <tr key={stage}>
                <th>{stage}</th>
                <td>
                  {usage
                    .reduce((sum, item) => sum + item.inputTokens, 0)
                    .toLocaleString()}
                </td>
                <td>
                  {usage
                    .reduce((sum, item) => sum + item.outputTokens, 0)
                    .toLocaleString()}
                </td>
              </tr>
            );
          })}
          <tr>
            <th>Total</th>
            <td>
              {trace.usage
                .reduce((sum, item) => sum + item.inputTokens, 0)
                .toLocaleString()}
            </td>
            <td>
              {trace.usage
                .reduce((sum, item) => sum + item.outputTokens, 0)
                .toLocaleString()}
            </td>
          </tr>
        </tbody>
      </table>
      <p className="flow-map-muted">
        Stack detection and index traversal run locally.
      </p>
    </details>
  );
}

function FlowMapCanvas({
  post,
  response,
}: {
  post: (message: FlowMapPanelToExtensionMessage) => void;
  response: FlowMapResponse;
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowCanvasNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<FlowCanvasEdge>([]);
  const [flow, setFlow] = useState<
    ReactFlowInstance<FlowCanvasNode, FlowCanvasEdge> | undefined
  >();
  const [selection, setSelection] = useState<Selection>(null);
  const [layoutError, setLayoutError] = useState<string | null>(null);
  const graph = response.graph;
  const evidenceSummary = flowMapEvidenceSummary(graph);

  useEffect(() => {
    let active = true;
    setLayoutError(null);
    void layoutFlowGraph(graph)
      .then((layout) => {
        if (!active) {
          return;
        }
        setNodes(layout.nodes);
        setEdges(layout.edges);
      })
      .catch((error: unknown) => {
        if (active) {
          setLayoutError(
            error instanceof Error ? error.message : String(error),
          );
        }
      });
    return () => {
      active = false;
    };
  }, [graph, setEdges, setNodes]);

  useEffect(() => {
    if (!flow || nodes.length === 0) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      void flow.fitView({ padding: 0.18 });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [flow, nodes.length]);

  const onNodeClick: NodeMouseHandler<FlowCanvasNode> = (_, node) => {
    setSelection({ kind: "node", value: node.data.flowNode });
  };
  const onNodeDoubleClick: NodeMouseHandler<FlowCanvasNode> = (_, node) => {
    const span = node.data.flowNode.sourceSpans[0];
    if (span) {
      post({ type: "flow_map.open_source", span });
    }
  };
  const onEdgeClick: EdgeMouseHandler<FlowCanvasEdge> = (_, edge) => {
    const flowEdge = edge.data?.flowEdge;
    if (flowEdge) {
      setSelection({ kind: "edge", value: flowEdge });
    }
  };

  return (
    <main className="flow-map-shell">
      <header className="flow-map-header">
        <div>
          <span className="flow-map-eyebrow">Feature flow</span>
          <h1>{response.investigation.question}</h1>
          <p>{evidenceSummary}</p>
        </div>
        <div className="flow-map-stats" aria-label="Flow Map statistics">
          <span>{graph.nodes.length} nodes</span>
          <span>{graph.edges.length} edges</span>
          <span>{graph.frontiers.length} frontiers</span>
          <span>{response.model}</span>
        </div>
      </header>

      {graph.warnings.length > 0 ? (
        <div className="flow-map-warnings" role="status">
          {graph.warnings.map((warning) => (
            <span key={warning.code}>{warning.message}</span>
          ))}
        </div>
      ) : null}

      <section className="flow-map-workspace">
        <div className="flow-map-canvas">
          {layoutError ? (
            <div className="flow-map-layout-error">
              Layout failed: {layoutError}
            </div>
          ) : null}
          <ReactFlow<FlowCanvasNode, FlowCanvasEdge>
            edges={edges}
            fitView
            fitViewOptions={{ padding: 0.18 }}
            minZoom={0.1}
            nodeTypes={nodeTypes}
            nodes={nodes}
            onEdgesChange={onEdgesChange}
            onEdgeClick={onEdgeClick}
            onNodeClick={onNodeClick}
            onNodeDoubleClick={onNodeDoubleClick}
            onNodesChange={onNodesChange}
            onInit={setFlow}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={20} size={1} />
            <MiniMap
              nodeColor={(node) =>
                node.data.root
                  ? "var(--vscode-button-background)"
                  : "var(--vscode-descriptionForeground)"
              }
              pannable
              zoomable
            />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
        <FlowMapInspector
          evidenceSummary={evidenceSummary}
          post={post}
          response={response}
          selection={selection}
        />
      </section>
    </main>
  );
}

function SentiaFlowNode({ data, selected }: NodeProps<FlowCanvasNode>) {
  const node = data.flowNode;
  const span = node.sourceSpans[0];
  const provenance = data.targetProvenance[0];
  return (
    <article
      className={`flow-node${data.root ? " flow-node--root" : ""}${selected ? " flow-node--selected" : ""}`}
    >
      <Handle position={Position.Left} type="target" />
      <div className="flow-node__heading">
        <span>{node.kind.replaceAll("_", " ")}</span>
        <div className="flow-node__badges">
          {provenance ? (
            <span
              className={`flow-node__provenance flow-node__provenance--${provenance.targetProvenance}`}
              title={`Target provenance: ${targetProvenanceLabel(provenance.targetProvenance)}`}
            >
              {targetProvenanceBadge(provenance.targetProvenance)}
            </span>
          ) : null}
          {data.root ? <strong>Anchor</strong> : null}
        </div>
      </div>
      <div className="flow-node__label">{node.label}</div>
      <div className="flow-node__location">
        {span ? `${span.path}:${String(span.startLine)}` : "No source location"}
      </div>
      {node.hiddenNeighborCount > 0 ? (
        <span className="flow-node__frontier">
          +{node.hiddenNeighborCount} hidden
        </span>
      ) : null}
      <Handle position={Position.Right} type="source" />
    </article>
  );
}

function FlowMapInspector({
  evidenceSummary,
  post,
  response,
  selection,
}: {
  evidenceSummary: string;
  post: (message: FlowMapPanelToExtensionMessage) => void;
  response: FlowMapResponse;
  selection: Selection;
}) {
  const spans: FlowSourceSpan[] = useMemo(() => {
    if (!selection) {
      return response.investigation.evidence;
    }
    return selection.kind === "node"
      ? selection.value.sourceSpans
      : selection.value.evidence;
  }, [response, selection]);
  const targetProvenance = useMemo(() => {
    if (!selection) {
      return [];
    }
    return selection.kind === "edge"
      ? edgeTargetProvenance(selection.value)
      : nodeTargetProvenance(response.graph, selection.value.id);
  }, [response.graph, selection]);

  return (
    <aside className="flow-map-inspector">
      <span className="flow-map-eyebrow">
        {selection ? `${selection.kind} details` : "Investigation"}
      </span>
      {selection?.kind === "node" ? (
        <>
          <h2>{selection.value.label}</h2>
          <p>{selection.value.qualifiedName ?? selection.value.kind}</p>
          <dl>
            <div>
              <dt>Kind</dt>
              <dd>{selection.value.kind.replaceAll("_", " ")}</dd>
            </div>
            <div>
              <dt>Expandable</dt>
              <dd>{selection.value.expandable ? "Yes" : "No"}</dd>
            </div>
            <TargetProvenanceDetails signals={targetProvenance} />
          </dl>
        </>
      ) : selection?.kind === "edge" ? (
        <>
          <h2>{selection.value.kind.replaceAll("_", " ")}</h2>
          <p>{selection.value.label ?? "Evidence-backed relationship"}</p>
          <dl>
            <div>
              <dt>Resolution</dt>
              <dd>{selection.value.resolution.replaceAll("_", " ")}</dd>
            </div>
            <TargetProvenanceDetails signals={targetProvenance} />
            <div>
              <dt>Extractor</dt>
              <dd>{selection.value.provenance.extractor}</dd>
            </div>
            <div>
              <dt>Rule</dt>
              <dd>{selection.value.provenance.ruleId}</dd>
            </div>
          </dl>
        </>
      ) : (
        <>
          <h2>What the displayed graph proves</h2>
          <p>{evidenceSummary}</p>
          <dl>
            <div>
              <dt>Revision</dt>
              <dd>{response.graph.repositoryRevision}</dd>
            </div>
            <div>
              <dt>Files read</dt>
              <dd>{response.investigation.selectedFiles.length}</dd>
            </div>
            <div>
              <dt>Tokens</dt>
              <dd>
                {response.usage.inputTokens.toLocaleString()} in ·{" "}
                {response.usage.outputTokens.toLocaleString()} out
              </dd>
            </div>
          </dl>
          <details>
            <summary>Anchor selection rationale</summary>
            <p>{response.rootSelection.rationale}</p>
            <p>
              This model-supplied rationale explains anchor relevance; it is not
              evidence that the anchors are connected.
            </p>
          </details>
        </>
      )}

      {spans.length > 0 ? (
        <div className="flow-map-sources">
          <span className="flow-map-eyebrow">Source evidence</span>
          {spans.map((span) => (
            <button
              key={`${span.path}:${String(span.startLine)}:${String(span.startColumn)}:${String(span.endLine)}`}
              onClick={() => post({ type: "flow_map.open_source", span })}
              type="button"
            >
              <strong>{span.path}</strong>
              <span>
                lines {span.startLine}–{span.endLine}
              </span>
            </button>
          ))}
        </div>
      ) : (
        <p className="flow-map-muted">
          No source span is attached to this item.
        </p>
      )}

      <details>
        <summary>Files investigated</summary>
        <ul>
          {response.investigation.selectedFiles.map((path) => (
            <li key={path}>{path}</li>
          ))}
        </ul>
      </details>
      <p className="flow-map-hint">
        Select a node or edge for evidence. Double-click a node to open its
        source.
      </p>
    </aside>
  );
}

function TargetProvenanceDetails({
  signals,
}: {
  signals: TargetProvenanceSignal[];
}) {
  if (signals.length === 0) {
    return (
      <div>
        <dt>Target provenance</dt>
        <dd>Not available</dd>
      </div>
    );
  }

  return (
    <>
      <div>
        <dt>Target provenance</dt>
        <dd>
          {signals
            .map((signal) => targetProvenanceLabel(signal.targetProvenance))
            .join(", ")}
        </dd>
      </div>
      <div>
        <dt>Basis</dt>
        <dd>
          {signals
            .map((signal) => signal.targetProvenanceBasis.replaceAll("_", " "))
            .join(", ")}
        </dd>
      </div>
      {signals.some((signal) => signal.targetModule) ? (
        <div>
          <dt>Target module</dt>
          <dd>
            {signals
              .map((signal) => signal.targetModule)
              .filter((module): module is string => Boolean(module))
              .join(", ")}
          </dd>
        </div>
      ) : null}
    </>
  );
}

function FlowMapLoading({ question }: { question?: string }) {
  return (
    <main className="flow-map-state">
      <div className="flow-map-loader" aria-hidden="true" />
      <span className="flow-map-eyebrow">Building feature flow</span>
      <h1>{question ?? "Investigating the repository…"}</h1>
      <p>
        Sentia is inspecting the stack, finding relevant source, and checking
        the connections needed to explain this feature.
      </p>
    </main>
  );
}
