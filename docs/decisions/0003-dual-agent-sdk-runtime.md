# ADR 0003: Dual coding-agent SDK runtime

- Status: Accepted
- Date: 2026-07-27

## Context

Sentia needs an autonomous coding layer, but it should not be coupled to one
vendor's session, event, permission, or authentication types. The product also
has a deliberately bounded read-only repository-question path whose cached
snapshot keeps evidence and token use controlled.

The two supported coding runtimes are the Anthropic Claude Agent SDK for Python
and the OpenAI Codex SDK for Python. Their execution models differ: Claude
streams typed message blocks and configures tools through `ClaudeAgentOptions`;
Codex owns threads and turns, streams JSON-RPC notifications, and applies
sandbox and approval policies when a thread or turn starts.

## Decision

Keep two explicit responsibilities:

1. `RepositoryIntelligence` is the read-only brain. Claude uses one Agent SDK
   investigation loop with custom cached-snapshot search and sanitized-read
   tools. Codex uses read-only SDK turns around the same local evidence boundary.
2. `CodingAgentAdapter` is the agentic hands. Both Claude Agent SDK and OpenAI
   Codex SDK implement the same Sentia-owned run request and event contract.

The shared contract normalizes provider and run identity, access, session
start, visible assistant text, tool activity, usage, and terminal results.
Sentia never forwards hidden thinking blocks. Provider payloads do not cross
the adapter boundary except for small allowlisted metadata such as a file path,
turn count, status, or cost.

Read-only is the default. Claude repository questions receive only Sentia's
custom snapshot search and read tools in `dontAsk` mode; built-in filesystem,
shell, network, and editing tools are denied. General Claude coding-agent runs
receive `Read`, `Glob`, and `Grep` in plan mode. Codex uses the read-only sandbox
and denies approvals.
Workspace-write runs must be requested explicitly and will ultimately be gated
by Sentia's approved-plan and permission broker.

Claude's `allowed_tools` option is an approval rule, not a tool-surface
allowlist. In particular, a bare `Bash` entry approves every shell command.
Sentia therefore leaves Bash visible to the runtime without pre-approving it;
non-read-only commands must pass through the future permission broker.

## Authentication

- Claude receives the Anthropic API key already held in VS Code SecretStorage;
  the adapter passes it only in the SDK child process environment. A standalone
  development run may use `ANTHROPIC_API_KEY`. Sentia does not offer or reuse a
  user's claude.ai login or subscription rate limits.
- Codex reuses the local Codex authentication cache. The user signs in with
  `codex login` using either ChatGPT or an OpenAI API key; Sentia does not copy
  `~/.codex/auth.json` or ask the user to paste that file.

## Consequences

- Provider selection can change without changing the UI state machine.
- The sidecar retains evidence-validated Q&A while allowing Claude to recover
  from weak one-shot file selection through a bounded agentic search loop.
- Event mapping, cancellation, permission brokerage, and provider conformance
  tests become first-class product code.
- Supporting two beta SDKs increases compatibility testing and package size.
  Both SDK versions and their bundled CLI runtimes must be locked.

The current Claude integration was verified against `claude-agent-sdk` 0.2.128
and its bundled Claude Code 2.1.220. See
[`../CLAUDE_AGENT_SDK_AUDIT.md`](../CLAUDE_AGENT_SDK_AUDIT.md) for the feature
and documentation audit.

## Follow-up

The current slice provides the shared types, router, both SDK adapters, a
read-only-by-default smoke-test CLI, dependency locks, and contract tests. The
durable run manager, approval UI, live event transport, cancellation controls,
and editor-follow behavior remain Phase 5 work.
