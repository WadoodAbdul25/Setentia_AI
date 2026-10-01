# Feature Trace Engine — V2 Architecture

Status: First working slice plus feature interpretation and relevance filtering implemented.
The design below also includes follow-up
capabilities; the table at the end distinguishes them.

## Purpose

Explain how a feature moves from input to outcome, with source evidence for each
connection. Support both a single feature and a backend overview made of several
feature traces.

For example, a PRD trace should distinguish creating empty sections from generating
and saving their content. Finding related symbols is only the starting point.

## Components

The Feature Trace Engine coordinates the following components. Its workflow is
shared across providers; Codex and Claude supply model decisions through the same
contract.

| Component | Responsibility | Runs in |
| --- | --- | --- |
| **Stack Profiler** | Detect languages, application types, frameworks, databases, workers, and event systems per application or subsystem. | Sentia |
| **Repository Index** | Store symbols, structural relationships, source spans, and file hashes. Update affected facts when code changes. | Sentia |
| **Framework Adapters** | Extract HTTP handlers, task dispatch, workflow registration, persistence, and event connections using framework rules. | Sentia |
| **Hybrid Retrieval** | Combine exact/lexical search with semantic search to find relevant symbols and source ranges. | Sentia + embedding model |
| **Feature Planner** | Interpret the request, identify inputs and expected outcomes, select candidate anchors, and organize feature stages. | LLM, guided by Sentia |
| **Trace Resolver** | Follow structural relationships and supported data mappings; assemble evidence paths between stages. | Sentia |
| **Coverage Reviewer** | Check missing stages and transitions, request further investigation, and determine whether the requested outcome is explained. | Sentia + LLM |
| **Trace Store and Presenter** | Cache traces and their dependencies; display compact stages with expandable implementation evidence. | Sentia |

### Stack detection

The Stack Profiler runs before anchor selection. It reads dependency manifests,
imports, configuration, and registrations without executing repository code.

It distinguishes a dependency that is **declared**, **used in source**, and
**involved in the requested feature**. It also records whether Sentia has an adapter
for it. Detecting a framework does not mean all its behavior can be traced.

A repository can contain multiple stacks: for example, a web frontend, a Django
API, Celery workers, and both MongoDB and a SQL database. Profiles guide retrieval
and adapter selection without excluding connections across those subsystems.

### Retrieval and evidence

Semantic search finds code whose meaning matches the request even when names
differ. Exact search preserves matches for fields, routes, task names, and symbols.
Merge their rankings and send a bounded, deduplicated shortlist to the planner.

Search results are candidates. Similarity never becomes an execution edge.
Connections must be supported by structural facts or explicit framework rules;
unverified interpretations remain separate and labeled.

## Stages

Repository preparation is reused across requests. Investigation repeats only when
the requested feature needs more evidence.

| Stage | Components and their role | Result |
| --- | --- | --- |
| **1. Prepare the repository** | Stack Profiler identifies the stack. Repository Index and Framework Adapters extract facts. Hybrid Retrieval updates searchable chunks. | A consistent repository revision, stack profiles, and search indexes. |
| **2. Understand the request** | Feature Planner decides whether this is one feature, a subsystem, or an overview, and defines the expected input and outcome. | A scope and a list of things the trace must explain. |
| **3. Find candidate anchors** | Hybrid Retrieval finds relevant entry points, operations, and outcomes. Feature Planner assigns candidate roles. | An initial feature plan linked to real symbols. |
| **4. Resolve the trace** | Trace Resolver searches forward from inputs and backward from outcomes using indexed relationships and adapter facts. | Evidence paths, data mappings, and unresolved transitions. |
| **5. Investigate gaps** | Coverage Reviewer identifies missing evidence. Hybrid Retrieval fetches targeted ranges; the planner interprets them and revises anchors or stages. | An improved trace, or a precise explanation of the remaining gap. |
| **6. Validate and save** | Sentia checks evidence, hashes, revision, and transition support. Coverage Reviewer assesses the requested outcome. Trace Store records dependencies. | A validated trace with an explicit coverage status. |
| **7. Present and expand** | Presenter groups implementation into readable stages. Expansion retrieves cached details; new questions can resume investigation. | A compact map with inspectable source evidence. |

Stages 4–5 repeat within limits for time, model rounds, retrieved content, and graph
search. Stop when coverage is established, investigation makes no progress, or a
budget is exhausted. A partial result states what remains unknown and why.

## The feature trace

Each saved trace contains:

- Scope, inputs, entry points, and requested outcomes.
- Stages and candidate anchors, with roles such as trigger, transformation, and persistence.
- Transitions and their underlying evidence paths.
- Supported argument, return-value, field, and state-key mappings.
- Coverage gaps, unsupported boundaries, and budget limits reached.
- Repository revision and dependencies needed to detect stale results.

