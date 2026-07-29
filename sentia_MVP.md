# Sentia MVP Implementation Plan

This plan turns Sentia's editor-native product concept into a sequence of
independently verifiable tasks. Task IDs are continuous across phases so they
can be copied directly into an issue tracker.

The VS Code extension is the application and primary security boundary. React
renders an embedded editor webview; it is not deployed as a website. FastAPI is
a private local sidecar launched, authenticated, monitored, and stopped by the
extension; it is not a hosted backend. VS Code's stable Extension API is the
primary editor contract, with Cursor treated as a later compatibility target.

## Task categories

| Category | Meaning                                                                |
| -------- | ---------------------------------------------------------------------- |
| Product  | Product behavior, user journeys, and decision records                  |
| Frontend | React webview embedded in the editor and its client-side state         |
| Backend  | Supervised local FastAPI sidecar, persistence, and runtime behavior    |
| API      | HTTP, WebSocket, and cross-process contracts                           |
| AI       | Retrieval, prompting, grounding, summarization, and narration          |
| Editor   | Product host, sidecar supervision, VS Code context, and editor control |
| Agent    | Claude Code orchestration and coding-run lifecycle                     |
| Voice    | Wispr input experience and text-to-speech output                       |
| Security | Permissions, exclusions, secret handling, and auditability             |
| Tooling  | Workspace setup, developer tooling, packaging, and observability       |
| QA       | Automated tests, fixtures, performance checks, and acceptance testing  |

## Phase 0 — Product contract and project foundation

**Outcome:** The product boundary, shared vocabulary, repository structure, and
development workflow are stable enough for parallel feature development.

**Implementation status:** Complete. Product contracts live in `docs/`, the
shared protocol lives in `packages/protocol`, and automated checks are defined
in the root workspace and CI workflow.

| Task | Category | Description                                                                                                                                                                                                                                                                                         | Depends on | Complete when                                                               |
| ---- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | --------------------------------------------------------------------------- |
| T1   | Product  | Write the primary in-editor user journey covering extension installation, repository onboarding, grounded conversation, selection explanation, brainstorming, plan approval, live implementation, and final review. Explicitly exclude a standalone website and hosted Sentia backend from the MVP. | —          | The editor-native journey and non-goals are documented and reviewable.      |
| T2   | Product  | Define Sentia's state machine: `disconnected`, `indexing`, `ready`, `discussing`, `planning`, `awaiting_approval`, `prompting`, `executing`, `testing`, `completed`, `failed`, and `cancelled`. Specify allowed transitions and the visible UI treatment of each state.                             | T1         | Every runtime state and valid transition is documented.                     |
| T3   | Tooling  | Create the monorepo structure for the VS Code extension host, embedded React webview, supervised FastAPI sidecar, shared protocol, fixtures, and end-to-end tests. Add root development commands.                                                                                                   | T1         | All components can be installed and invoked from documented root commands.  |
| T4   | Tooling  | Configure TypeScript, ESLint, Prettier, Python formatting and linting, type checking, pre-commit hooks, and environment-variable templates.                                                                                                                                                         | T3         | Formatting, linting, and type-check commands pass on the initial workspace. |
| T5   | API      | Define versioned Pydantic schemas for workspace metadata, chat messages, evidence ranges, task plans, approvals, agent events, diffs, command results, and errors. Generate or maintain matching TypeScript types.                                                                                  | T2, T3     | Python and TypeScript share a tested, versioned protocol contract.          |
| T6   | QA       | Add continuous integration for webview checks, extension checks, sidecar checks, protocol compatibility tests, and unit tests.                                                                                                                                                                      | T4, T5     | A clean checkout passes one automated CI workflow.                          |

## Phase 1 — Editor application shell and supervised sidecar

**Outcome:** Installing and opening the extension produces an embedded React
interface and a healthy, authenticated local FastAPI sidecar without opening a
browser or requiring a hosted service.

**Implementation status:** Implemented and verified through static checks,
automated tests, production builds, and a real sidecar process smoke test. A
manual Extension Development Host launch is still required on a machine with
the VS Code `code` command before the phase is considered acceptance-complete.

