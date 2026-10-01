# ADR 0004: Root-seeded, evidence-backed Flow Maps

- Status: Accepted
- Date: 2026-08-17

## Context

Sentia already uses Codex or Claude to investigate a repository, select files,
read bounded source evidence, and answer developer questions. Flow Maps should
reuse that investigation instead of asking a second model pass to rediscover the
same repository context.

A model is useful for mapping a concept such as "login" to symbols such as a
form submission handler, session route, credential verifier, or token issuer.
It is not an authoritative source for whether one symbol calls another, whether
an import exists, or where a declaration is located.

Repository flows also cross boundaries that a language call graph cannot see.
For example, a TypeScript `fetch` call and a Python FastAPI route have no native
compiler edge between them. The same is true for React webview messages, VS Code
extension handlers, sidecar events, and other framework-mediated transitions.

Finally, a code flow is not generally a tree. Calls may cycle, several callers
may share one callee, asynchronous work may branch, and one feature may have
multiple entry points. The UI may project a bounded graph as a tree-like flow,
but the underlying index must retain graph semantics.

## Decision

Flow Maps V1 uses root-seeded deterministic traversal:

```text
Developer request
    ↓
Existing repository investigation
    ↓
AI selects one to three indexed root entities
    ↓
Sentia validates the roots against one repository revision
    ↓
Sentia traverses parser-, compiler-, and adapter-derived relationships
    ↓
Sentia constructs a versioned Flow Graph
    ↓
ELK layout and React Flow rendering
```

The existing repository investigation produces a reusable, revision-bound
`RepositoryInvestigation` artifact. It records selected files, complete bounded
read ranges, final validated evidence, search queries when available, and
candidate identifiers. A textual answer and a Flow Map may consume the same
artifact.

The model returns a `FlowRootSelection` containing one to three existing entity
IDs. It may explain why those roots are relevant and identify unresolved user
concepts. It does not return graph edges, source locations, React Flow nodes, or
layout coordinates.

Sentia owns structural truth:

- Python AST and conservative Python binding resolve Python facts.
- The TypeScript compiler and type checker resolve TypeScript/JavaScript facts.
- Framework adapters connect HTTP, webview-message, and event boundaries.
- The graph builder validates roots, traverses facts, enforces budgets, handles
  cycles, includes connector entities, and retains evidence.
- Source reads remain a fallback when normalized structure cannot resolve
  semantic relevance.

V1 exposes one `feature_flow` traversal profile. It begins with forward
traversal and supports bounded, lazy one-hop caller or callee expansion. The
default request accepts at most three roots, five traversal levels, and fifty
initially visible nodes. These are product defaults rather than graph-schema
limits and may be adjusted after benchmarks.

## Cross-language boundaries

Folders and languages do not prevent connection once their declarations are
normalized into Sentia entities. A boundary is connected only when Sentia has
evidence for a shared framework key.

For example:

```text
TypeScript function
    -- sends_http_request -->
HTTP POST /api/session
    -- handles_http_request -->
Python route function
```

The HTTP endpoint is a synthetic entity. Both adjacent relationships retain
their own source evidence. Sentia must not represent the TypeScript function as
directly calling the Python function.

If a path or message name is constructed dynamically, the relationship remains
unresolved. The model may select a second root on the other side of the
boundary, but that does not convert an unproven connection into a resolved one.

## Evidence and resolution

Every displayed relationship carries at least one source span with a repository
revision and file content hash. Each relationship also carries extractor name,
extractor version, rule identifier, and one resolution value:

- `compiler_resolved`
- `statically_resolved`
- `framework_inferred`
- `unresolved`
- `runtime_observed` (reserved for a post-V1 runtime overlay)

V1 does not use numeric confidence. Resolution, provenance, ambiguity, and
evidence communicate what Sentia knows without implying uncalibrated precision.

Lexical call order and branch membership may be displayed. They must not be
described as guaranteed runtime execution order.

## Stable identity

Entity identity is derived from workspace identity, language, normalized module
identity, qualified name, kind, and an overload/local discriminator when
required. Line numbers are not part of entity identity.

Relationship-fact identity is derived from source entity, target or unresolved
key, relationship kind, call-site source span, and extractor rule. Display edges
may aggregate multiple relationship facts, but must retain their evidence.

Flow-node identity is a projection identity and is separate from entity
identity. Layout and interaction state are stored separately from structural
facts.

The normative ID construction algorithm will be implemented with the structural
index. V1 IDs use opaque prefixes (`ent_`, `rel_`, `node_`, `edge_`, `map_`, and
`inv_`) so clients do not depend on their internal hash representation.

## Security and resource boundaries

Indexers may parse repository text but may not execute repository code, imports,
TypeScript plugins, custom transformers, build scripts, or framework startup.
Existing ignore, symlink, size, binary, and secret rules apply before indexing
or source retrieval.

Parsing and graph traversal require time, memory, file-count, depth, and node
budgets. A failed language index produces a partial map with diagnostics rather
than allowing the model to invent missing structure.

## Consequences

- Existing Codex/Claude investigation evidence becomes reusable product state.
- Warm Flow Map requests avoid a second repository-reading workflow.
- Graph topology is deterministic for the same roots and repository revision.
- Cross-language coverage depends on explicit, testable adapters.
- Cold indexing introduces implementation and latency cost.
- Dynamic behavior remains incomplete and must be shown as unresolved.
- The graph core stays host-neutral; the VS Code webview is one renderer rather
  than the owner of structural truth.

## V1 non-goals

- Model-authored edges presented as verified facts
- Raw AST transport to the model or webview
- Guaranteed execution order or full control-flow graphs
- Runtime tracing
- ERD or database data-flow generation
- Embedding search
- Languages other than Python, TypeScript, and JavaScript
- General-purpose framework inference
- Rendering an entire repository at function granularity
