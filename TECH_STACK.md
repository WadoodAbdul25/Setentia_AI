# Sentia Technology Stack

This document defines the technologies, packages, external applications, and
third-party APIs planned for the editor-native Sentia MVP. Package versions will
be pinned in lockfiles when the application is scaffolded; this document names
packages and responsibilities rather than embedding version numbers that will
quickly become stale.

## Form-factor decision

Sentia is not a website. The installable VS Code extension is the application.
React renders a webview embedded inside the editor, and FastAPI runs as a private
local sidecar supervised by the extension.

This means the MVP has:

- No browser tab or public product URL
- No separately deployed frontend
- No hosted Sentia backend
- No Sentia login or cloud account
- No direct filesystem access from the React webview
- No editor automation through screen scraping

The word **webview** describes the editor's embedded HTML/CSS/JavaScript UI
surface; it does not describe Sentia's distribution model. A browser harness may
exist for isolated component development, but it is not a supported product
surface.

## Stack summary

| Layer                                   | Primary technology             | Primary use                                                                                           |
| --------------------------------------- | ------------------------------ | ----------------------------------------------------------------------------------------------------- |
| Product host                            | VS Code extension + TypeScript | Own application lifecycle, workspace trust, editor access, and secure component bridging              |
| Embedded editor UI                      | React + TypeScript             | Conversation, task plans, approvals, live activity, evidence links, and diff review inside the editor |
| Editor integration                      | VS Code Extension API          | Read active editor context and open, reveal, focus, or highlight code                                 |
| Webview build                           | Vite                           | Develop and bundle the embedded React interface                                                       |
| Local sidecar                           | FastAPI + Python               | Repository intelligence, conversations, persistence, permissions, and agent orchestration             |
| Extension-to-sidecar realtime transport | WebSockets                     | Stream answer tokens, state changes, coding-agent events, and cancellation acknowledgements           |
| Extension-to-sidecar request transport  | HTTP + OpenAPI                 | Health, workspace setup, history, settings, and other non-streaming operations                        |
| Webview bridge                          | VS Code `postMessage` API      | Carry allowlisted UI requests and state events between React and the extension host                   |
| Local database                          | SQLite                         | Conversations, summaries, task plans, agent events, approvals, and audit records                      |
| Search                                  | SQLite FTS5                    | Initial lexical retrieval over files, symbols, and summaries                                          |
| Read-only AI                            | Anthropic Messages + Codex SDK | Provider-selected repository summaries, grounded explanations, planning, and concise narration        |
| Coding agents                           | Claude Agent SDK + Codex SDK   | Interchangeable approved inspection, editing, command execution, and verification                     |
| Voice input                             | Deepgram Flux `/v2/listen`     | Turn-aware streaming transcription with repository keyterms and correction                            |
| Voice output                            | Web Speech synthesis           | Speak normalized voice-initiated answers without exposing credentials to the webview                  |
| Source control                          | Git                            | Workspace state, change detection, run-owned diffs, and rollback evidence                             |
| File discovery                          | ripgrep                        | Fast file and text discovery while honoring repository boundaries                                     |

## Runtime architecture

```text
VS Code editor
    |
    |-- VS Code extension host <----> VS Code Extension API
    |       |-- owns workspace and editor access
    |       |-- supervises local process lifecycle
    |       |-- provides a restricted webview bridge
    |       `-- owns HTTP/WebSocket connection to sidecar
    |
    |-- React webview
    |       |-- captures 80 ms WebM/Opus microphone chunks
    |       `-- communicates only through extension postMessage
    |
    `-- FastAPI sidecar on loopback
            |-- SQLite / FTS5
            |-- repository indexer
            |-- repository vocabulary and transcript correction
            |-- authenticated Deepgram Flux WebSocket
            |-- Anthropic API (key validation)
            |-- Claude Agent SDK + snapshot-backed read tools
            `-- OpenAI Codex SDK
                    |
                    v
              approved repository tools
```

The FastAPI sidecar listens only on the loopback interface for the MVP. The
extension starts it, supplies a short-lived connection token, verifies its
identity and version, and owns its lifecycle. A random available port is
preferred over a fixed port. Only the extension knows the sidecar endpoint and
token. The React webview does not connect to the sidecar directly and does not
receive filesystem, process, or network credentials.

## Embedded editor UI

### React and TypeScript

React is used for the entire interactive surface. The primary UI is rendered in
a VS Code webview rather than a separate desktop window. TypeScript provides
compile-time safety for messages exchanged among the React UI, extension host,
and FastAPI sidecar.