| Task | Category | Description                                                                                                                                                                                                                                                              | Depends on   | Complete when                                                                                                      |
| ---- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------ | ------------------------------------------------------------------------------------------------------------------ |
| T7   | Editor   | Scaffold the VS Code extension as the Sentia application entry point. Register commands, create and restore the sidebar webview, respect Workspace Trust, and dispose owned resources when the editor or workspace closes.                                               | T3, T4       | Opening Sentia in an Extension Development Host displays an extension-owned placeholder panel.                     |
| T8   | Backend  | Scaffold the FastAPI sidecar with typed configuration, lifecycle management, SQLite persistence and migrations, structured errors, and `/health` and `/version` endpoints. Persist workspaces, conversations, messages, plans, runs, events, and audits.                 | T3, T4, T5   | The sidecar starts locally, reports a compatible version, and retains a test conversation across restarts.         |
| T9   | Editor   | Implement the sidecar supervisor in the extension: choose a random loopback port, create a short-lived token, start the expected packaged process, verify health and version, detect crashes, avoid duplicate processes, and stop only processes owned by the extension. | T7, T8       | The extension automatically reaches a healthy sidecar and cleans it up without touching unrelated processes.       |
| T10  | Frontend | Build the embedded React webview shell with editor-aware theming, accessible navigation, empty/loading/error states, and state for connection, workspace, conversation, active task, approvals, highlights, and speech.                                                  | T5, T7       | The webview renders inside VS Code in light and dark themes and can exercise state transitions with mocked events. |
| T11  | API      | Implement authenticated HTTP and WebSocket endpoints with typed event envelopes, sequence numbers, heartbeats, reconnect support, and replay from the last acknowledged event. Bind only to loopback and reject missing or invalid extension tokens.                     | T5, T8, T9   | The extension can connect securely, reconnect without event loss, and unauthorized local requests are rejected.    |
| T12  | Editor   | Implement the restricted extension bridge and typed webview client. The webview may request only allowlisted editor and sidecar actions and receives recoverable connection errors without gaining filesystem or process access.                                         | T10, T11     | The embedded UI exchanges typed events while direct privileged operations remain unavailable to webview code.      |
| T13  | Tooling  | Add correlated local logging across the extension, sidecar, and event stream plus an extension diagnostics view. Exclude sensitive prompt content and connection tokens by default.                                                                                      | T9, T11, T12 | One interaction can be traced across all local components without revealing credentials or repository secrets.     |

## Phase 2 — Editor awareness and visual grounding

**Outcome:** Sentia knows what the user is looking at and can open and highlight
the exact code being discussed.

**Milestone 1 status:** The allowlisted evidence-open command, workspace-boundary
validation, clickable evidence chips, and temporary line highlighting are
implemented. Active selection/symbol capture, multi-root handling, and editor
integration coverage remain.

| Task | Category | Description                                                                                                                                                                                                             | Depends on             | Complete when                                                                                  |
| ---- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------- | ---------------------------------------------------------------------------------------------- |
| T14  | Editor   | Capture trusted workspace roots, active file, language, cursor position, selected ranges, visible ranges, and document version. Debounce context changes and do not transmit unrelated file contents.                   | T5, T7, T12            | Moving between files and selections updates sidecar context accurately.                        |
| T15  | Editor   | Implement allowlisted commands to open a workspace file, reveal a line range, focus an editor column, and clear or apply Sentia-owned decorations. Validate every incoming path and range before invoking VS Code APIs. | T5, T7, T12            | A fixture event opens a workspace file and highlights the exact requested range.               |
| T16  | Editor   | Collect document symbols and definitions from VS Code language services when available, with a graceful fallback when a language server is absent.                                                                      | T14                    | Symbol information is returned for a supported fixture and absence is handled cleanly.         |
| T17  | Frontend | Add evidence chips and clickable `path:start-end` references to answers, plans, progress events, and final results. Route navigation through the restricted extension command from T15.                                 | T12, T15               | Every rendered evidence reference can navigate to and highlight its source.                    |
| T18  | Editor   | Implement trusted, restricted, empty, single-root, multi-root, local, and remote-workspace states. Disable unsupported capabilities explicitly instead of silently attempting local sidecar access.                     | T7, T9, T14            | The UI accurately communicates available capabilities for each workspace type and trust state. |
| T19  | QA       | Add extension integration tests for sidecar supervision, context capture, navigation, decoration lifecycle, workspace trust, multi-root workspaces, and renamed or deleted files.                                       | T9, T14, T15, T16, T18 | Editor integration tests pass against the maintained fixture workspace.                        |

