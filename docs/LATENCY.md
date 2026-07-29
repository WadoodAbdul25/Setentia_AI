# Sentia latency strategy

Sentia should optimize both total answer time and perceived responsiveness
without weakening repository grounding. Keeping an entire repository in a model
context is not the intended solution: larger prompts cost more, take longer to
prefill, become stale, and make evidence boundaries harder to enforce.

## Current request path

1. Reuse the in-memory project snapshot and structural manifest.
2. For Claude, start one Agent SDK turn with snapshot-backed search and read
   tools; for Codex, run the cached-manifest shortlist.
3. Read, redact, and line-number selected evidence locally.
4. Produce a structured, cited answer and validate citations before display.

The local snapshot and manifest are already cached. The Codex SDK process is
also kept warm for the lifetime of the sidecar, while individual model threads
remain ephemeral and read-only. This avoids repeated CLI startup without
carrying stale repository content between questions.

Claude diagnostics record `agent_elapsed_ms`, Agent SDK session/turn/cost data,
and `total_elapsed_ms`. Codex diagnostics record `selection_elapsed_ms`,
`content_read_elapsed_ms`, `answer_elapsed_ms`, `client_reused`, and total time.
Optimization decisions should be based on these values, not only the total time
observed in the UI.

## Next implementation order

### L1 — Adaptive one-call fast path

Classify questions locally using paths, symbols, active-editor context, and
conversation references. Use deterministic retrieval plus one answer request
when confidence is high. Keep the current two-request planner for ambiguous or
broad questions. Quality evaluations must show that the fast path does not
return weaker evidence sets before it becomes the default.

Examples suitable for the fast path include questions naming a known file,
symbol, route, component, or previously cited evidence. Repository overviews
and vague questions should retain model-assisted planning until local retrieval
has proven equivalent coverage.

### L2 — Versioned repository memory

Cache these artifacts outside the model context:

- File outlines and symbol metadata keyed by content hash
- Redacted evidence chunks keyed by file hash and line range
- A grounded repository overview with its source snapshot revision
- Recent question, answer, selected-file, and evidence references

File changes invalidate only entries derived from changed hashes. Cached
answers must never survive a snapshot revision unless all of their evidence
hashes still match.

### L3 — Conversation retrieval memory

Store compact conversation state rather than replaying every full answer. A
follow-up such as “where is that implemented?” should resolve “that” to the
previous answer and evidence, then retrieve only missing files. The provider
receives a bounded conversational summary, relevant prior evidence references,
and newly selected code.

### L4 — Streaming and cancellation

Stream provider output and phase events to the webview so users immediately see
`Shortlisting`, `Reading`, and `Responding`. Structured citations can be
validated before the final message is committed even when answer text is shown
incrementally. Every provider request must remain cancellable.

## Guardrails

- Do not preload the whole repository into a provider context.
- Do not reuse stale model threads as the source of repository truth.
- Do not return a cached answer without matching evidence hashes.
- Do not remove the planner from ambiguous questions merely to improve a
  latency benchmark.
- Track answer quality, selected-file recall, citation validity, first-token
  latency, and total latency together.