The React application is responsible for:

- Conversation history and streaming responses
- Repository evidence and file/line links
- Brainstorm and Plan & Build mode selection
- Visible workflow journey from planning through prompting, implementation,
  testing, and review
- Plan review and explicit approval
- Live coding-run state and cancellation
- Command and test-result presentation
- Final diff review
- Speech playback controls and settings

### Editor UI runtime packages

| Package                 | Primary use                                                                                                                  |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `react`                 | Component model and UI rendering                                                                                             |
| `react-dom`             | Mount the React application inside the webview                                                                               |
| `zustand`               | Small client-owned state such as current panel, temporary draft, playback state, and connection indicator                    |
| `@tanstack/react-query` | Cache request/response resources obtained through the extension bridge, such as history, settings, and completed run details |
| `zod`                   | Validate untrusted messages arriving from the extension host before updating UI state                                        |
| `react-markdown`        | Render assistant explanations and plans as controlled Markdown                                                               |
| `remark-gfm`            | Support tables, task lists, and other GitHub-flavored Markdown used in plans                                                 |
| `rehype-sanitize`       | Sanitize generated Markdown and prevent unsafe HTML from entering the webview                                                |
| `shiki`                 | Syntax-highlight short code examples in conversation responses                                                               |
| `diff`                  | Parse and format unified diffs for the run-completion view                                                                   |
| `clsx`                  | Build conditional class names without repetitive string handling                                                             |

The webview does not open HTTP or WebSocket connections. It uses VS Code's
`acquireVsCodeApi().postMessage(...)` bridge, and the extension owns all sidecar
transport. Socket.IO is unnecessary because the extension and sidecar use a
small, versioned protocol.

### Editor UI development packages

| Package                     | Primary use                                                   |
| --------------------------- | ------------------------------------------------------------- |
| `typescript`                | Type checking and compilation                                 |
| `vite`                      | Development server and production webview bundle              |
| `@vitejs/plugin-react`      | React Fast Refresh and JSX transformation in Vite             |
| `vitest`                    | Fast frontend unit and component tests                        |
| `jsdom`                     | Browser-like DOM environment for component tests              |
| `@testing-library/react`    | Test React behavior from the user's perspective               |
| `@testing-library/jest-dom` | Readable DOM assertions                                       |
| `eslint`                    | TypeScript and React static analysis                          |
| `prettier`                  | Consistent formatting for TypeScript, JSON, CSS, and Markdown |

We are not using Next.js in the MVP. Server-side rendering, web pages, and public
routing do not help an editor-hosted local application.

## Product host: VS Code extension

The extension host is the Sentia application entry point, not a thin accessory
to a web application. It is a TypeScript process separate from the React
webview and the only component allowed to call VS Code editor APIs.

It is responsible for:

- Creating and restoring the Sentia sidebar
- Starting and stopping the local FastAPI process
- Reporting workspace roots, trust state, active file, selection, and symbols
- Opening files and revealing exact line ranges
- Applying and clearing Sentia-owned decorations
- Forwarding typed messages between the webview and local sidecar
- Keeping local connection credentials out of webview storage

### Extension packages

| Package                 | Primary use                                                                        |
| ----------------------- | ---------------------------------------------------------------------------------- |
| `@types/vscode`         | Type definitions for the stable VS Code Extension API                              |
| `ws`                    | WebSocket client owned by the extension for the authenticated sidecar event stream |
| `esbuild`               | Bundle the extension host into a small Node-compatible artifact                    |
| `@vscode/test-cli`      | Configure and run extension integration test suites                                |
| `@vscode/test-electron` | Launch an isolated desktop VS Code instance for extension tests                    |
| `@vscode/vsce`          | Package the extension as a `.vsix` artifact                                        |

The `vscode` module itself is supplied by the editor at runtime and is not
bundled. Sentia will target the stable VS Code API and test Cursor compatibility
separately; it will not depend on undocumented Cursor APIs for the MVP.

## Supervised local sidecar

### FastAPI and Python

FastAPI is the private local control plane. It fits the MVP better than Django because
Sentia needs typed APIs, long-lived WebSocket streams, async model calls, and
coding-agent lifecycle management more than it needs server-rendered pages,
admin screens, or a large relational application framework.

The sidecar is not independently deployed, publicly reachable, or presented to
the user as a separate application. The extension launches a compatible
sidecar build, performs health and version checks, and terminates owned
processes during shutdown or recovery.

