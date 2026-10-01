# Flow Maps V1 contract

This document is the normative Phase 0 contract for Sentia Flow Maps. ADR 0004
defines the architecture decision; this document defines the vocabulary,
invariants, traversal behavior, fixtures, and release measurements.

## Product contract

Flow Maps V1 turns one to three AI-selected, indexed root entities into a
bounded, interactive feature-flow graph. The model selects semantic starting
points. Sentia owns entities, relationships, evidence, traversal, grouping, and
rendering metadata.

The underlying value is a directed multigraph. A client may render a tree-like
projection but must preserve shared nodes, cycles, and relationship direction.

## Schema ownership

`FLOW_MAP_SCHEMA_VERSION` is independent from Sentia's transport protocol
version. The sidecar Pydantic models are the service-boundary authority and the
TypeScript package contains the corresponding Zod runtime validators. Phase 0
keeps the existing repository convention of explicit models in both languages.
Both runtimes must parse the same checked-in golden JSON fixtures, and a change
to one side is incomplete until the other validator and fixture tests agree.

Schema generation may replace the explicit mirror later, but Flow Maps V1 does
not depend on adding a generator before indexing work begins.

## Supported V1 behavior

- Python, TypeScript, and JavaScript entities
- Forward feature-flow traversal
- Lazy one-hop callers and callees
- Calls, awaits, containment, imports as metadata, HTTP transitions, webview
  message transitions, and selected event transitions
- File, package, language, and process grouping
- Resolved, inferred, and unresolved relationships
- Exact source navigation from nodes and edges
- Draggable positions, expand/collapse, filters, re-layout, and persisted view
  state

## Entity kinds

The V1 protocol recognizes these kinds:

| Kind              | Meaning                                                 |
| ----------------- | ------------------------------------------------------- |
| `repository`      | Indexed workspace                                       |
| `directory`       | Directory grouping entity                               |
| `package`         | Language/package grouping entity                        |
| `process`         | Runtime boundary such as webview, extension, or sidecar |
| `file`            | Eligible repository file                                |
| `module`          | Language module                                         |
| `class`           | Class declaration                                       |
| `interface`       | TypeScript interface                                    |
| `function`        | Free or nested function                                 |
| `method`          | Class or object method                                  |
| `component`       | React or framework component                            |
| `http_endpoint`   | Synthetic method/path boundary                          |
| `message_channel` | Synthetic discriminant/topic boundary                   |
| `external`        | Referenced implementation outside the indexed workspace |
| `unresolved`      | Explicit unresolved frontier                            |

## Relationship kinds

| Kind                                         | V1 traversal treatment                          |
| -------------------------------------------- | ----------------------------------------------- |
| `contains`                                   | Used for hierarchy and grouping                 |
| `imports`, `exports`                         | Metadata; not followed as execution by default  |
| `calls`, `constructs`, `awaits`              | Followed by feature-flow traversal              |
| `inherits`, `implements`, `decorates`        | Inspectable metadata                            |
| `returns`, `raises`                          | Display/control metadata                        |
| `registers_handler`                          | Followed when it establishes a boundary handler |
| `emits_message`, `handles_message`           | Followed across message boundaries              |
| `sends_http_request`, `handles_http_request` | Followed across HTTP boundaries                 |
| `publishes_event`, `consumes_event`          | Followed for supported event adapters           |

`imports` never means `calls`. `sends_http_request` never means a direct
language-level call to a server handler.

## Resolution values

| Resolution            | Meaning                                                |
| --------------------- | ------------------------------------------------------ |
| `compiler_resolved`   | A compiler/type checker identified the target          |
| `statically_resolved` | A conservative Sentia resolver identified one target   |
| `framework_inferred`  | A named adapter matched evidence-backed framework keys |
| `unresolved`          | No unique target can be proven                         |
| `runtime_observed`    | Reserved for later runtime evidence                    |

## Source-span invariants

A source span contains:

- Workspace-relative POSIX path
- One-based start/end lines
- Zero-based start/end columns
- Lowercase 64-character content hash
- Positive repository revision