## Phase 3 — Repository understanding and the codebase persona

**Outcome:** Sentia can explain what a repository is, how selected code works,
and what appears incomplete while grounding statements in repository evidence.

**Milestone 1 status:** A read-only vertical slice is implemented for macOS-first
Python and JavaScript/TypeScript projects. It connects either a user-supplied
Anthropic API key or local Codex login, inventories high-signal repository files,
filters ignored and sensitive content, routes structured explanations through
the selected provider, validates citations, and navigates to evidence. Persistent FTS indexing, incremental
updates, selection explanations, needs analysis, and grounding evaluations
remain. The current vertical slice caches the project tree and per-file
structural metadata on disk and in sidecar memory. Its question path uses a
shortlisting model, a deterministic local content reader with a 15-second
deadline, and a user-facing answer model; README is never forcibly included.

| Task | Category | Description                                                                                                                                                                                                                                                                                                                                                    | Depends on         | Complete when                                                                                                                          |
| ---- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| T20  | Security | Implement workspace path validation, symlink boundary checks, binary detection, size limits, `.gitignore` support, and `.sentiaignore`. Deny sidecar access outside trusted workspace roots supplied by the extension.                                                                                                                                         | T8, T18            | Traversal and symlink tests cannot expose files outside the workspace.                                                                 |
| T21  | Backend  | Build and cache the repository inventory: directories, text files, languages, sizes, Git status, tracked files, Markdown descriptions, manifests, tests, configuration entry points, imports, and top-level symbols. Keep the attached manifest in memory and persist it with timestamps.                                                                      | T8, T20            | A fixture repository produces a deterministic, queryable inventory that is reused between questions.                                   |
| T22  | Backend  | Add text and symbol extraction with line-preserving chunks. Use editor symbols first, then lightweight parsing and text fallbacks without requiring every language to have a custom parser.                                                                                                                                                                    | T16, T21           | Extracted chunks retain correct file paths and line ranges across fixture languages.                                                   |
| T23  | Backend  | Store searchable content and metadata in SQLite FTS. Implement lexical search, path filters, language filters, symbol boosts, and result deduplication.                                                                                                                                                                                                        | T8, T22            | Known fixture questions retrieve the expected evidence in the top results.                                                             |
| T24  | AI       | Generate and cache hierarchical summaries for symbols, files, directories, and the repository. Record source hashes and evidence ranges so stale summaries can be detected.                                                                                                                                                                                    | T22, T23           | Sentia can produce a repository overview whose major claims link to source evidence.                                                   |
| T25  | Backend  | Add incremental indexing triggered by file create, change, rename, delete, branch change, and manual refresh. Refresh cached structure metadata immediately, then invalidate only affected chunks and summaries once persistent content indexing exists.                                                                                                       | T21, T24           | Editing one file updates its cached metadata and results without rebuilding the entire fixture index.                                  |
| T26  | AI       | Define the codebase-persona prompt and response contract. Require separation of observed facts, inferences, proposed work, and current execution state; require citations for code claims.                                                                                                                                                                     | T1, T5, T24        | Contract tests reject uncited factual code claims and invalid evidence ranges.                                                         |
| T27  | AI       | Implement provider-neutral grounded question answering with three explicit roles: cached-structure shortlisting, bounded local content reading, and user-facing synthesis. Route both model roles through the user's selected Claude or Codex provider while sharing retrieval, validation, and evidence budgets without forcing README into the evidence set. | T17, T23, T26      | Both providers answer architecture and implementation questions with relevant code, retrieval within 15 seconds, and valid references. |
| T28  | AI       | Implement selection and line-by-line explanation. Explain logical groups by default, allow literal per-line detail on request, and connect identifiers to their definitions when evidence is available.                                                                                                                                                        | T14, T16, T27      | A user can select a fixture function and receive a plain-language grounded explanation.                                                |
| T29  | AI       | Implement a repository-needs analyzer using TODOs, incomplete stubs, failing checks supplied by the user, missing neighboring tests, and documented-but-absent features. Label every result as observed or inferred and attach confidence and evidence.                                                                                                        | T21, T23, T26      | “What do you need?” returns ranked, labeled findings without claiming guesses as facts.                                                |
| T30  | QA       | Build a retrieval and grounding evaluation set with expected files, line ranges, unsupported-question cases, and prompt-injection fixtures embedded in source files.                                                                                                                                                                                           | T23, T27, T28, T29 | The evaluation reports retrieval accuracy, citation validity, and unsupported-answer rate.                                             |

