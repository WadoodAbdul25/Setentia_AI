# Claude Agent SDK verification

- Verified: 2026-07-28
- Installed Python SDK: `claude-agent-sdk==0.2.128`
- Bundled Claude Code runtime: `2.1.220`
- Sentia adapter: `apps/sidecar/src/sentia_sidecar/agent_runtime.py`

This audit compares Sentia's Claude adapter with Anthropic's current official
Agent SDK and Claude Code documentation. Claude Code CLI features are not
automatically Agent SDK features even though the SDK launches the Claude Code
runtime internally.

## Current verdict

Sentia's adapter uses supported Python SDK types and valid options. It correctly
uses `query()` for a bounded, independent coding run, normalizes streamed SDK
messages, hides thinking blocks, limits turns and optional dollar spend, scopes
the working directory, loads project instructions, and separates read-only from
workspace-write access.

The audit found and fixed one unsafe configuration: a bare `"Bash"` value in
`allowed_tools` auto-approved every shell command. Bash is no longer
pre-approved. It remains available to Claude so a later Sentia permission
broker can approve individual commands.

The expanded documentation review also produced three immediate corrections:

- Sentia now explicitly requests the `claude_code` system-prompt preset. Agent
  SDK 0.1.0 and later otherwise use a minimal prompt, while Anthropic recommends
  the preset for an IDE-like coding tool.
- Partial-message output is enabled and normalized into Sentia text events, so
  the UI can render tokens before a complete `AssistantMessage` arrives without
  repeating the final accumulated text.
- Claude agent runs now require an Anthropic API key. Sentia does not fall back
  to a user's claude.ai login or subscription limits; Anthropic requires
  third-party Agent SDK products to use API-key authentication unless separately
  approved.

## Feature matrix