The end position may not precede the start position. A graph is stale when its
span hash no longer matches the indexed revision.

## Repository investigation

Repository answers and Flow Maps share one investigation artifact:

```text
RepositoryInvestigation
  schemaVersion
  investigationId
  repositoryRevision
  question
  provider
  selectedFiles[]
  readSpans[]
  evidence[]
  searchQueries[]
  candidateIdentifiers[]
  createdAt
```

`readSpans` records the complete bounded material made available to the model.
`evidence` records the smaller validated citation set. The artifact contains no
hidden reasoning or unrestricted provider payloads.

## Root selection

The AI returns:

```text
FlowRootSelection
  schemaVersion = "1"
  repositoryRevision
  rootEntityIds[1..3]
  viewType = "feature_flow"
  direction = "forward"
  rationale
  unresolvedConcepts[]
```

Sentia rejects unknown entity IDs, wrong revisions, duplicate roots, and more
than three roots. The rationale is user-visible semantic metadata, not evidence
for a relationship. `rootEntityIds` is the V1 protocol name; after validation,
these IDs are treated as semantic anchors and need not remain rendered graph
roots.

Evidence ranges are mapped to candidate roots using the smallest containing
symbol first. Read files, search results, question terms, routes, and message
keys may influence ranking. Selected files alone are never treated as roots.

V1 does not ask the model for a second set of semantic traversal keywords.
Traversal priority is calculated locally from the original question, optional
candidate identifiers already present in the investigation, entity metadata,
relationship kinds, graph distance, and fan-out. Candidate identifiers may be
empty and never change whether a relationship fact is structurally valid.

## Codex-specific feature coverage investigation

Codex Flow Map investigation includes one additional, provider-specific review
after the initial root selection. Evaluation showed that Codex can select a
locally important implementation center while omitting feature-level landmarks
such as its trigger, orchestration boundary, or externally meaningful outcome.
The review improves where deterministic reconstruction starts; it does not
change how graph truth is established.

The provider paths are:

```text
Claude: file selection -> bounded reads -> root selection

Codex:  file selection -> bounded reads -> initial root selection
            -> feature coverage review -> validated merged anchors
```

Claude does not receive the extra model call. The Codex pass runs when the
validated initial selection contains fewer than three anchors and the bounded
coverage catalog contains at least one additional entity. It is skipped when
the existing three-anchor graph limit is already full or no additional
candidate can be supplied.

The Codex SDK root-selection thread is reused for the coverage turn. The thread
already contains the bounded repository read and initial candidate catalog, so
Sentia does not resend or expose the whole repository. The coverage prompt adds:

- the original feature request and file-selection report;
- files already read;
- the initial anchors, rationale, and unresolved concepts;
- at most 120 deterministically selected candidates;
- concise incoming and outgoing relationship summaries, capped at three per
  direction and a 48,000-character catalog budget.

Coverage candidates prioritize initial anchors, their incoming callers and
outgoing callees, useful parents, executable entities in selected files, query
matches, other selected-file entities, and the original bounded root catalog.
`StructuralIndex.incoming_relationships()` derives callers from the canonical
relationship tuple; the coverage pass does not create reverse facts.

Codex returns this strict conceptual shape:

```text
ModelCodexFeatureCoverage
  coverageAssessment
    sufficient: boolean
    reason: string
  additionalAnchorEntityIds[0..2]
  unresolvedConcepts[0..20]
  rationale
```

It may assess relevant conceptual stages such as entry, orchestration, major
operation, asynchronous/framework boundary, persistence/state mutation, and
outcome/notification. Not every feature must contain every stage. The prompt
prefers major runtime landmarks over utilities and framework mechanics, but no
name-based filter is used.

Every additional ID is checked against the exact catalog actually included in
the bounded prompt and against the same-revision `StructuralIndex`. Invented or
out-of-catalog IDs are discarded. Existing IDs are deduplicated, model order is
preserved, no more than two additions are accepted, and the final selection
never exceeds the existing three-anchor limit. A `sufficient=true` assessment
accepts no additions. Accepted unresolved concepts are deduplicated and remain
investigation metadata, not structural evidence.

