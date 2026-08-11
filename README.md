# Sentia

> Bring your codebase to life.

Sentia is an **editor-native**, local-first AI agent layer that lets a software
repository speak for itself. A developer can talk to the repository, ask for
plain-language explanations, brainstorm changes, and authorize an AI coding
agent to implement work without leaving the code editor. While work is running,
Sentia opens the relevant code, highlights the sections under discussion, and
reports verified progress in a conversational voice.

Sentia is delivered as a VS Code extension. It coordinates three capabilities:

- **Deepgram Flux or OpenAI Voice** provides streaming voice input and natural,
  AI-generated speech output; the user can switch providers in the composer.
- **The Sentia editor extension** hosts the interface, understands editor
  context, and controls the workflow.
- **Claude Code or Codex** inspects, edits, and tests the code after the user
  approves a plan.

React is used to render Sentia's embedded editor panel. It does not make Sentia
a website. FastAPI runs as a private local sidecar process supervised by the
extension; it is not a hosted web backend.

## Product form factor

The MVP is an installable VS Code extension containing an embedded React
webview. The extension starts a local FastAPI sidecar when needed and stops it
when Sentia shuts down. No browser tab, public URL, hosted frontend, Sentia user
account, or remote application server is required.

The responsibilities are deliberately separated:

- **VS Code extension host:** product entry point, editor context, workspace
  trust, file navigation, highlighting, local process lifecycle, and secure
  message bridging.
- **React webview:** conversation, evidence, plans, approvals, live progress,
  voice controls, and diff review inside the editor sidebar.
- **FastAPI sidecar:** local repository index, authenticated Flux streaming,
  AI calls, persistence, permissions, and coding-agent orchestration.

VS Code's stable Extension API is the primary target. Cursor is a compatibility
target after the extension works correctly in VS Code; the MVP does not depend
on undocumented Cursor APIs.

## Product experience

Sentia speaks in four clearly separated modes:

1. **What I am** — an evidence-backed description of the repository's purpose,
   architecture, packages, and important flows.
2. **How I work** — simple explanations of selected lines, functions, files, and
   relationships, with file and line references.
3. **What I need** — missing implementations, failing tests, TODOs, and proposed
   improvements, clearly labeled as observations or inferences.
4. **What I am doing** — live, verified updates from the coding agent, including
   files being inspected, edits proposed, commands running, and test results.

The codebase persona must never present a guess as repository fact. Claims about
the code should be linked to file paths and line ranges, and claims about active
work should come from actual agent events.

## MVP scope

The MVP supports one local repository and one active coding run at a time. It
will:

- Index a repository locally without uploading the complete project to a Sentia
  service.
- Answer architecture and implementation questions with code references.
- Explain the user's current selection in plain language.
- Open files, reveal ranges, and highlight relevant code in VS Code-compatible
  editors.
- Capture microphone audio through a native macOS helper and transcribe it
  through Deepgram Flux or OpenAI live transcription with repository-aware
  vocabulary and spelling correction.
- Keep detailed visual answers separate from faithful, second-stage AI speech
  renderings synthesized through the selected voice provider.
- Turn a discussion into a plan and require approval before coding begins.
- Run Claude Code through a controlled adapter and stream structured progress.
- Show changed files, diffs, commands, and test results.
- Stop an active run and preserve an audit trail.

The MVP will not include a standalone website, browser-based product, autonomous
unattended development, cloud repository hosting, team accounts, billing,
mobile clients, or parallel coding agents.

## Architecture

```text
VS Code / Cursor-compatible editor
    |
    |-- Sentia extension host
    |     |-- active editor context
    |     |-- open, reveal, and highlight
    |     |-- sidecar lifecycle and authenticated transport
    |     `-- restricted postMessage bridge
    |
    |-- React webview
    |     |-- chat, approvals, task state, voice controls, diffs
    |     `-- microphone capture and local spoken-answer playback
    |
    `-- supervised FastAPI local sidecar
          |-- conversation and repository persona
          |-- repository index and retrieval
          |-- Deepgram Flux gateway and terminology correction
          |-- task and approval state
          |-- event stream and audit log
          `-- coding-agent adapter
                  |
                  v
              Claude Code
```