Allow roughly **3–8 initial anchor candidates per feature** as a starting policy,
not a completeness rule. Investigation can replace, remove, or add candidates
within its budget. Coverage review runs regardless of anchor count.

Data tracking starts with recognizable local assignments, arguments, returns, and
literal state keys. Dynamic behavior stays unresolved. Registration, invocation,
conditional transitions, and persistence are distinct relationships; a static map
does not claim guaranteed runtime execution.

Backend overviews group multiple feature traces. Features may share services and
models without needing one connected execution path across the entire backend.

## Efficiency and token ownership

Sentia performs stack detection, parsing, traversal, evidence validation, caching,
and layout with local compute. The LLM handles feature planning, ambiguous evidence,
and semantic coverage review. Embedding inference has a separate cost from the
generation model.

- Cache file extraction by content hash and extractor version; re-resolve affected dependencies after changes.
- Reuse an in-memory index per revision. Keep reusable fact identity separate from revision-specific evidence.
- Embed structural chunks such as functions and configuration blocks; update changed chunks and remove deleted ones.
- Read symbols and exact ranges instead of fixed file prefixes. Batch independent reads and avoid repeated context.
- Keep separate budgets for repository indexing, model investigation, and visible graph size.
- Reuse traces when their dependencies remain valid. Include discovery dependencies so new routes or features can invalidate an overview.
- Record tokens by planning, gap investigation, and review, plus embedding usage, latency, and cache reuse.

Ordinary reopening and expansion of an unchanged cached trace should require no
generation call. Token savings are a benchmark goal, not a guarantee.

## Major differences from V1

This comparison describes the current implementation. Some capabilities appear in
the V1 design but are still follow-up work.

| Area | Current V1 | Proposed V2 |
| --- | --- | --- |
| Main result | A bounded graph around selected symbols. | A feature trace that explains input, operations, and outcome. |
| Stack awareness | No dedicated stack-profile stage. | Evidence-backed profiles guide retrieval and adapters before anchor selection. |
| Anchors | At most three; coverage review is skipped when three are initially selected. | Flexible candidates with roles; review always runs. |
| Search | Identifier and repository-metadata matching. | Hybrid lexical and semantic retrieval tied to source identities. |
| Investigation | File selection, bounded reads, root selection, and optional review. | Repeated, targeted investigation of missing feature stages and transitions. |
| Relationships | Conservative Python calls and constructors; framework types exceed implemented extraction. | Explicit adapter connections, JS/TS support, and bounded data mappings. |
| Coverage | Mainly selected-anchor connectivity and traversal warnings. | Checks whether the requested input-to-outcome behavior is explained. |
| Source access | Retained manifest capped at 250 files; reads limited to fixed prefixes and character budgets. | Index limits separate from prompt limits; targeted symbol and range retrieval. |
| Updates | Watcher refresh rebuilds repository facts. | Changed-file extraction, affected-dependency resolution, and cached index reuse. |
| Display | Symbols and unresolved operations dominate. | Feature stages with expandable implementation and evidence. |
| Broad requests | The same small root-selection workflow. | An inventory and plan for multiple feature traces. |

## Delivery and validation

1. Add the shared trace contract, stack profiles, targeted reads, and unconditional coverage review.
2. Add incremental indexing and hybrid retrieval over shared source identities.
3. Add framework adapters and bounded data tracking through complete vertical slices.
4. Add feature-stage presentation, trace reuse, and backend overview planning.

Adapters need generic rules and fixtures, not special cases for evaluation repos.
Version the new trace contract and keep Python and TypeScript validators aligned.

Measure relationship precision, stage and transition coverage, token usage, warm
latency, and incremental update cost. Test framework ambiguity, branches, retries,
deletions, stale evidence, and unsupported patterns. A useful release test follows
an input across an HTTP handler, background task, workflow function, and persisted
outcome, with evidence at each boundary.

## Implementation status

The extension now requests V2 through the existing flow-map endpoint using
`engineVersion: "2"`. Requests that omit this field keep V1 behavior.

