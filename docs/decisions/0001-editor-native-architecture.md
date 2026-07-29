# ADR 0001: Editor-native architecture

- Status: Superseded for primary distribution by ADR 0002; retained for the
  optional editor companion
- Date: 2026-07-26

## Context

Sentia must observe selections, navigate files, highlight exact ranges, manage
workspace trust, and run a local coding agent. A conventional website would need
an additional editor bridge and would separate the main experience from the code
being discussed.

## Decision

For the editor-hosted implementation, the VS Code extension is the product host
and security boundary. It embeds a
React webview for presentation and supervises a private FastAPI sidecar for
repository intelligence and agent orchestration. The webview uses VS Code
`postMessage`; only the extension connects to the sidecar over authenticated
loopback HTTP and WebSockets.

VS Code stable APIs are the primary contract. Cursor is a compatibility target,
not a source of required proprietary APIs.

## Consequences

- The primary experience stays inside the code editor.
- React remains useful without creating a website deployment.
- The extension must package and supervise platform-compatible sidecar builds.
- The webview remains unprivileged and does not receive the sidecar token.
- Python and TypeScript require a versioned shared protocol.
- Remote and virtual workspaces need explicit capability handling.
