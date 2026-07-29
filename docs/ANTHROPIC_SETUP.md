# Anthropic setup for Sentia

Sentia's repository-understanding path uses the Claude Agent SDK with Claude
Haiku 4.5. Authentication is bring-your-own-key: the person running Sentia owns
the Anthropic Console account and pays that account's API usage.

## What Sentia requires from you

For live AI testing, provide the following on your own machine:

1. An Anthropic Console account.
2. Active Anthropic API billing or credits.
3. One Anthropic API key created under **Settings → API keys**.
4. A trusted local VS Code workspace containing a Python or
   JavaScript/TypeScript project.
5. Permission for Sentia to send selected, filtered excerpts from that workspace
   to the Anthropic API when you submit a question.

Do **not** send the key to another person, paste it into a chat, commit it to the
repository, or add it to `.env`. Sentia asks for it through a masked native VS
Code input and stores it using VS Code `SecretStorage`.

You do not need:

- A Sentia account
- A hosted Sentia backend
- A Claude Pro or Max subscription
- `claude login`
- A Claude.ai password, cookie, or OAuth session
- Wispr Flow for typed questions
- A separate global Claude Code installation when the pinned Agent SDK runtime
  is available through Sentia's Python environment

## Connect the key

1. Open [Anthropic Console](https://console.anthropic.com/) and create an API
   key.
2. Start Sentia in the VS Code Extension Development Host.
3. Open the Sentia sidebar.
4. Select **Connect API key**, or run **Sentia: Connect Anthropic** from the
   Command Palette.
5. Paste the key into the masked VS Code prompt.
6. Wait for the state to change to **Anthropic connected**.

Sentia validates a newly entered key with Anthropic before saving it. On later
starts, the extension restores it from SecretStorage without putting it in the
React webview, logs, SQLite, command arguments, or workspace settings.

Run **Sentia: Disconnect Anthropic** to delete the saved key and clear it from
the running sidecar.

## First acceptance test

Open one representative Python repository and one representative
JavaScript/TypeScript repository. For each repository:

1. Confirm VS Code marks the workspace as trusted.
2. Ask `What is this codebase about?`.
3. Confirm the answer describes the project purpose, important components, and
   entry points without claiming unsupported details.
4. Select every evidence chip and confirm it opens the expected file and
   highlights the expected lines.
5. Confirm `.env`, private-key files, `node_modules`, `.venv`, build outputs, and
   paths excluded by `.gitignore` or `.sentiaignore` do not appear as evidence.

Each question starts one bounded, read-only Agent SDK investigation. Claude
receives only a compact summary of the in-memory project snapshot, then uses a
custom search tool over cached eligible paths, roles, imports, and top-level
symbols. A second custom tool reads at most eight selected files through
Sentia's local path validation, secret redaction, 15-second deadline, and
12,000-character evidence budget. README is not mandatory. Python outline mode
sends signatures and docstrings without function bodies; full mode is used when
the requested function behavior requires implementation evidence.

Git-ignored, `.sentiaignore`-excluded, sensitive, generated, dependency, cache,
and lockfile paths never enter the tool's eligible snapshot. Nested `env`,
`venv`, `.venv`, `site-packages`, `node_modules`, and vendored dependency trees
are hard scanner boundaries even when a repository forgot to ignore them.
Built-in filesystem, shell, network, and editing tools are disabled for
repository questions. The sidebar reports the Agent SDK run's input and output
token usage.

## Configuration

The default model is pinned for repeatability:

```text
claude-haiku-4-5-20251001
```

During sidecar development it can be changed with:

```text
SENTIA_ANTHROPIC_MODEL=another-supported-model-id
```

No API key belongs in that environment file; the extension supplies it from
SecretStorage.

## Connection failures

- **Anthropic rejected this API key:** create a new Console API key and reconnect.
- **Required access:** check the key's workspace and permissions in Anthropic
  Console.
- **Billing or credits:** enable API billing or add credits to the Console
  account.
- **Could not reach Anthropic:** check network, proxy, VPN, or firewall settings.
- **Rate limited:** wait briefly and retry.

Sentia intentionally reports these categories without logging the key or the
full provider response.

## Diagnostics and token accounting

When a request fails, select **Open Sentia Diagnostics** in the error card or
run **Sentia: Open Diagnostics** from the Command Palette. Each repository
question has a `sentia_...` diagnostic ID shared by the UI error and JSON log
events. The log records both Anthropic requests and the local content-reader
stage, including HTTP status, Anthropic request and message IDs when available,
actual input/output tokens, model-selected and expanded file paths, read modes,
cache source, content-reader duration, context sizes, stop reasons, and
field-level validation errors.

Diagnostics never record the API key, question text, repository file contents,
or model answer text. They record lengths and paths so request construction can
be debugged without copying source code into logs.
