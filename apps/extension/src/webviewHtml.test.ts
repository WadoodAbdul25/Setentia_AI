import { describe, expect, it } from "vitest";

import { prepareWebviewHtml } from "./webviewHtml";

describe("webview HTML preparation", () => {
  it("allows Vite entry and imported chunks from the VS Code webview origin", () => {
    const html = prepareWebviewHtml(
      '<html><head></head><body><script type="module" src="./assets/entry.js"></script><link rel="modulepreload" href="./assets/shared.js"></body></html>',
      "vscode-webview://fixture/media/webview",
      "vscode-webview://fixture",
      "abc123",
    );

    expect(html).toContain(
      "script-src vscode-webview://fixture 'nonce-abc123'",
    );
    expect(html).toContain(
      'script nonce="abc123" type="module" src="vscode-webview://fixture/media/webview/assets/entry.js"',
    );
    expect(html).toContain(
      'href="vscode-webview://fixture/media/webview/assets/shared.js"',
    );
  });
});