The extension is the product shell and security boundary. The React webview is
an embedded interface, not a separately deployed frontend. The supervised
FastAPI process binds only to the local loopback interface and communicates with
the extension through authenticated HTTP and WebSockets. A browser-based UI
harness is allowed only as a development and component-testing aid.

## Technology stack

### Editor UI

- React
- TypeScript
- Vite
- Zustand for small, explicit client-side state
- TanStack Query for request state obtained through the extension bridge
- VS Code `postMessage` for communication with the extension host
- VS Code theme variables and Codicons for editor-native styling

### Local sidecar

- Python 3.12+
- FastAPI
- Pydantic
- Uvicorn
- SQLAlchemy with SQLite for MVP persistence
- WebSockets for conversation and agent events
- Claude Agent SDK for structured coding sessions and events

FastAPI is preferred over Django for the MVP because the sidecar primarily needs
streaming APIs, typed event contracts, local process orchestration, and a small
amount of persistence. It is packaged and supervised as part of the editor
extension experience rather than deployed as an independent website backend.

### Repository intelligence

- Git and ripgrep for discovery and evidence gathering
- Language-server/document-symbol data where the editor provides it
- SQLite FTS for initial text retrieval
- Hierarchical summaries at repository, directory, file, and symbol levels
- Optional Tree-sitter and embeddings after baseline retrieval is measured

### AI and agent execution

- Provider-neutral repository-intelligence and coding-agent interfaces
- Claude Agent SDK and OpenAI Codex SDK adapters behind one normalized event contract
- Structured streaming events instead of terminal-output scraping
- Model-generated explanations grounded with deterministic repository evidence
- Explicit plan, approval, execution, verification, and review states

## Core workflow

```text
Brainstorm
  -> inspect repository evidence
  -> propose a plan
  -> request user approval
  -> prompt the coding agent with approved context
  -> run the coding agent
  -> narrate verified events
  -> show and explain the diff
  -> run checks
  -> present the result for acceptance
```

Brainstorming and explanation are read-only by default. Sentia may not edit
files, run shell commands, or install packages until the user has deliberately
approved the relevant action.

## Repository layout

The intended project structure is:

```text
sentia/
|-- apps/
|   |-- extension/          # Editor-native product host
|   |-- webview/            # React UI embedded in the editor
|   `-- sidecar/            # Supervised local FastAPI process
|-- packages/
|   |-- protocol/           # Shared event schemas and generated TS types
|   `-- editor-client/      # Extension-to-service client
|-- tests/
|   |-- fixtures/           # Small sample repositories
|   `-- e2e/
|-- docs/
`-- sentia_MVP.md
```

## Safety and trust principles

- Read-only behavior is the default.
- Every edit and command is attributable to an approved task.
- `.gitignore`, Sentia ignore rules, binary detection, and secret filtering are
  applied before indexing.
- Environment files, credentials, private keys, and known secret patterns are
  excluded from prompts and logs.
- The UI displays a visible journey through Brainstorm, Plan mode, Prompting,
  Implementing, Testing, and Review, with separate starting, indexing, waiting
  for approval, failed, and cancelled indicators.
- Users can stop speech and active coding runs independently.
- Changes remain visible as a Git diff and are never silently committed.

## MVP definition of done

The MVP is complete when a developer can open a sample repository, ask “What
are you?”, receive a grounded spoken answer, select a function and request a
line-by-line explanation, brainstorm a small feature, approve the resulting
plan, watch Claude Code implement it while Sentia opens and highlights relevant
code, and review the final diff and test results.

See [sentia_MVP.md](sentia_MVP.md) for the phased implementation backlog and
[TECH_STACK.md](TECH_STACK.md) for the technology, package, and external-service
decisions. See [docs/LATENCY.md](docs/LATENCY.md) for the measured fast-path,
repository-memory, and streaming strategy.

## Current status

Phase 0, Phase 1, and the first Phase 2–3 repository-understanding slice are present:

- Product contract, state machine, and editor-native architecture decision
- TypeScript workspace and versioned shared protocol
- VS Code extension host and restricted webview bridge
- Supervised sidecar lifecycle with random loopback port and short-lived token
- Embedded React connection-state interface
- FastAPI health, version, state, conversation, and replayable WebSocket APIs
- SQLite persistence and an initial Alembic migration
- JavaScript and Python linting, type checks, tests, builds, and CI
- Anthropic bring-your-own-key connection through VS Code SecretStorage
- Provider-selected Claude or Codex read-only repository explanations
- Snapshot-backed repository questions: an agentic Claude search/read loop or
  Codex shortlist/read/synthesis flow, both producing evidence-grounded answers