The pass has a 30-second timeout and is optional. Timeout, SDK failure, empty or
malformed structured output, and a review with no usable IDs all fall back to
the already validated initial selection. A malformed response's token usage is
still counted when the SDK returned usage. The fallback never prevents local
graph construction.

`FlowMapResponse` preserves the existing total `usage` and additionally exposes
`investigationUsage` with `fileSelection`, `initialAnchorSelection`,
`coveragePass`, and `total`. `RepositoryInvestigation` records
`initialAnchorEntityIds` and accepted `coverageAnchorEntityIds`; the adjacent
`FlowRootSelection` contains the final merged IDs. Structured telemetry records
the stage latencies, attempted/sufficient state, requested/accepted/rejected
coverage IDs, final IDs, selected files, and final visible graph entity and edge
IDs. This permits provider and coverage-on/off evaluation without making model
rationale part of graph evidence.

The hard boundary remains:

```text
Codex coverage review -> candidate entity IDs only
StructuralIndex       -> validates that entities exist
RelationshipFact      -> supplies evidence-backed relationships
build_feature_flow()  -> deterministically projects the graph
```

Codex cannot return `FlowEdge` values, its rationale is never converted into a
relationship, and there is no separate Codex graph builder.

## Anchor Reconciliation / Feature Backbone Discovery

Selected anchors are semantic feature landmarks, not automatically independent
rendered graph roots. After either provider has produced its final validated
anchor set—and after the optional Codex coverage merge—Sentia crosses a hard
model boundary:

```text
Requested Feature
      ↓
AI Semantic Investigation
      ↓
Initial Anchor Selection
      ↓
Optional Provider Coverage Investigation
      ↓
Final Validated Anchors
      ↓
==========================
      MODEL BOUNDARY
==========================
      ↓
Deterministic Anchor Reconciliation
      ↓
Candidate Feature Backbone
      ↓
Feature Progression / Expansion
      ↓
Feature Projection
      ↓
FlowGraph
```

`reconcile_anchors()` is provider-independent and has no model, embedding,
semantic-search, or filesystem dependency. Its only inputs are the final
revision-bound `FlowRootSelection`, a matching `StructuralIndex`, and explicit
search budgets. Claude and Codex selections therefore use exactly the same
structural reconstruction.

Reconciliation reuses the index's precomputed incoming and outgoing adjacency.
It inspects direct anchor relationships, bounded directed paths in either
direction, and bounded undirected structural paths while retaining every fact's
actual execution direction. Unresolved facts never become resolved bridges.
Candidate paths are ordered deterministically: directed before weak structural
connections, then shorter paths, stronger resolution, and stable relationship
IDs. Several bounded candidates may be retained; a deterministic minimum
backbone connects each provable anchor component.

Incoming traversal also finds request-local coordinator candidates. A candidate
records which selected anchors it calls directly, which it can reach within the
upstream bound, and the exact `RelationshipFact` path to every reached anchor.
A caller that directly coordinates at least two anchors and reaches an entire
component may become that component's structural graph entry. This is a
conservative projection choice, not a permanent entity classification. Shared
downstream callees are recorded separately and are never mislabeled as upstream
coordinators.

Every result records the repository revision, final anchor IDs, direct anchor
facts, candidate and selected backbone paths, bridge intermediates, coordinator
and shared-callee candidates, connected components, disconnected anchors,
structural entries, expansion exits, visit/candidate counts, and truncation
reasons. Every path contains the original relationship IDs, kinds, resolution,
provenance, direction, and source evidence. Structured Flow Map telemetry emits
these diagnostics without adding unconditional per-path production logs.