The local sidecar is responsible for:

- Workspace validation and safe file access
- Repository indexing and incremental refresh
- Retrieval and hierarchical summaries
- Grounded repository conversation
- Task-plan and approval state machines
- Claude coding-run orchestration
- Normalized progress events and narration
- Persistence, audit records, diffs, and verification results

### Sidecar runtime packages

| Package               | Primary use                                                                                             |
| --------------------- | ------------------------------------------------------------------------------------------------------- |
| `fastapi`             | Typed HTTP endpoints, OpenAPI generation, dependency injection, lifecycle hooks, and WebSocket routes   |
| `uvicorn[standard]`   | Local ASGI server, event loop integration, WebSocket transport, and development reload support          |
| `pydantic`            | Request, response, event, evidence, plan, and configuration data models                                 |
| `pydantic-settings`   | Load typed settings from environment variables and local configuration                                  |
| `sqlalchemy[asyncio]` | Database models, queries, transactions, and async session management                                    |
| `aiosqlite`           | Async-compatible SQLite driver used by SQLAlchemy                                                       |
| `alembic`             | Version and apply database schema migrations                                                            |
| `httpx`               | Async HTTP client and API integration testing                                                           |
| `watchfiles`          | Observe repository file changes for incremental indexing                                                |
| `pathspec`            | Apply Git-style `.gitignore` and `.sentiaignore` rules consistently                                     |
| `structlog`           | Structured local logs with workspace, conversation, and run correlation IDs                             |
| `orjson`              | Efficient serialization for large event and indexing payloads where profiling justifies it              |
| `anthropic`           | Direct, read-only model requests for summaries, grounded explanations, planning, and narration          |
| `claude-agent-sdk`    | Interactive Claude Code sessions, tool events, hooks, permissions, cancellation, and coding-run results |
| `detect-secrets`      | Scan candidate content and command output before it is sent to a model or written to diagnostic logs    |

`orjson` is optional until profiling shows a benefit. The remaining packages are
part of the planned MVP baseline.

### Sidecar development packages

| Package          | Primary use                                                                |
| ---------------- | -------------------------------------------------------------------------- |
| `pytest`         | Sidecar unit, integration, protocol, and security tests                    |
| `pytest-asyncio` | Run async database, API, model-adapter, and orchestration tests            |
| `pytest-cov`     | Report sidecar test coverage                                               |
| `ruff`           | Fast Python linting and formatting                                         |
| `mypy`           | Static type checking for service boundaries and agent events               |
| `respx`          | Mock Anthropic and other outbound HTTP calls without accessing the network |
| `freezegun`      | Make event timestamps, timeouts, and recovery tests deterministic          |

## API and shared protocol

Sentia uses two local transport boundaries:

- **React webview to extension:** VS Code `postMessage` messages with a small
  allowlist and runtime validation.
- **Extension to FastAPI sidecar:** authenticated HTTP/OpenAPI for requests and
  WebSockets for response tokens, index progress, state transitions, tool
  activity, test results, errors, heartbeats, and cancellation results.

Every WebSocket message uses a common envelope:

```json
{
  "protocolVersion": "1",
  "eventId": "evt_...",
  "sequence": 42,
  "conversationId": "conv_...",
  "runId": "run_...",
  "type": "agent.file_changed",
  "createdAt": "2026-07-25T12:00:00Z",
  "payload": {}
}
```

### Protocol packages

| Package                                | Primary use                                                             |
| -------------------------------------- | ----------------------------------------------------------------------- |
| `openapi-typescript`                   | Generate TypeScript HTTP contract types from FastAPI's OpenAPI document |
| `zod`                                  | Runtime validation of asynchronous events at the TypeScript boundary    |
| Pydantic, built into the sidecar stack | Authoritative Python event models and runtime validation                |

WebSocket event types are defined in Pydantic and exported as JSON Schema. A
small repository script generates corresponding Zod-compatible TypeScript
definitions. Generated files are checked into source control so protocol drift
is visible in pull requests.

## Repository indexing and retrieval

The first indexing implementation intentionally favors transparent local tools
over a vector database.

| Technology or package    | Primary use                                                                 |
| ------------------------ | --------------------------------------------------------------------------- |
| Git CLI                  | Tracked-file inventory, branch and dirty state, diffs, and change ownership |
| ripgrep (`rg`)           | Fast content search and file discovery                                      |
| Python `pathlib`         | Normalized path handling and workspace-boundary enforcement                 |
| `pathspec`               | Ignore-rule evaluation                                                      |
| SQLite                   | Metadata, extracted chunks, hashes, and summary cache                       |
| SQLite FTS5              | Lexical retrieval with path, language, and symbol filters                   |
| VS Code document symbols | High-quality symbol ranges when a language service is active                |

