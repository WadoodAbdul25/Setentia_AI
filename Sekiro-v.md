# Sekiro-v implementation log

This file records the delivered repository-intelligence phases. It is updated
at the end of every completed phase with the user-visible outcome and the
verification performed.

## Phase 1 — Foundation and evaluation

**Status:** Complete

Added a workspace-scoped index contract, deterministic Python and
TypeScript/JavaScript extractors, explicit `exact`/`best_effort`/`unresolved`
edge resolution, and focused regression fixtures. Extraction is syntax-first;
it never represents an unresolvable dynamic call as certain.

## Phase 2 — Persistent hybrid index

**Status:** Complete

Implemented a local `.sentia/repository-index.db` SQLite source of truth with
file hashes, symbols, import/call edges, line-preserving chunks, summary cache
keys, and FTS5 search. Added authenticated search and graph API endpoints.

## Phase 3 — Incremental lifecycle

**Status:** Complete

Connected snapshot attachment and watcher updates to changed-file index writes.
Unchanged hashes are skipped; deleted files and their FTS/chunk records are
removed. Index revisions are emitted as `repository.index_updated` events.

## Phase 4 — Graph vertical slice

**Status:** Complete

The authenticated graph endpoint and an extension-bridged module graph panel
are implemented. The panel is intentionally collapsed by module and opens the
corresponding file through the existing restricted evidence bridge. Protocol,
webview, and extension type checks pass.

## Phase 5 — Voice and graph focus

**Status:** Complete

Structural wording such as “what calls this” or “what imports this” now loads
the live local graph alongside the existing spoken answer path. Informational
voice questions retain the grounded answer flow without loading graph data.

## Phase 6 — Semantic retrieval and shared agent memory

**Status:** Not started

## Phase 7 — Interactive graph exploration

**Status:** Complete

Replaced the static module list with a dark interactive file graph. Teal file
bubbles expand on hover or keyboard focus to show a concise deterministic
summary and the first three indexed functions. Clicking a bubble opens its
file through the restricted editor bridge and highlights all indexed symbol
ranges for four seconds. Exact edges are solid; best-effort edges are dashed.