Graph construction reserves complete selected backbone paths before optional
outward expansion. Connected downstream anchors no longer seed separate fan-out
expansions; expansion resumes from backbone exits so continuation beyond the
known anchors remains discoverable. A strong common caller can be a structural
entry, while a shared callee leaves both upstream anchors as entries. If a
backbone cannot fit the graph node or edge budget, Sentia falls back to the
validated anchors and emits an explicit warning. If structural evidence cannot
connect anchors, each legitimate component remains separate and no relationship
is invented.

The default reconciliation limits are eight relationship hops, 2,000 visited
entities, three candidate paths per anchor pair, 100 incoming and 100 outgoing
candidates per visited entity, and two upstream levels. Cycles are excluded from
individual candidate paths and all searches terminate at these shared limits.
Depth, fan-in, fan-out, candidate, and visit exhaustion remain visible as
truncation diagnostics.

Bridge intermediates are structurally relevant because they prove anchor
connectivity; they are not automatically visually important or globally marked
as helpers or feature steps. Reconciliation does not solve
`Progression(A -> B | RequestedFeature)`, complete feature discovery, helper
classification, node visibility, or semantic compression. A future projection
may collapse an evidence path visually only if the complete structural proof
remains recoverable.

## Relationship classification foundation

Flow Maps preserve a boundary between structural truth and feature projection.
`CodeEntity` declarations and `RelationshipFact` edges record what the static
index can establish about the repository. A relationship that is structurally
true is not deleted or rewritten merely because a later feature-flow decision
may treat it as implementation detail.

Helper and feature-step are therefore not global `CodeEntity` properties. The
same target can be central to one requested feature and incidental to another.
The intended future unit of evaluation is contextual:

```text
Progression(A -> B | RequestedFeature)
```

That progression classifier and its projection rules are not implemented yet.
The first implemented relationship-level signal is **Target Provenance**, which
classifies who owns the target of each revision-bound relationship fact:

| Target provenance   | Deterministic meaning |
| ------------------- | --------------------- |
| `project_owned`     | The target resolves to an indexed entity in the repository revision |
| `dependency_owned`  | An explicit absolute import binds the target to a non-project, non-standard-library module |
| `standard_library`  | An explicit import binds the target to the Python runtime standard library |
| `unresolved`        | The available structural evidence cannot establish ownership safely |

`project_owned` is positive evidence for future progression analysis, but it
does not make a function feature-critical: formatters, log helpers, and thin
wrappers may all be project-owned. Conversely, `dependency_owned` does not mean
helper or implementation detail: a payment provider, task queue, database, or
workflow runtime may be central to a feature. Standard-library provenance is
also evidence rather than an absolute visibility rule.

The Python extractor in `python_relationships.py` retains explicit import
binding evidence on unresolved external call facts. It does not import or
execute analyzed code. The dedicated classifier and signal models live in
`relationship_signals.py`. `StructuralIndex` computes and stores one
`RelationshipSignals` value per stable relationship ID after validating all
entities and facts against a single repository revision. Resolved targets in
that index are project-owned. Explicit absolute imports are compared with the
indexed project module roots and Python's fixed standard-library module set;
relative imports, project-shadowed imports, dynamic receivers, and malformed or
missing evidence stay unresolved.

Projected `FlowEdge.metadata.relationshipSignals` exposes the signal and its
classification basis for diagnostics and future classifiers. Target provenance
is not read by traversal, neighbor ranking, node budgeting, or visibility. It
therefore does not currently hide, promote, compact, or otherwise change any
node. Future deterministic relationship signals can be added to the same
revision-bound model without scattering feature semantics through
`build_feature_flow()` or contaminating the language-level structural facts.

This is intentionally only the first foundational slice of the larger
feature-progression architecture; feature relevance, semantic effects, data and
control dependencies, boundary effects, scoring, and learned ranking are not
part of this implementation.

## Flow Graph

The shared `SentiaFlowGraph` transport contains:

- Graph schema version and map ID
- Repository revision and root entity IDs
- V1 view type
- Source-backed nodes
- Evidence-backed directed edges
- Lazy expansion frontiers
- Explicit warnings
- Generation timestamp