### Persistent project-structure snapshot

When the extension attaches to a trusted local workspace, the sidecar writes an
atomic `.sentia/project-structure.json` file. It contains the workspace name,
`created_at`, `updated_at`, sorted directory and file paths, and a compact
per-file metadata manifest containing role, size, priority, imports, and
top-level symbols. All entries pass Sentia's ignore, dependency,
runtime-artifact, sensitive-path, and symlink rules.

`watchfiles` observes the workspace while the sidecar is active. File and
directory create, modify, and delete events rebuild the snapshot and advance
`updated_at` whenever cached file metadata changes. Reattaching compares the
live filesystem with the stored JSON, so changes made while Sentia was stopped
are also captured. The `.sentia` directory is excluded from its own watcher to
prevent feedback loops. While attached, the validated repository manifest is
also held in sidecar memory for immediate question routing. Sentia falls back
to a fresh filesystem scan if the JSON is missing, invalid, or does not match
the active workspace.

### Deferred retrieval packages

These are not baseline MVP dependencies. They should be added only after the
grounding evaluation demonstrates a need:

| Package or service          | Possible later use                                                            |
| --------------------------- | ----------------------------------------------------------------------------- |
| `tree-sitter`               | Language-aware syntax trees and reliable symbol boundaries outside the editor |
| `tree-sitter-language-pack` | Prebuilt parsers for multiple repository languages                            |
| `sqlite-vec`                | Local vector similarity search without operating a separate database          |
| Embedding API               | Semantic retrieval when lexical and structural retrieval are insufficient     |

## AI responsibilities

Sentia deliberately separates read-only AI from tool-using coding sessions.

### Provider-neutral read-only intelligence

The `anthropic` package and OpenAI Codex SDK can both be used for:

- Repository, directory, file, and symbol summaries
- Evidence-grounded questions and answers
- Plain-language code explanations
- “What needs to be built?” analysis
- Structured implementation plans
- Short spoken versions of verified responses and events

Both paths receive selected repository evidence but no file-write permission.
Responses that describe repository facts must return structured evidence ranges
that the sidecar validates. The extension includes the user's selected provider
with every repository question; a `RepositoryIntelligenceRouter` dispatches to
`AnthropicRepositoryService` or `CodexRepositoryService`.

Repository questions share one evidence contract but use provider-appropriate
investigation loops:

1. Claude receives a compact summary of the cached snapshot and autonomously
   searches its paths and symbol outlines through a custom Agent SDK tool.
2. Claude reads the smallest useful set through a second custom tool that
   preserves ignore rules, path boundaries, secret redaction, evidence budgets,
   and exact line numbers. Built-in filesystem, shell, network, and editing tools
   are unavailable to this question-answering agent.
3. Claude returns one structured answer from the same agent loop. Codex retains
   its typed shortlist, bounded local read, and grounded synthesis turns.
4. Both paths return evidence ranges that the sidecar validates before display.

This flow does not use LangGraph. Repository access remains local deterministic
code exposed through a narrow tool boundary. LangGraph should be reconsidered
when Sentia has parallel specialist agents, resumable checkpoints, branching
tool loops, or durable human-approval nodes.

### Claude Agent SDK and OpenAI Codex SDK: coding execution

The `claude-agent-sdk` and `openai-codex` packages provide two interchangeable
coding runtimes. Claude supplies typed message blocks and tool configuration;
Codex supplies threads, turns, JSON-RPC notifications, sandbox presets, and
approval modes.

Sentia wraps both behind its own `CodingAgentAdapter`, `AgentRunRequest`, and
`AgentEvent` contract. Provider-specific SDK objects stop at the adapter
boundary, so provider selection does not rewrite the UI or state machine. SDK
and bundled CLI versions are locked and tested because their event formats and
supported capabilities evolve.

Read-only runs are the default. Claude receives only repository-reading tools;
Codex receives a read-only sandbox. Workspace-write is an explicit capability
reserved for an approved implementation plan. See
`docs/decisions/0003-dual-agent-sdk-runtime.md` for the boundary and rationale.

The sidecar must never expose hidden chain-of-thought. It converts observable
tool calls, file events, commands, and verification results into concise status
updates.

## Voice stack