- Per-answer selected-file and combined token-usage visibility
- Automatic `.sentia/project-structure.json` metadata snapshots when Sentia
  attaches to a trusted workspace, held in sidecar memory and refreshed by file
  create, modify, and delete triggers
- Python and JavaScript/TypeScript file inventory with ignore, size, binary,
  symlink, and potential-secret filtering
- Evidence-backed answers with clickable editor highlights
- Deepgram Flux `/v2/listen` voice input using native 16 kHz linear PCM
- Active-file-weighted repository vocabulary, dynamic keyterms, and conservative
  transcript correction metadata
- Voice-initiated question submission, a second AI pass that derives speech from
  the validated display answer, streaming Deepgram Flux `/v2/speak` synthesis,
  native 24 kHz playback, and click-to-barge-in cancellation

The production bundles and sidecar process-level smoke test pass. Launching the
Extension Development Host remains a manual validation step because it requires
VS Code and its `code` command.

## Development quick start

Requirements: Node.js 22+, pnpm 10, Python 3.12+, uv, and VS Code stable.
Voice capture and playback currently require macOS plus the Xcode Command Line
Tools so the native audio helpers can be compiled. The non-voice repository
experience can still be built and tested on Linux.

```bash
corepack enable
corepack prepare pnpm@10.15.1 --activate
pnpm install
uv sync
pnpm verify
pnpm build
```

The VS Code `F5` build task uses `npm run build`, so it works after dependencies
are installed even when `pnpm` is not currently exposed on the shell `PATH`.
pnpm remains the repository's package manager for installation and full
workspace verification.

For a traditional Python `venv` and pip workflow, use the pinned runtime
dependencies in `requirements.txt`:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On Windows, activate it with `.venv\Scripts\activate` instead. The `.venv`
directory is intentionally excluded from Git because virtual environments are
machine-specific; `requirements.txt`, `pyproject.toml`, and `uv.lock` are the
reproducible dependency records.

Open the repository in VS Code and press `F5` using the **Run Sentia Extension**
launch configuration. The extension uses `.venv/bin/sentia-sidecar` during local
development and starts it automatically. The development launch disables other
installed extensions inside the Extension Development Host so their logs,
network requests, and deprecation warnings are not mistaken for Sentia output.
It opens this repository and the Sentia sidebar automatically, allowing Sentia
to explain its own codebase. Inside the development host, **Sentia: Open** is
available from the Command Palette; the **Run Sentia Extension** launch command
remains in the original development window because it owns the debugger.

On macOS, bypass the function keys and launch the complete development
extension directly from the repository:

```bash
npm start
```

This builds Sentia and opens a fresh VS Code Extension Development Host with the
local extension loaded. The launcher reports a setup error if the Python
sidecar or Visual Studio Code installation cannot be found.

Development launches automatically open the **Sentia** output panel in the new
VS Code window. It records local sidecar HTTP requests and responses, streamed
speech-render events, OpenAI TTS request metadata, response status and headers,
audio byte counts, and latency metrics. The shell that runs `npm start` does not
receive extension-host logs because the VS Code CLI opens a detached window.
Reopen the panel at any time with **Sentia: Open Diagnostics**. To include the
actual spoken chunks in these local logs, enable
`sentia.development.logVoicePayloads`; it is disabled by default to avoid
recording repository content.

## Anthropic setup for live answers

You need an Anthropic Console API key with active API billing or credits. A
Claude Free, Pro, or Max subscription is not a replacement for an API key.

