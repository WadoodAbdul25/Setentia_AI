import { createHash, randomBytes } from "node:crypto";
import { readFile, realpath } from "node:fs/promises";
import * as path from "node:path";

import {
  flowMapPanelToExtensionMessageSchema,
  type AgentProvider,
  type ExtensionToFlowMapPanelMessage,
  type FlowSourceSpan,
} from "@sentia/protocol";
import * as vscode from "vscode";

import type { SidecarRuntime } from "./sidecarRuntime.js";
import { prepareWebviewHtml } from "./webviewHtml.js";

export class FlowMapPanelManager implements vscode.Disposable {
  private readonly panels = new Set<FlowMapPanel>();

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly runtime: SidecarRuntime,
    private readonly output: vscode.OutputChannel,
  ) {}

  async open(
    question: string,
    workspaceFolder: vscode.WorkspaceFolder,
    provider: AgentProvider,
  ): Promise<void> {
    const panel = new FlowMapPanel(
      this.context,
      this.runtime,
      this.output,
      question,
      workspaceFolder,
      provider,
      () => this.panels.delete(panel),
    );
    this.panels.add(panel);
    await panel.initialize();
  }

  dispose(): void {
    for (const panel of this.panels) {
      panel.dispose();
    }
    this.panels.clear();
  }
}