### Input: Deepgram Flux

The React webview captures microphone audio as containerized WebM/Opus and emits
approximately 80 ms chunks. The allowlisted bridge forwards those chunks to an
extension-owned, authenticated sidecar WebSocket. The sidecar owns the Deepgram
credential and connects to `/v2/listen` with `flux-general-en` or
`flux-general-multi`; it never exposes a provider key to the webview.

Before connecting, the sidecar derives at most 100 keyterms from cached file and
symbol outlines, prioritizing the active file. `TurnInfo` events remain typed
through the extension boundary. A deterministic correction layer compares small
transcript spans with repository spellings, applies only high-confidence active
context matches, and preserves medium-confidence candidates as metadata.

Flux endpoints are configurable for global, EU, AU, dedicated, and self-hosted
deployments. Language hints are sent only with the multilingual model. Because
the input is containerized, Sentia omits `encoding` and `sample_rate`.

Typed input remains fully supported, and Sentia does not persist microphone
audio or transcripts separately from the submitted conversation.

### Output: local text-to-speech

Voice-initiated answers use the webview's platform speech synthesis. A
normalization layer removes Markdown formatting, replaces code blocks with a
short on-screen cue, and splits common code identifiers into natural speech.
Starting a new microphone turn cancels playback immediately for click-to-barge-in.

Streaming ElevenLabs, Cartesia, or another provider can replace this adapter
after the repository-answer path itself emits streaming text.

## Required external applications and commands

| Dependency                     | Required?                                | Purpose                                                                                          |
| ------------------------------ | ---------------------------------------- | ------------------------------------------------------------------------------------------------ |
| VS Code stable                 | Yes for the primary MVP                  | Hosts the extension, React sidebar, editor context, navigation, and highlights                   |
| Cursor                         | Compatibility target                     | May host the extension after it passes the stable VS Code implementation and compatibility suite |
| Git                            | Yes                                      | Repository metadata and change tracking                                                          |
| ripgrep (`rg`)                 | Yes initially                            | Fast local discovery and lexical evidence gathering                                              |
| Python 3.12+                   | Yes during development                   | Runs the FastAPI sidecar                                                                         |
| Node.js LTS                    | Yes during development                   | Builds the React webview and VS Code extension                                                   |
| `pnpm`                         | Yes during development                   | JavaScript workspace and package management                                                      |
| `uv`                           | Yes during development                   | Python dependency management, virtual environments, locking, and task execution                  |
| Deepgram API key               | Required for built-in voice input        | Authenticates Flux streaming and remains in VS Code SecretStorage                                |
| Claude or Codex authentication | At least one is required for AI features | Authorizes the selected repository-intelligence and coding-agent provider                        |

Distribution should package the Python sidecar and `rg`, removing the need
for end users to install the development runtimes themselves.

## Third-party APIs and credentials

### Required for the full MVP

| Service       | Credential or account                                         | Used for                                                                            | Data sent                                                                                             |
| ------------- | ------------------------------------------------------------- | ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Anthropic API | `ANTHROPIC_API_KEY` or a supported Claude authentication flow | Claude-selected summaries, explanations, plans, narration, and approved coding runs | User prompt, selected repository evidence, approved task context, and tool results needed for the run |
| OpenAI Codex  | Local ChatGPT login or Codex API-key login                    | Codex-selected summaries, explanations, plans, narration, and approved coding runs  | User prompt, selected repository evidence, approved task context, and tool results needed for the run |
| Deepgram Flux | Deepgram API key                                              | Built-in turn-aware voice transcription                                             | Ephemeral microphone audio and selected repository keyterms                                           |

The exact authentication paths for packaged distribution must be finalized
before release. Development can use an Anthropic API key stored outside the
repository or the Codex SDK's existing local login. Sentia must never write
credentials into SQLite, logs, the webview, or an agent prompt.

### Not required

| Service                 | Reason no API is required                                                             |
| ----------------------- | ------------------------------------------------------------------------------------- |
| GitHub API              | The MVP works with a local Git repository and does not create issues or pull requests |
| Cloud database          | SQLite stores MVP state inside the local sidecar data directory                       |
| Vector database         | SQLite FTS5 handles initial retrieval                                                 |
| Text-to-speech API      | Local platform speech supplies initial voice output                                   |
| Authentication provider | The local single-user MVP has no Sentia accounts                                      |
| Telemetry service       | Structured diagnostics remain local by default                                        |

### Optional post-MVP APIs