Every edge references existing nodes and carries at least one evidence span.
Every root entity must be represented by a node. Parent node references must
resolve within the same graph. `FlowRootSelection.rootEntityIds` records the
model-selected anchors; `SentiaFlowGraph.rootEntityIds` records the reconciled
structural entries used by the rendered graph. They are equal when reconciliation
does not establish a better entry.

The transport deliberately excludes coordinates. Structural topology and user
layout state have different lifecycles.

## V1 traversal profile

The initial `feature_flow` profile:

1. Starts from one to three final validated semantic anchors.
2. Reconciles bounded, evidence-backed paths and coordinators between anchors.
3. Reserves complete connecting paths before spending budget on optional branches.
4. Traverses forward over calls, constructs, awaits, supported framework
   transitions, and supported event transitions.
5. Adds containment ancestors required for grouping.
6. Does not traverse imports as execution.
7. Stops at five levels or fifty initially visible nodes by default.
8. Preserves cycles and shared callees rather than duplicating structural facts.
9. Aggregates repeated display edges while retaining all call-site evidence.
10. Emits frontiers with hidden-neighbor counts when a budget stops traversal.
11. Emits unresolved nodes when a call or boundary has no unique target.

Within those bounds, Sentia prioritizes execution and framework relationships,
then uses deterministic identifier overlap to choose among peer branches. It
splits snake_case and camelCase names locally and applies no embeddings or
additional model call. A branch stops when it has no traversable outgoing fact,
reaches an unresolved or external terminal, revisits a visible entity, or hits
a depth, node, edge, or per-node branch budget. Budgeted neighbors remain
available through explicit frontiers rather than being discarded, while edge
truncation is labeled with an explicit graph warning.

Anchor reconciliation uses only resolved facts already present in the
revision-bound structural index. It prefers directed paths and may retain a weak
structural connector only with an explicit warning that the connection is not a
single directed execution sequence. A backbone path is added as a whole or not
at all. When selected anchors cannot be connected, the graph keeps every
component and emits a deterministic explanation instead of inventing a
relationship.

The displayed summary is derived from the projected graph and its warnings.
The provider rationale explains why entities were selected as anchors; it is
not relationship evidence and must not claim that a complete or end-to-end
flow was reconstructed.

The API will support bounded one-hop caller or callee expansion without asking
the AI to select every newly displayed node.

## Sidecar generation endpoint

`POST /api/v1/repository/flow-maps` runs the first backend vertical slice. Its
request contains `workspacePath`, `question`, `provider`, and optional
`maxDepth`, `maxNodes`, `maxEdges`, and `maxBranchesPerNode` traversal budgets.

The sidecar loads one revision-bound manifest and structural index, performs a
bounded provider file selection and source read, asks the provider only for one
to three catalogued root entity IDs, validates those IDs, and projects the
graph locally. The response contains the `RepositoryInvestigation`, validated
`FlowRootSelection`, `SentiaFlowGraph`, provider model, and combined token
usage. No semantic-keyword model call is part of this endpoint.

The extension exposes this only through explicit conversational intent. A
sidebar question such as “Create a flow map for repository question handling”
opens a dedicated VS Code editor webview tab and bypasses ordinary answer
generation. Questions that merely discuss a `FlowMap` type or ask what a flow
map is do not trigger generation. There is no Command Palette action.

The tab uses ELK for deterministic layered layout and React Flow for pan/zoom,
selection, a minimap, source inspection, and source navigation. The Sentia
sidebar remains the conversational launcher rather than the graph host.

Lazy frontier expansion, saved layouts, filters, and the TypeScript/JavaScript
indexers remain follow-up work.

## Framework adapter invariant

An adapter emits synthetic entities and relationship facts. Every inferred fact
must include:

- Adapter/extractor name and version
- Stable rule identifier
- Source-side evidence
- Normalized matching key
- Resolution or ambiguity state

The first adapters are:

- TypeScript HTTP request to FastAPI route
- React/webview `postMessage` discriminant to VS Code extension handler
- Supported sidecar event publication to event consumer

