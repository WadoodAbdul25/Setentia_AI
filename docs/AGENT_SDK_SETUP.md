# Coding-agent SDK setup

Sentia supports the Claude Agent SDK and OpenAI Codex SDK behind one local
adapter. Do not paste keys into source files, `.env` files inside a codebase, or
chat messages.

The provider selected in the Sentia sidebar is used for both repository
questions and coding-agent runs. Both repository-question adapters use bounded,
read-only selection and answer turns around Sentia's cached, ignore-aware
manifest and local evidence reader. Claude uses two Claude Agent SDK queries;
Codex uses two ephemeral Codex SDK threads.

## Install the Python environment

Choose one workflow.

### uv

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install uv
uv sync --all-groups
```

### pip

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

The lock currently resolves `claude-agent-sdk==0.2.128` and
`openai-codex==0.144.4`. The Codex wheel includes its pinned local Codex CLI
runtime; the Claude Agent SDK wheel includes or locates a compatible Claude
Code runtime on supported platforms.

## Claude authentication

For development, use either an Anthropic Console API key with active billing or
an existing Claude Code login. The explicit API-key path is easiest to
diagnose:

```bash
export ANTHROPIC_API_KEY="your-key" # pragma: allowlist secret
```

The VS Code extension's existing **Sentia: Connect Anthropic** command stores
the key in VS Code SecretStorage. When the extension starts an agent run, the
sidecar can pass that in-memory key only to the Claude SDK child process. The
standalone `sentia-agent` command cannot read VS Code SecretStorage, so it needs
the environment variable or a local Claude login.

Check local Claude authentication without sending a prompt:

```bash
claude auth status
```

## Codex authentication

In the Sentia sidebar, choose **Codex** and select **Connect Codex**. Sentia
reuses a valid local login when one exists. Otherwise, it starts the Codex
SDK-native login attempt and opens OpenAI's authentication page in the browser.
After finishing the browser flow, select **Check connection**.

The equivalent terminal paths are:

```bash
codex login
```

or, for usage-based API billing:

```bash
printenv OPENAI_API_KEY | codex login --with-api-key
```

Then verify it:

```bash
codex login status
```

Prefer ChatGPT login for personal interactive work when your plan includes
Codex. Prefer API-key login for non-interactive automation where usage-based
billing and API organization controls are intended. Do not give Sentia your
ChatGPT password or copy the Codex auth cache into the repository.

## Safe smoke tests

Both commands are read-only unless `--write` is explicitly present:

```bash
.venv/bin/sentia-agent claude \
  "Explain this repository in three concise bullets." \
  --workspace . --max-turns 4 --max-budget-usd 0.25

.venv/bin/sentia-agent codex \
  "Explain this repository in three concise bullets." \
  --workspace .
```

Events are emitted as JSON Lines so the same stream can later drive the VS Code
state journey and diagnostics. Hidden model reasoning is intentionally omitted.
Claude supports the shown per-run turn and dollar caps. The current Codex Python
SDK does not expose equivalent per-run arguments, so Sentia rejects those flags
for Codex instead of pretending to enforce them; configure account spend limits
for that provider.

`--write` is a developer-only escape hatch at this stage. Do not use it on an
important workspace until the Phase 5 plan approval and permission broker are
connected to the runtime.

## What Sentia still needs from you

1. Authenticate Claude with either `ANTHROPIC_API_KEY` or Claude Code login.
2. Authenticate Codex with `codex login`; no OpenAI key is needed when using a
   supported ChatGPT subscription login.
3. Decide which provider should be the default after comparing the two
   read-only smoke tests. Sentia will retain both providers either way.
4. Set a comfortable Claude per-run dollar cap for development. The initial
   recommendation is `$0.25` for repository explanation experiments, then tune
   it from observed usage.

No credential values are required from you in this repository or in chat.