## Phase 4 — In-editor conversation and voice

**Outcome:** The user can have a fluid, interruptible conversation with the
repository by typing or dictating and can hear concise spoken responses.

| Task | Category | Description                                                                                                                                                                                                                                             | Depends on    | Complete when                                                                                 |
| ---- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- | --------------------------------------------------------------------------------------------- |
| T31  | Frontend | Build the embedded conversation interface with message history, streaming answers, evidence references, mode selector, prompt composer, stop control, retry behavior, and suggested follow-ups.                                                         | T12, T17      | A full grounded conversation can be completed without leaving the editor sidebar.             |
| T32  | API      | Implement conversation creation, message submission, answer streaming, cancellation, and restoration endpoints. Persist the evidence set used for every assistant answer.                                                                               | T8, T11, T27  | Refreshing or reopening the editor restores the conversation and its citations.               |
| T33  | Frontend | Optimize the prompt composer for Wispr Flow: reliable focus, visible dictation state guidance, multiline editing, configurable submit shortcut, and protection against accidental submission during partial dictation.                                  | T31           | A user can dictate, review, edit, and submit a prompt entirely from the keyboard.             |
| T34  | Voice    | Add text-to-speech behind an extension-owned provider interface using operating-system speech capabilities first. Support play, pause, stop, rate, and voice preferences without depending on a browser or paid TTS API.                                | T7, T31       | Sentia can speak an answer and stop immediately without stopping the conversation.            |
| T35  | AI       | Implement a speech formatter that turns detailed written responses into short, citation-free spoken summaries while leaving the complete grounded answer visible.                                                                                       | T26, T34      | Spoken output is concise and does not read paths, code blocks, or long diffs aloud.           |
| T36  | Frontend | Add explicit `Brainstorm` and `Plan & Build` modes plus a visible journey through Brainstorm, Plan mode, Prompting, Implementing, Testing, and Review. Brainstorm is always read-only; Plan & Build cannot hand off to the coding agent until approval. | T2, T31       | The current mode, activity state, and permissions are always visible and enforced by the API. |
| T37  | QA       | Run accessibility and conversational latency tests covering keyboard-only use, screen readers, streaming, cancellation, speech interruption, and long answers.                                                                                          | T31, T32, T34 | Critical flows pass accessibility checks and meet the documented latency budget.              |

## Phase 5 — Provider-neutral planning and controlled execution

**Outcome:** A conversation can become an approved coding task that Claude or
Codex executes while Sentia shows and narrates verified progress.

