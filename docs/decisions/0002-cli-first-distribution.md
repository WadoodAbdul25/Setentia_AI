# ADR 0002: CLI-first distribution with an optional editor companion

- Status: Accepted direction
- Date: 2026-07-26

## Context

The VS Code Extension Development Host is useful for building editor navigation
and highlighting, but it is not the simplest way for a developer to start a
conversation with a repository. Sentia should eventually be installable once
and runnable from any local project without copying Sentia's source into that
project or opening a special editor window.

Wispr Flow already enters dictated text into the focused application. Sentia
does not need a proprietary Wispr integration to accept voice input in a
terminal prompt.

## Decision

Sentia's long-term primary entry point will be a macOS-first terminal command:

```text
cd /path/to/project
sentia
```

The command treats the current directory as the workspace, starts the local
Sentia runtime, restores an Anthropic API key from secure operating-system
storage, and opens a conversational terminal interface. Wispr Flow can dictate
directly into the focused prompt. The same read-only repository intelligence,
mode routing, approval state machine, and Claude Agent SDK adapter remain shared
application services rather than editor-specific logic.

The VS Code extension becomes an optional companion. When present, it connects
to the local Sentia runtime to provide file navigation, line highlighting,
plans, progress, and diffs. The terminal experience must remain functional
without the extension.

## Distribution target

- Install with `pipx`, Homebrew, or a signed macOS package; select one after
  packaging experiments.
- Run against the current working directory by default, with an explicit path
  option for automation.
- Store the Anthropic credential in macOS Keychain through a maintained keyring
  adapter; never require a key in repository files.
- Use a terminal UI package such as Rich/Textual for conversation, evidence,
  state, approvals, and cancellation.
- Use native terminal links and an optional `code --goto path:line` bridge when
  an editor is available.
- Keep coding disabled until a displayed plan has explicit approval.

## Consequences

- Repository intelligence and orchestration must be extracted from FastAPI
  route handlers into host-neutral services shared by the CLI and extension.
- The local HTTP/WebSocket layer remains useful for optional editor clients but
  is not required inside a single CLI process.
- The current VS Code extension remains the Phase 2–3 development harness for
  visual grounding.
- Packaging must include Python runtime dependencies and macOS credential
  storage without asking users to configure a development environment.