1. Create or sign in to an account at the
   [Anthropic Console](https://console.anthropic.com/).
2. Confirm that API billing or credits are available.
3. Create a key from **Settings → API keys**.
4. Start the Extension Development Host with `F5` and open a trusted Python or
   JavaScript/TypeScript workspace.
5. In Sentia, select **Connect API key** and paste the key into VS Code's masked
   input. Do not paste the key into source code, `.env`, chat, or an issue.
6. Ask **“What is this codebase about?”** and use the evidence buttons to open
   and highlight supporting source lines.

The key is stored by VS Code SecretStorage and is sent only to Sentia's private
loopback sidecar in memory. **Sentia never needs your Anthropic password or your
Claude.ai session cookie.** API usage is charged to the Anthropic Console
account that owns the key. See [docs/ANTHROPIC_SETUP.md](docs/ANTHROPIC_SETUP.md)
for connection states, troubleshooting, and the exact information needed from
the developer during testing.

## Deepgram Flux setup

Voice input and spoken answers require a Deepgram API key. Select **Connect voice** in the prompt
composer and paste the key into VS Code's masked input. The extension stores it
in SecretStorage and supplies it only to the private sidecar; the webview never
receives the key or the sidecar token.

Sentia defaults to `flux-general-en` on `wss://api.deepgram.com/v2/listen`.
Use the extension settings to select `flux-general-multi`, add
`sentia.voice.languageHints`, or change `sentia.voice.deepgramEndpoint` to the
EU, AU, dedicated, or self-hosted WebSocket origin. Enter the origin only;
Sentia appends `/v2/listen` and declares its native 16 kHz linear PCM microphone
stream. Sentia then gives the completed, validated display answer to a separate
AI speech-rendering pass. That pass preserves its claims, qualifications, steps,
and technical relationships while removing Markdown, citations, paths, and raw
code notation. Sentia streams only that derived version to Deepgram Flux TTS at
`/v2/speak`; the complete evidence-backed answer remains visible on screen. The default TTS voice is
`flux-bruce-en` and can be changed with `sentia.voice.deepgramTtsModel`. Sentia keeps one Flux TTS
WebSocket open across spoken turns so Deepgram can preserve conversational prosody. If explicit
speed control is required, choose an `aura-2-*` model instead and set
`sentia.voice.deepgramTtsSpeed`; Aura uses `/v1/speak` and does not provide Flux cross-turn context.

## OpenAI Voice setup

Choose **OpenAI** under **Voice provider**, then select **Connect OpenAI** on the
microphone button and paste an OpenAI Platform API key. This key is separate
from a ChatGPT subscription and from the Codex browser login. Sentia stores it
in VS Code SecretStorage; it is never sent to the webview or written to the
repository.

OpenAI mode opens `wss://api.openai.com/v1/realtime?model=gpt-realtime`, then
configures `gpt-live-transcribe` for input transcription of the native 24 kHz PCM stream to
text. Sentia's already-selected coding agent produces the one authoritative
repository answer, and
`gpt-4o-mini-tts` speaks the faithful speech rendering of that same answer.
The full answer and evidence remain visible. The default voice is `marin`; use
`sentia.voice.openaiVoice` to change it. Sentia labels the output as
AI-generated in the interface. OpenAI input is push-to-talk: choose **Stop** to
submit the recording or **Cancel** to discard it without asking the codebase.
Deepgram Flux automatically queues a detected end-of-turn for voice submission
after a two-second safety window; choose **Cancel** to discard it or **Send now**
to submit immediately.

## Coding-agent SDK setup

Sentia now has read-only-by-default adapters for both the Claude Agent SDK and
OpenAI Codex SDK. Repository Q&A is provider-neutral: the selected provider
selects files from Sentia's cached manifest and returns the same grounded
evidence contract. Claude and Codex both perform a bounded manifest-shortlisting
turn, use Sentia's sanitized local content reader, and then run a separate
grounded synthesis turn. The SDK adapters also provide the tool-using coding
layer used after planning and approval.

On first launch, Sentia asks the user to choose **Claude Code** or **Codex** and
remembers the choice in VS Code global state. Claude uses the masked Anthropic
key flow. Codex reuses an existing local login or starts the SDK-native OpenAI
browser login. The user can return to the chooser from the connected state.

Choosing Codex now routes both repository explanations and later coding-agent
runs through Codex. Choosing Claude routes repository explanations through the
Claude Agent SDK and uses the same SDK for later coding runs with a different
permission profile. Both repository paths return the same validated evidence
and token-usage contract.

The SDK dependencies are locked in `uv.lock` and `requirements.txt`. Follow
[docs/AGENT_SDK_SETUP.md](docs/AGENT_SDK_SETUP.md) to authenticate each provider
and run the safe JSON-lines smoke tests. Never paste either provider's
credentials into this repository or chat.