| Topic                    | Official capability                                                                                             | Sentia status                                                             | Decision                                                                                                                        |
| ------------------------ | --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| One-off agent runs       | Python `query()` creates a fresh session by default                                                             | Implemented                                                               | Correct for an approved, bounded coding run                                                                                     |
| Continuous conversation  | `ClaudeSDKClient` reuses one session and supports follow-ups, interrupts, and changing model or permission mode | Not implemented in the coding adapter                                     | Add with the durable run/session manager; repository Q&A currently uses its separate provider-neutral intelligence path         |
| Partial output streaming | `include_partial_messages=True` emits raw `StreamEvent` text deltas before complete assistant messages          | Implemented                                                               | Use for lower perceived latency; avoid rendering the accumulated assistant text twice                                           |
| System prompt            | Agent SDK defaults to a minimal prompt; IDE coding tools should request the `claude_code` preset                | Implemented                                                               | Keep Sentia-specific UI and state rules outside hidden reasoning                                                                |
| Authentication           | Third-party Agent SDK products should use API keys rather than offering claude.ai login or subscription usage   | Implemented                                                               | Key comes from VS Code SecretStorage or `ANTHROPIC_API_KEY`                                                                     |
| Session resume and fork  | Capture `session_id`; use `resume`, `continue_conversation`, or `fork_session`                                  | Not implemented                                                           | Persist workspace/provider session identity before enabling conversational coding                                               |
| External session storage | `SessionStore` mirrors transcript entries and supports cross-host resume                                        | Not needed for the local MVP                                              | Revisit only for hosted or multi-device Sentia                                                                                  |
| Programmatic subagents   | Define `agents={...}` and allow the `Agent` tool                                                                | Not enabled                                                               | Deferred; parallel agents are an MVP non-goal and add context, cost, and coordination work                                      |
| Agent teams              | Experimental CLI feature enabled by `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`                                    | Not enabled                                                               | Do not treat as an SDK option; Anthropic explicitly says teams are not configured directly through SDK options                  |
| Worktrees                | CLI/session isolation feature; SDK can resume a session already associated with a worktree                      | Not managed by Sentia                                                     | Deferred with parallel coding runs                                                                                              |
| Claude Security          | Local Claude Code plugin that runs a multi-agent scan                                                           | Not loaded                                                                | Optional future workflow; it requires a locally installed plugin path and cannot be enabled merely by sending the slash command |
| Fast mode                | Research-preview, higher-cost fast configuration for supported Opus models                                      | Not enabled                                                               | Incompatible with Sentia's current Haiku default; do not turn it on for low-cost repository Q&A                                 |
| Effort                   | `ClaudeAgentOptions.effort` can trade reasoning depth for latency on supported models                           | Not set                                                                   | Consider per-task effort when Sentia exposes model-aware performance controls; Haiku does not use the documented effort levels  |
| Permission modes         | `plan`, `acceptEdits`, `dontAsk`, `default`, `auto`, and `bypassPermissions`                                    | Uses `plan` for read-only and `acceptEdits` for approved workspace writes | Valid. Never use `bypassPermissions`; add `can_use_tool` before interactive command approval                                    |
| User approvals           | `can_use_tool` pauses the agent for command approval or `AskUserQuestion`                                       | Not implemented                                                           | Requires persistent streaming input plus the VS Code approval UI                                                                |
| Structured output        | `output_format` validates a final JSON Schema result and re-prompts on mismatch                                 | Not used by the coding adapter                                            | Use for plans, file selections, diff summaries, and other machine-consumed events—not conversational prose                      |
| File checkpointing       | `enable_file_checkpointing` plus `rewind_files()` restores SDK-tracked edits                                    | Not implemented                                                           | Useful recovery layer, but not a replacement for Git or Sentia's run-owned diff                                                 |
| Cost tracking            | Result messages include call-level usage, model usage, and estimated total cost                                 | Partially implemented                                                     | Sentia emits usage and cost per run; it must accumulate totals across future session turns                                      |
| OpenTelemetry            | CLI child process can export traces, metrics, and logs when explicitly enabled                                  | Not enabled                                                               | Keep local structured logs now; make OTLP opt-in for development or hosted deployments                                          |
| Project context          | `setting_sources=["project"]` loads project `CLAUDE.md`, rules, skills, hooks, and settings                     | Implemented                                                               | Intentional: user and local settings are excluded for a predictable project-scoped run                                          |
| Custom MCP tools         | `mcp_servers` accepts a name-to-server mapping                                                                  | Not used                                                                  | Add only when Sentia needs an in-process tool; the supplied list-shaped example is invalid                                      |
| Plugins                  | SDK accepts local plugin directories in `plugins=[{"type": "local", "path": ...}]`                              | Not used                                                                  | Marketplace installation alone does not pass a plugin into an SDK run                                                           |
| Skills and commands      | Filesystem skills and commands load through setting sources and consume context                                 | Project source enabled                                                    | Let trusted repositories supply them; do not mistake a context filter for a security boundary                                   |
| Tool search              | Defers large MCP/custom-tool definitions and discovers them on demand                                           | Not needed                                                                | Add only after Sentia exposes a large tool catalog                                                                              |
| Task progress            | Current runtimes default to Task tools; legacy `TodoWrite` requires an override                                 | Generic tool events only                                                  | Normalize `TaskCreate` and `TaskUpdate` into Sentia's observable state model later                                              |
| Gateway protocol         | Documents the contract for operators proxying Claude Code inference                                             | Not used                                                                  | Irrelevant to direct Anthropic authentication; needed only if Sentia later supports an enterprise LLM gateway                   |

## Corrections to the supplied examples

1. Agent teams and SDK subagents are different. Prompting `query()` to “spawn
   teammates” is not a programmatic Agent SDK configuration for teams. For SDK
   subagents, pass `agents={...}` and include `"Agent"` in `allowed_tools`.
2. `permission_mode="ask"` is invalid. The manual mode is `"default"`; the
   installed Python SDK accepts `default`, `acceptEdits`, `plan`,
   `bypassPermissions`, `dontAsk`, and `auto`.