| Task | Category | Description                                                                                                                                                                                            | Depends on    | Complete when                                                                                  |
| ---- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------- | ---------------------------------------------------------------------------------------------- |
| T38  | Agent    | Define a provider-neutral `CodingAgentAdapter` with capabilities for availability checks, session creation, plan generation, execution, streaming events, cancellation, and final results.             | T2, T5        | Shared run and normalized event contracts have lifecycle, permission, and cancellation tests.  |
| T39  | Agent    | Implement Claude Agent SDK and OpenAI Codex SDK adapters. Pin both SDKs and bundled runtimes; normalize only structured events and never parse decorative terminal output.                             | T38           | Controlled fixture tasks from both providers produce the same Sentia event contract.           |
| T40  | Backend  | Build the durable sidecar agent-run manager with one active run per workspace, state transition enforcement, process ownership, timeouts, extension reload recovery, and cancellation.                 | T8, T11, T38  | Runs cannot overlap accidentally and abandoned processes are detected and handled.             |
| T41  | AI       | Convert a brainstormed request into a structured implementation plan containing goal, scope, assumptions, files likely involved, ordered steps, verification, risks, and unresolved questions.         | T27, T36, T38 | A Build-mode conversation can produce an editable, evidence-backed plan.                       |
| T42  | Frontend | Build the plan review UI with step editing, scope summary, risk indicators, estimated commands, and explicit approve, revise, or reject actions.                                                       | T31, T41      | No execution begins until a specific plan revision is visibly approved.                        |
| T43  | Security | Implement the permission broker for file writes, shell commands, network access, dependency installation, and access outside the workspace. Bind every decision to an agent run and plan revision.     | T20, T40, T42 | Disallowed operations stop and surface a precise approval request in the UI.                   |
| T44  | Agent    | Implement approved execution with workspace context, bounded turns and cost, allowed-tool or sandbox configuration, structured event normalization, provider selection, and clear failure propagation. | T39, T40, T43 | Each provider can complete an approved fixture edit without receiving unapproved capabilities. |
| T45  | Frontend | Build the live activity view showing current phase, active tool, file references, elapsed time, commands, test status, warnings, and a persistent stop button. Avoid exposing hidden chain-of-thought. | T11, T17, T44 | Every meaningful normalized agent event has a clear, human-readable UI state.                  |
| T46  | Editor   | Connect file-read and file-edit events to editor navigation and temporary highlights. Keep user focus stable unless the user enables automatic following.                                              | T16, T44, T45 | During a fixture run, Sentia reveals relevant edits without stealing focus unexpectedly.       |
| T47  | AI       | Implement live narration from normalized events. Announce only verified milestones, coalesce noisy activity, remain silent for trivial reads, and allow speech to be disabled independently.           | T35, T44      | Spoken progress accurately matches the event log and remains concise.                          |
| T48  | Agent    | Add verification execution for project-specific lint, type-check, and test commands proposed in the approved plan. Capture exit codes and bounded output as structured results.                        | T43, T44      | The final result distinguishes passed, failed, skipped, and unavailable checks.                |
| T49  | Backend  | Capture the pre-run and post-run Git state and calculate the run-owned diff without staging or committing. Detect unrelated pre-existing changes and keep them separate.                               | T20, T40, T44 | The sidecar can identify which changes belong to the run in a dirty fixture repository.        |
| T50  | Frontend | Build the completion view with goal summary, changed files, navigable diff, verification results, remaining concerns, accept result, and continue conversation actions.                                | T17, T48, T49 | The user can inspect every run-owned change and its verification evidence.                     |

## Phase 6 — Trust, resilience, and MVP release

**Outcome:** The complete experience is secure enough for personal repository
use, recoverable after common failures, and proven by an end-to-end demo.