| API                  | Possible use                                                           | Adoption condition                                            |
| -------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------- |
| GitHub API           | Issues, pull requests, review context, and publishing approved changes | Only after local coding workflow is reliable                  |
| Embeddings API       | Semantic code retrieval                                                | Only if measured retrieval quality remains inadequate         |
| Premium TTS API      | Higher-quality and consistent cross-platform voices                    | User opt-in with a clear data and cost disclosure             |
| Error monitoring API | Opt-in crash and performance reporting                                 | Only with redaction, consent, and a documented privacy policy |

## Security packages and boundaries

Packages support security, but the primary protection comes from architectural
boundaries:

- The webview cannot access the filesystem directly.
- The extension exposes a small typed command allowlist.
- The sidecar validates all paths after resolving symlinks.
- `pathspec` excludes ignored files before indexing.
- `detect-secrets` supplements explicit exclusions and redaction patterns.
- Read-only model calls have no editing or command tools.
- Coding tools exist only inside an approved agent run.
- Claude hooks and the Sentia permission broker evaluate tool use before it runs.
- Run-owned diffs are separated from pre-existing workspace changes.
- The sidecar binds to loopback and requires a short-lived extension token.
- The webview never receives the sidecar port or authentication token.

## Development and package management

JavaScript dependencies use a `pnpm-lock.yaml`; Python dependencies use a
`uv.lock`. Both lockfiles are committed. Runtime versions are declared in the
repository rather than relying on whichever global versions happen to exist.

Expected root commands are:

```text
pnpm install          install JavaScript workspace packages
uv sync               install locked Python packages
pnpm dev              launch the Extension Development Host, webview watcher, and sidecar
pnpm lint             run editor UI, extension, and sidecar linting
pnpm typecheck        run TypeScript and Python type checks
pnpm test             run unit and protocol tests
pnpm test:e2e         run VS Code integration and critical journey tests
pnpm build            build the webview, extension, and packaged sidecar
```

These commands describe the intended developer contract. They will become
executable as the Phase 0 and Phase 1 scaffolding tasks are implemented.

## Testing stack

| Test layer                       | Tools                                                         | Coverage                                                                          |
| -------------------------------- | ------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| React unit/component             | Vitest, jsdom, Testing Library                                | Rendering, state transitions, approvals, events, and accessibility behavior       |
| Extension integration            | `@vscode/test-cli`, `@vscode/test-electron`                   | Workspace context, navigation, highlights, trust state, and webview bridge        |
| FastAPI sidecar unit/integration | pytest, pytest-asyncio, HTTPX                                 | API, WebSockets, persistence, indexer, permissions, and recovery                  |
| AI contract                      | pytest fixtures and mocked Anthropic responses                | Citation validation, unsupported questions, prompt injection, and event narration |
| Agent contract                   | Fake `CodingAgentAdapter` plus controlled Claude fixture runs | Plans, approvals, permissions, events, cancellation, diffs, and verification      |
| End-to-end                       | VS Code Extension Development Host                            | Complete “bring your codebase to life” acceptance journey                         |

External model calls are mocked in normal CI. A separate opt-in integration job
may use real credentials and a strict cost limit.

## Deliberate exclusions

The following technologies are intentionally absent from the MVP stack:

- Django, because the local service does not need templates or an admin site
- Next.js, because the React surface is an editor webview
- Redux, because Sentia's client-owned state is small and Zustand is sufficient
- Socket.IO, because native WebSockets and a controlled protocol are sufficient
- PostgreSQL, because the MVP is single-user and local
- Redis or a queue service, because only one agent run is active per workspace
- Docker as an end-user requirement, because the editor needs direct local
  workspace access
- A cloud vector database, because local FTS should be evaluated first
- Electron or Tauri, because VS Code already supplies the application host

## Official references

- [React](https://react.dev/)
- [Vite](https://vite.dev/)
- [VS Code Extension API](https://code.visualstudio.com/api/)
- [VS Code extension testing](https://code.visualstudio.com/api/working-with-extensions/testing-extension)
- [FastAPI](https://fastapi.tiangolo.com/)
- [FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/)
- [SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python)
- [Claude Agent SDK for Python](https://github.com/anthropics/claude-agent-sdk-python)
- [OpenAI Codex SDK](https://developers.openai.com/codex/sdk/)
- [Deepgram Flux quickstart](https://developers.deepgram.com/docs/flux/quickstart)
- [Deepgram Flux WebSocket reference](https://developers.deepgram.com/reference/speech-to-text/listen-flux)