3. `mcp_servers=[server_config]` is invalid in Python. Use a mapping such as
   `mcp_servers={"math": server_config}` and approve the specific
   `mcp__math__...` tools.
4. The Claude Security plugin is installed from inside Claude Code with
   `/plugin install claude-security@claude-plugins-official`, then activated.
   An SDK application must also pass the installed plugin's local directory in
   `ClaudeAgentOptions.plugins` before `/claude-security` exists in that run.
5. Fast mode is not a lightweight-model switch. It uses supported Opus models,
   costs more per token, and is unavailable on Haiku and Sonnet. Lower effort is
   a separate latency/quality tradeoff for models that support it.
6. Bare names in `allowed_tools` are auto-approval rules. They do not constrain
   the runtime to only those tools. Pair explicit approvals with deny rules,
   `dontAsk`, `can_use_tool`, or hooks depending on the desired policy.
7. The TypeScript V2 session API page is retained only as documentation for a
   removed preview. Do not build Sentia against it. The supported Python
   continuous-session surface is `ClaudeSDKClient`.
8. Agent SDK 0.1.0 stopped loading Claude Code's full system prompt by default.
   An IDE-like agent must explicitly select the `claude_code` preset.
9. `ResultMessage.usage` excludes subagent usage, while `total_cost_usd` and
   `model_usage` include it. Future multi-agent cost reporting must not sum the
   wrong field.
10. Session forks copy conversation history, not files. Use worktrees or file
    checkpointing when filesystem isolation or rewind is required.

## Recommended next Agent SDK slice

The next useful SDK work is not agent teams. It is a persistent
`ClaudeSDKClient` owned by Sentia's run manager, with:

- one session per connected workspace and provider;
- streamed partial response events for lower perceived latency;
- explicit interrupt and cancellation wiring;
- `can_use_tool` connected to the editor approval UI;
- per-command approvals rather than a bare Bash rule;
- session and context-usage telemetry;
- optional programmatic subagents only for tasks where parallelism justifies
  the additional token cost.

## Official references

- [Python Agent SDK reference](https://code.claude.com/docs/en/agent-sdk/python)
- [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)
- [Agent SDK quickstart](https://code.claude.com/docs/en/agent-sdk/quickstart)
- [Streaming input](https://code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode)
- [Streaming output](https://code.claude.com/docs/en/agent-sdk/streaming-output)
- [Agent loop](https://code.claude.com/docs/en/agent-sdk/agent-loop)
- [Sessions](https://code.claude.com/docs/en/agent-sdk/sessions)
- [Session storage](https://code.claude.com/docs/en/agent-sdk/session-storage)
- [Approvals and user input](https://code.claude.com/docs/en/agent-sdk/user-input)
- [Structured outputs](https://code.claude.com/docs/en/agent-sdk/structured-outputs)
- [File checkpointing](https://code.claude.com/docs/en/agent-sdk/file-checkpointing)
- [Cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking)
- [Observability](https://code.claude.com/docs/en/agent-sdk/observability)
- [Hosting](https://code.claude.com/docs/en/agent-sdk/hosting)
- [Secure deployment](https://code.claude.com/docs/en/agent-sdk/secure-deployment)
- [Migration guide](https://code.claude.com/docs/en/agent-sdk/migration-guide)
- [Use Claude Code features in the SDK](https://code.claude.com/docs/en/agent-sdk/claude-code-features)
- [Subagents in the SDK](https://code.claude.com/docs/en/agent-sdk/subagents)
- [Agent SDK permissions](https://code.claude.com/docs/en/agent-sdk/permissions)
- [Plugins in the SDK](https://code.claude.com/docs/en/agent-sdk/plugins)
- [Agent teams](https://code.claude.com/docs/en/agent-teams)
- [Worktrees](https://code.claude.com/docs/en/worktrees)
- [Claude Security](https://code.claude.com/docs/en/claude-security)
- [Fast mode](https://code.claude.com/docs/en/fast-mode)
- [Gateway protocol](https://code.claude.com/docs/en/llm-gateway-protocol)