| Task | Category | Description                                                                                                                                                                                                                                                                                                                    | Depends on             | Complete when                                                                                                                   |
| ---- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| T51  | Security | Add secret detection and redaction before repository content, command output, events, or logs are sent to a model or persisted. Cover environment files, private keys, tokens, and common credential formats.                                                                                                                  | T13, T20, T44          | Secret fixtures are excluded or redacted in prompts, persistence, and diagnostic logs.                                          |
| T52  | Security | Implement an append-only audit view for user prompts, plan revisions, approvals, agent capabilities, commands, edits, checks, cancellation, and errors.                                                                                                                                                                        | T8, T43, T49           | A user can reconstruct why and how every material action occurred.                                                              |
| T53  | Backend  | Add recovery for sidecar restarts, extension reloads, WebSocket loss, Claude Code failure, malformed events, deleted workspaces, and user cancellation. Coordinate ownership so recovery cannot leave orphan processes.                                                                                                        | T9, T11, T25, T40      | Each failure produces a stable terminal or recoverable state without orphan processes.                                          |
| T54  | Frontend | Build extension-first onboarding for workspace trust, sidecar health, Git, Claude and Codex availability, explicit coding-provider selection, indexing progress, Wispr usage guidance, TTS preferences, and a first suggested question. Keep provider choice inside the editor, using a browser only for provider-owned OAuth. | T9, T14, T25, T31, T39 | A new user can choose and connect a coding provider, then reach their first grounded answer without manual configuration files. |
| T55  | QA       | Create representative fixture repositories and end-to-end tests for onboarding, “What are you?”, selected-code explanation, “What do you need?”, plan approval, execution, highlights, cancellation, diff review, and failed tests.                                                                                            | T30, T50, T53, T54     | The critical journey passes automatically on the supported development platform.                                                |
| T56  | QA       | Measure indexing time, incremental refresh time, first-token latency, memory use, reconnect behavior, and event throughput. Define and enforce practical MVP budgets.                                                                                                                                                          | T25, T32, T45, T55     | Benchmarks are recorded and regressions beyond the agreed budgets fail CI.                                                      |
| T57  | Tooling  | Package the extension, embedded webview, compatible FastAPI sidecar, and required local binaries into an installable MVP build. Include sidecar version checks, platform packaging, and diagnostics documentation.                                                                                                             | T6, T53, T54           | Installing the extension on a clean supported machine launches Sentia without separately installing or hosting a website.       |
| T58  | Product  | Run the final acceptance scenario on a non-trivial sample repository and record usability problems, grounding failures, unsafe surprises, and deferred features.                                                                                                                                                               | T55, T56, T57          | The complete definition-of-done scenario passes and release blockers are resolved.                                              |

## Recommended implementation order

The phase order is also the default implementation order. Within a phase, tasks
whose dependencies are satisfied may proceed together. The critical path is:

```text
T1 -> T2 -> T5
T1 -> T3 -> (T7 + T8) -> T9
(T5 + T9) -> T11 -> T12 -> T14 -> (T16 + T18)
T18 -> T20 -> T21
(T16 + T21) -> T22 -> T23 -> T24 -> T26 -> T27 -> T31 -> T36
   -> T38 -> T39 -> T40 -> T41 -> T42 -> T43 -> T44 -> T48
   -> T49 -> T50 -> T55 -> T58
```

## MVP release gate

Sentia is ready for its MVP demonstration only when all of the following are
true:

- Repository claims include valid evidence or are explicitly labeled as
  inference.
- The complete product experience runs inside the editor; no browser tab or
  hosted Sentia service is required.
- The extension securely starts, authenticates, monitors, and stops its local
  sidecar.
- The selected-code explanation opens and highlights the correct source range.
- Wispr Flow can dictate naturally into the composer without a custom Wispr API.
- Speech can be interrupted independently of an agent run.
- Auto mode is the default. The model recommends Brainstorm or Plan & Build for
  each request, while the user can override it explicitly.
- Model mode selection cannot fabricate execution progress; Prompting,
  Implementing, Testing, and Review require observable runtime events.
- Brainstorm mode cannot edit files or execute commands.
- Plan & Build mode cannot enter Prompting or Implementing until the displayed
  plan revision is approved.
- Every command, edit, check, and permission decision appears in the audit trail.
- Cancellation stops the coding process without losing the conversation or diff.
- The completion view separates run-owned changes from pre-existing changes.
- The end-to-end “bring your codebase to life” scenario passes on a clean setup.

## Post-MVP backlog

The following features are intentionally deferred:

- Multiple concurrent coding agents and isolated Git worktrees
- Cursor-specific APIs beyond tested stable VS Code extension compatibility
- Additional coding-agent adapters
- Tree-sitter coverage and embedding-based semantic retrieval
- Cloud sync, remote execution, user accounts, teams, and billing
- GitHub issues, pull requests, design tools, and project-management connectors
- Automatic commits or pull requests
- Custom speech recognition that replaces Wispr Flow
- A standalone website, mobile client, or browser-only editor experience