Dynamic keys remain unresolved. Selecting roots on both sides does not prove a
relationship between them.

## Python relationship extraction

Python indexing runs in two passes over bounded, eligible source text. The
first pass creates all file, module, class, function, and method entities for a
repository revision. The second pass resolves calls against that complete
entity set, so forward declarations and explicit imports can be handled without
executing repository code.

The initial conservative resolver supports direct lexical calls, nested
functions, constructors, awaited calls, `self`/`cls` methods, explicit imported
symbols, imported-module aliases, and relative imports. It emits exact `ast.Call`
evidence with named extractor rules. Parameters, duplicate declarations,
dynamic receivers, computed callables, missing modules, and ambiguous targets
remain explicit unresolved relationship facts. An awaited invocation emits one
`awaits` fact rather than duplicate `calls` and `awaits` facts.

## Scope boundary

Repository-specific evaluation scenarios are not part of the Flow Maps V1
contract. They may reveal generic accuracy opportunities, but application
features, framework libraries, and runtime patterns found in an evaluated
repository do not become Sentia requirements.

Any future framework adapter or projection mode requires its own generic
product contract, evidence rules, fixtures, and release criteria before it is
added here. Flow Maps must not contain behavior specialized to a repository
used for evaluation.

## Golden fixtures

Phase 0 establishes these repository-independent fixtures:

1. `tests/fixtures/flow-maps/login-cross-language` demonstrates a React login
   handler crossing `POST /api/session` into a Python FastAPI route.
2. `tests/fixtures/flow-maps/contracts/valid-flow-graph.json` is parsed by both
   Pydantic and Zod contract tests.
3. `tests/fixtures/flow-maps/sentia-repository-question.expected.json` records
   stable symbol anchors and boundary keys for Sentia's own first vertical
   slice. It avoids line-number assertions so normal edits do not invalidate the
   Phase 0 intent fixture.

Future indexer phases will turn these intent fixtures into generated graph
goldens.

## Security requirements

- Never execute repository imports, build scripts, compiler plugins, or custom
  transformers while indexing.
- Apply current ignore, dependency, symlink, binary, sensitive-name, and file
  size rules before parsing.
- Bound parser time, memory, nesting, file count, graph depth, and graph size.
- Treat identifiers, comments, signatures, and string literals as untrusted
  repository data.
- Do not persist unrestricted provider traces or hidden reasoning.
- Return partial, labeled results when one parser or adapter fails.

## Evaluation and release measurements

Phase 0 defines the measurements; representative live-provider baselines are
recorded when credentials and evaluation repositories are available.

| Measurement               | Definition                                                                |
| ------------------------- | ------------------------------------------------------------------------- |
| Root recall@1/@3          | Expected starting entity appears in the first one/three roots             |
| Structural edge precision | Displayed resolved edge is present in fixture truth                       |
| Adapter precision/recall  | Expected boundary matches are correct and complete                        |
| Evidence validity         | Path, range, hash, and revision validate at display time                  |
| Topology determinism      | Same roots, revision, question terms, and budgets give identical topology |
| Cold index latency        | Attach through structurally ready revision                                |
| Incremental latency       | File change through new structurally ready revision                       |
| Root-to-graph latency     | Validated roots through serialized graph                                  |
| Layout/render latency     | Flow Graph receipt through interactive canvas                             |
| Investigation reuse       | Follow-up map avoids an unnecessary source-reading pass                   |
| Token comparison          | Map discovery tokens versus direct source-to-Flow-JSON baseline           |

Performance thresholds will be ratified from fixture and representative
repository measurements instead of being guessed in the schema contract.

## Explicit V1 non-goals

- Runtime traces or guaranteed execution order
- Full control-flow graphs
- ERD and database data-flow views
- Embeddings
- Raw AST transport
- Model-authored verified edges
- General-purpose framework inference
- Languages beyond Python, TypeScript, and JavaScript
- Whole repositories rendered at function granularity