| Available in the first slice | Still to build |
| --- | --- |
| Local stack detection with declared/observed evidence paths. | Dedicated subsystem inventories and complete backend overviews. |
| Shared Codex/Claude planning and coverage loop, with up to eight anchors. | Semantic embeddings and hybrid ranking; current retrieval is lexical. |
| Behavioral specification, up to 24 candidate decisions, and evidence-gated stage selection. | Live evaluation of semantic selection across models and repositories. |
| Targeted source reads beyond V1's file-prefix limit. | More advanced retrieval and long-function evidence planning. |
| Import-backed Python rules for Celery dispatch, LangGraph registration/invocation/transitions, and basic FastAPI/Django handlers. | JavaScript/TypeScript tracing, more framework patterns, and complete route mount resolution. |
| Directed source paths, explicit gaps, bounded reviews, and stale-evidence checks. | Automated argument/state data-flow proofs and richer persistence/event adapters. |
| Compact stage view, source navigation, token breakdown, and a code-graph toggle. | Saved trace reuse and dependency-based invalidation. |
| Existing repository snapshots. | Incremental fact extraction and removal of the retained-manifest bottleneck. |

The engine currently runs one planning turn and up to two review/investigation
turns. Source reads are bounded at 16,000 characters per turn; directed path search
is bounded at 2,000 visits and 12 hops. These are starting policies, not performance
claims. Stack profiling is local and currently inspects retained manifest files.

Stage labels and outcome assessments remain model interpretations of inspected
source. Connections retain indexed evidence. Registration alone cannot satisfy
execution coverage, and a backend overview remains partial until inventory-based
coverage is implemented. Changed evidence causes the request to fail with a
refresh message rather than publishing a stale trace.

Generic tests cover an HTTP handler → Celery task → invoked LangGraph workflow →
validation/repair → persistence function. Provider calls are mocked in these tests;
live-model accuracy, token savings, and latency still need measurement on real
repositories.

### Feature understanding and useful detail

The first model turn describes the actor, input, behavior, intended outcome,
competing interpretations, and open questions. It proposes candidates and reads;
Sentia does not promote first-pass lifecycle labels into stages.

Subsequent turns compare inspected functions. Each candidate has a responsibility,
an inclusion reason, a decision (`include`, `supporting`, `exclude`, or `uncertain`),
and citations inside its function. Invalid citations or incomplete function reads
leave the candidate unverified. Only inspected `include` decisions become stages.
Stage labels come from the reviewed responsibilities. Source review is a model
judgment, not a deterministic proof that a behavioral claim is true.

Investigation can consider 24 candidates independently of eight visible anchors.
Explicit requested reads go first. Structural neighbors get catalog space before
lexical alternatives, and source reads expand to connected functions when budget
allows. Repeated read batches are cached within the investigation, with freshness
checks. Relevant source is still included when reviewing a changed interpretation;
there is no promise that every token is sent only once.

The main view shows reviewed behavior and reasons for inclusion. Supporting helpers,
excluded alternatives, and uncertain proposals live in expandable investigation
details. Generic logging and formatting are not permanently blacklisted: they can
be central when the user asks about auditing or formatting. The full code-evidence
view remains available and may contain incidental implementation details.

Collapsed connections retain source paths, intermediate function names, conditions,
and asynchronous boundaries. Reviewed exclusions cannot serve as bridges between
stages. If the first review fails, the result has no established stages or connections;
it retains candidate proposals and gaps. A later failure can retain previously
reviewed stages without claiming complete coverage.

Regression fixtures include login versus calendar OAuth/internal credentials and
generation versus sharing/prototypes/manual finalization. These tests use scripted
model decisions to check discovery context, source validation, filtering, and UI
behavior. They do not measure a live model's semantic accuracy or prove fewer tokens.

Search hints are whitespace-normalized, deduplicated, and bounded locally (four
queries of 300 characters). Their schema advertises the same bounds, so malformed
retrieval hints do not discard a paid plan or trigger an extra model call.

Each turn's response schema includes the exact permitted catalog IDs for candidates,
stage anchors, and read requests. Local validation remains authoritative. If a
provider still returns unknown IDs, Sentia drops those references and records a gap,
retaining valid candidates for the already-budgeted review. If none remain, it keeps
one local discovery candidate as explicitly uncertain. It never invents a replacement
ID, matches a similar name, or adds a paid planning retry.

### Claude trace cost controls

Claude trace turns request low effort, a 4,096-token output limit, and a $0.50
SDK budget per turn. The existing four-turn SDK limit and 90-second trace-turn
timeout remain. The SDK budget is checked by the agent loop; it is not a prepaid
hard billing ceiling and an in-flight request can exceed it. Up to three trace
turns can run for one map. These controls have been tested with mocked providers;
their effect on live latency and trace quality still needs measurement.

Logs record prompt character counts, model, diagnostic ID, per-message usage,
and final SDK cost/turn counts when available. Cached input is included in the
displayed input-token total, with cache categories recorded separately in logs.
Message usage and final usage are alternative views, not additive totals.
A timeout can still incur provider charges; usage unavailable before cancellation
cannot be reconstructed locally. Source contents and credentials are not logged.