class FlowMapPanel implements vscode.Disposable {
  private readonly panel: vscode.WebviewPanel;
  private readonly disposables: vscode.Disposable[] = [];
  private disposed = false;
  private ready = false;
  private generating = false;
  private state: ExtensionToFlowMapPanelMessage;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly runtime: SidecarRuntime,
    private readonly output: vscode.OutputChannel,
    private readonly question: string,
    private readonly workspaceFolder: vscode.WorkspaceFolder,
    private readonly provider: AgentProvider,
    private readonly onDispose: () => void,
  ) {
    this.state = { type: "flow_map.loading", question };
    this.panel = vscode.window.createWebviewPanel(
      "sentia.flowMap",
      flowMapPanelTitle(question),
      vscode.ViewColumn.Active,
      {
        enableFindWidget: true,
        enableScripts: true,
        localResourceRoots: [
          vscode.Uri.joinPath(context.extensionUri, "media", "webview"),
        ],
        retainContextWhenHidden: true,
      },
    );
    this.panel.iconPath = vscode.Uri.joinPath(
      context.extensionUri,
      "media",
      "sentia.svg",
    );
    this.disposables.push(
      this.panel.onDidDispose(() => this.dispose()),
      this.panel.webview.onDidReceiveMessage((raw: unknown) => {
        void this.handleMessage(raw);
      }),
    );
  }

  async initialize(): Promise<void> {
    this.panel.webview.html = await this.getHtml();
    void this.generate();
  }

  dispose(): void {
    if (this.disposed) {
      return;
    }
    this.disposed = true;
    for (const disposable of this.disposables.splice(0)) {
      disposable.dispose();
    }
    this.onDispose();
    this.panel.dispose();
  }

  private async handleMessage(raw: unknown): Promise<void> {
    const message = flowMapPanelToExtensionMessageSchema.safeParse(raw);
    if (!message.success) {
      this.output.appendLine(
        "[flow-map] rejected message outside the allowlist",
      );
      return;
    }
    if (message.data.type === "flow_map.ready") {
      this.ready = true;
      await this.postState();
      return;
    }
    if (message.data.type === "flow_map.retry") {
      void this.generate();
      return;
    }
    if (message.data.type === "flow_map.export") {
      try {
        await this.exportResult(message.data.destination);
      } catch (error) {
        void vscode.window.showErrorMessage(
          `Sentia could not export this Flow Map: ${error instanceof Error ? error.message : String(error)}`,
        );
      }
      return;
    }
    try {
      await this.openSource(message.data.span);
    } catch (error) {
      void vscode.window.showErrorMessage(
        `Sentia could not open this Flow Map source: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }

  private async exportResult(destination: "clipboard" | "file"): Promise<void> {
    if (this.state.type !== "flow_map.result") {
      return;
    }
    const response = this.state.payload;
    const exportedAt = new Date().toISOString();
    const contents =
      JSON.stringify(
        {
          format: "sentia.flow-map.export",
          version: 1,
          exportedAt,
          agent: {
            provider: response.investigation.provider,
            model: response.model,
          },
          tokens: {
            total: response.usage,
            investigation: response.investigationUsage,
            traceTurns: response.featureTrace?.usage ?? [],
            note: "Reported usage for this result. Breakdowns overlap; do not add them to the total. Unreported usage from interrupted calls is not included.",
          },
          result: response,
        },
        null,
        2,
      ) + "\n";
    if (destination === "clipboard") {
      await vscode.env.clipboard.writeText(contents);
      void vscode.window.showInformationMessage(
        "Copied Flow Map and feature trace, including agent and token usage.",
      );
      return;
    }
    const uri = await vscode.window.showSaveDialog({
      title: "Export Flow Map and feature trace",
      defaultUri: vscode.Uri.joinPath(
        this.workspaceFolder.uri,
        `sentia-flow-map-${exportedAt.replace(/[:.]/g, "-")}.json`,
      ),
      filters: { JSON: ["json"] },
      saveLabel: "Export",
    });
    if (!uri) return;
    await vscode.workspace.fs.writeFile(uri, Buffer.from(contents, "utf8"));
    void vscode.window.showInformationMessage(
      "Exported Flow Map and feature trace.",
    );
  }

  private async generate(): Promise<void> {
    if (this.generating || this.disposed) {
      return;
    }
    this.generating = true;
    this.state = { type: "flow_map.loading", question: this.question };
    await this.postState();
    try {
      const response = await this.runtime.createFlowMap(
        this.workspaceFolder.uri.fsPath,
        this.question,
        this.provider,
      );
      if (this.disposed) {
        return;
      }
      this.state = { type: "flow_map.result", payload: response };
      this.panel.title = flowMapPanelTitle(
        this.question,
        response.graph.nodes.length,
      );
      await this.postState();
    } catch (error) {
      if (this.disposed) {
        return;
      }
      const message = error instanceof Error ? error.message : String(error);
      this.output.appendLine(`[flow-map] generation failed: ${message}`);
      this.state = { type: "flow_map.error", error: message };
      await this.postState();
    } finally {
      this.generating = false;
    }
  }

  private async postState(): Promise<void> {
    if (!this.ready || this.disposed) {
      return;
    }
    await this.panel.webview.postMessage(this.state);
  }

  private async openSource(span: FlowSourceSpan): Promise<void> {
    const workspaceRoot = await realpath(this.workspaceFolder.uri.fsPath);
    const target = path.resolve(workspaceRoot, span.path);
    const relative = path.relative(workspaceRoot, target);
    if (relative.startsWith("..") || path.isAbsolute(relative)) {
      throw new Error("The source path is outside the open workspace.");
    }
    const resolvedTarget = await realpath(target);
    const resolvedRelative = path.relative(workspaceRoot, resolvedTarget);
    if (
      resolvedRelative.startsWith("..") ||
      path.isAbsolute(resolvedRelative)
    ) {
      throw new Error("The source path resolves outside the open workspace.");
    }
    const currentHash = createHash("sha256")
      .update(await readFile(resolvedTarget))
      .digest("hex");
    if (currentHash !== span.contentHash) {
      void vscode.window.showWarningMessage(
        "This file changed after the Flow Map was generated. Sentia opened the recorded location, but the evidence may be stale.",
      );
    }

    const document = await vscode.workspace.openTextDocument(resolvedTarget);
    const editor = await vscode.window.showTextDocument(document, {
      preview: true,
      preserveFocus: false,
    });
    const startLine = Math.min(
      document.lineCount - 1,
      Math.max(0, span.startLine - 1),
    );
    const endLine = Math.min(
      document.lineCount - 1,
      Math.max(startLine, span.endLine - 1),
    );
    const range = new vscode.Range(
      startLine,
      0,
      endLine,
      document.lineAt(endLine).text.length,
    );
    editor.selection = new vscode.Selection(range.start, range.end);
    editor.revealRange(
      range,
      vscode.TextEditorRevealType.InCenterIfOutsideViewport,
    );
  }

  private async getHtml(): Promise<string> {
    const root = vscode.Uri.joinPath(
      this.context.extensionUri,
      "media",
      "webview",
    );
    const indexUri = vscode.Uri.joinPath(root, "flow-map.html");
    try {
      const html = await readFile(indexUri.fsPath, "utf8");
      const baseUri = this.panel.webview.asWebviewUri(root).toString();
      const nonce = randomBytes(16).toString("base64");
      return prepareWebviewHtml(
        html,
        baseUri,
        this.panel.webview.cspSource,
        nonce,
      );
    } catch {
      return "<!doctype html><html><body><h2>Sentia Flow Maps UI is not built</h2><p>Run the webview build, then reload the window.</p></body></html>";
    }
  }
}

export function flowMapPanelTitle(
  question: string,
  nodeCount?: number,
): string {
  const normalized = question.trim().replace(/\s+/g, " ");
  const label =
    normalized.length > 46 ? `${normalized.slice(0, 43)}…` : normalized;
  const count = nodeCount === undefined ? "" : ` · ${String(nodeCount)} nodes`;
  return `Flow Map: ${label || "Untitled"}${count}`;
}
