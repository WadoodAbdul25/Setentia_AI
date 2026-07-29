import { randomBytes } from "node:crypto";
import { readFile, realpath } from "node:fs/promises";
import * as path from "node:path";

import {
  agentProviderSchema,
  fluxModelSchema,
  webviewToExtensionMessageSchema,
  type AgentOnboarding,
  type AgentProvider,
  type EventEnvelope,
  type ExtensionToWebviewMessage,
  type AnthropicStatus,
  type DeepgramStatus,
  type EvidenceRange,
  type SidecarStatus,
  type VoiceServerMessage,
} from "@sentia/protocol";
import * as vscode from "vscode";

import type { SidecarRuntime } from "./sidecarRuntime.js";
import { DeepgramSpeech } from "./deepgramSpeech.js";
import { NativeMicrophone } from "./nativeMicrophone.js";

export class SentiaViewProvider implements vscode.WebviewViewProvider {
  static readonly viewType = "sentia.sidebar";
  private static readonly anthropicSecretKey = "sentia.anthropicApiKey"; // pragma: allowlist secret
  private static readonly deepgramSecretKey = "sentia.deepgramApiKey"; // pragma: allowlist secret
  private static readonly agentProviderKey = "sentia.agentProvider";
  private view: vscode.WebviewView | undefined;
  private agent: AgentOnboarding = {
    selectedProvider: null,
    connection: null,
  };
  private anthropic: AnthropicStatus = {
    connected: false,
    model: "claude-haiku-4-5-20251001",
    message: null,
  };
  private deepgram: DeepgramStatus = {
    connected: false,
    message: null,
  };
  private readonly evidenceDecoration: vscode.TextEditorDecorationType;
  private readonly microphone: NativeMicrophone;
  private readonly speech: DeepgramSpeech;
  private activeVoiceSessionId: string | undefined;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly runtime: SidecarRuntime,
    private readonly output: vscode.OutputChannel,
  ) {
    this.microphone = new NativeMicrophone(context, output);
    this.speech = new DeepgramSpeech(context, output);
    this.evidenceDecoration = vscode.window.createTextEditorDecorationType({
      backgroundColor: new vscode.ThemeColor(
        "editor.findMatchHighlightBackground",
      ),
      isWholeLine: true,
      overviewRulerColor: new vscode.ThemeColor(
        "editorOverviewRuler.findMatchForeground",
      ),
      overviewRulerLane: vscode.OverviewRulerLane.Center,
    });
    this.context.subscriptions.push(
      this.evidenceDecoration,
      this.microphone,
      this.speech,
    );
  }

  async resolveWebviewView(view: vscode.WebviewView): Promise<void> {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [
        vscode.Uri.joinPath(this.context.extensionUri, "media", "webview"),
      ],
    };
    view.webview.html = await this.getHtml(view.webview);
    view.webview.onDidReceiveMessage(
      (message: unknown) => void this.handleMessage(message),
      undefined,
      this.context.subscriptions,
    );
    view.onDidDispose(() => {
      if (this.activeVoiceSessionId) {
        this.microphone.stop(this.activeVoiceSessionId);
        this.runtime.cancelVoice(this.activeVoiceSessionId);
        this.activeVoiceSessionId = undefined;
      }
      this.speech.stop();
      this.view = undefined;
    });

    try {
      await this.runtime.start();
      await this.attachWorkspaceSnapshot();
      await this.restoreAnthropicKey();
      await this.restoreDeepgramKey();
      await this.restoreAgentProvider();
    } catch (error) {
      this.output.appendLine(
        `[extension] sidecar start failed: ${String(error)}`,
      );
    }
  }

  postStatus(status: SidecarStatus): void {
    this.post({ type: "sidecar.status", payload: status });
  }

  postEvent(event: EventEnvelope): void {
    this.post({ type: "sidecar.event", payload: event });
  }

  postVoiceMessage(message: VoiceServerMessage): void {
    if (
      message.type === "voice.error" ||
      (message.type === "voice.status" &&
        (message.state === "closed" || message.state === "error"))
    ) {
      this.microphone.stop(message.sessionId);
      if (this.activeVoiceSessionId === message.sessionId) {
        this.activeVoiceSessionId = undefined;
      }
    }
    this.post(message);
  }

  async connectAnthropic(): Promise<void> {
    const apiKey = await vscode.window.showInputBox({
      title: "Connect Sentia to Anthropic",
      prompt:
        "Paste an Anthropic Console API key. It will be stored in VS Code SecretStorage.",
      placeHolder: "sk-ant-api03-…",
      password: true,
      ignoreFocusOut: true,
      validateInput: (value) =>
        value.trim().length < 20
          ? "Enter a complete Anthropic API key."
          : undefined,
    });
    if (apiKey === undefined) {
      return;
    }

    this.setAnthropicStatus({
      ...this.anthropic,
      connected: false,
      message: "Checking Anthropic connection…",
    });
    try {
      const status = await this.runtime.setAnthropicKey(apiKey, true);
      await this.context.secrets.store(
        SentiaViewProvider.anthropicSecretKey,
        apiKey.trim(),
      );
      this.setAnthropicStatus(status);
      await this.refreshAgentStatus();
      void vscode.window.showInformationMessage(
        "Sentia is connected to Anthropic.",
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.setAnthropicStatus({
        ...this.anthropic,
        connected: false,
        message,
      });
      throw error;
    }
  }

  async disconnectAnthropic(): Promise<void> {
    await this.context.secrets.delete(SentiaViewProvider.anthropicSecretKey);
    try {
      this.setAnthropicStatus(await this.runtime.clearAnthropicKey());
    } catch {
      this.setAnthropicStatus({
        ...this.anthropic,
        connected: false,
        message: "Anthropic is disconnected.",
      });
    }
    await this.refreshAgentStatus();
  }

  async connectDeepgram(): Promise<void> {
    const apiKey = await vscode.window.showInputBox({
      title: "Connect Sentia Voice to Deepgram",
      prompt:
        "Paste a Deepgram API key. It will be stored in VS Code SecretStorage.",
      placeHolder: "Deepgram API key",
      password: true,
      ignoreFocusOut: true,
      validateInput: (value) =>
        value.trim().length < 20
          ? "Enter a complete Deepgram API key."
          : undefined,
    });
    if (apiKey === undefined) {
      return;
    }
    const status = await this.runtime.setDeepgramKey(apiKey.trim());
    await this.context.secrets.store(
      SentiaViewProvider.deepgramSecretKey,
      apiKey.trim(),
    );
    this.setDeepgramStatus(status);
    void vscode.window.showInformationMessage(
      "Sentia voice is connected to Deepgram Flux.",
    );
  }

  async disconnectDeepgram(): Promise<void> {
    this.speech.stop();
    await this.context.secrets.delete(SentiaViewProvider.deepgramSecretKey);
    try {
      this.setDeepgramStatus(await this.runtime.clearDeepgramKey());
    } catch {
      this.setDeepgramStatus({
        connected: false,
        message: "Deepgram is disconnected.",
      });
    }
  }

  async selectAgentProvider(provider: AgentProvider): Promise<void> {
    await this.context.globalState.update(
      SentiaViewProvider.agentProviderKey,
      provider,
    );
    this.agent = { selectedProvider: provider, connection: null };
    this.postAgentStatus();
    await this.refreshAgentStatus();
  }

  async clearAgentProvider(): Promise<void> {
    await this.context.globalState.update(
      SentiaViewProvider.agentProviderKey,
      undefined,
    );
    this.agent = { selectedProvider: null, connection: null };
    this.postAgentStatus();
  }

  async connectSelectedAgent(): Promise<void> {
    const provider = this.agent.selectedProvider;
    if (!provider) {
      throw new Error("Choose Claude Code or Codex before connecting.");
    }
    if (provider === "claude") {
      await this.connectAnthropic();
      return;
    }

    const status = await this.runtime.getAgentStatus("codex");
    if (status.connected) {
      this.agent = { selectedProvider: provider, connection: status };
      this.postAgentStatus();
      return;
    }

    const login = await this.runtime.startCodexLogin();
    const opened = await vscode.env.openExternal(
      vscode.Uri.parse(login.authUrl),
    );
    this.agent = {
      selectedProvider: provider,
      connection: {
        ...status,
        message: opened
          ? "Complete Codex login in the browser, then select Check connection."
          : "Open the Codex login URL from Sentia Diagnostics, then select Check connection.",
      },
    };
    this.postAgentStatus();
  }

  async refreshAgentStatus(): Promise<void> {
    const provider = this.agent.selectedProvider;
    if (!provider || this.runtime.getStatus().status !== "healthy") {
      return;
    }
    try {
      this.agent = {
        selectedProvider: provider,
        connection: await this.runtime.getAgentStatus(provider),
      };
    } catch (error) {
      this.agent = {
        selectedProvider: provider,
        connection: {
          provider,
          connected: false,
          authentication: "unavailable",
          message: error instanceof Error ? error.message : String(error),
        },
      };
    }
    this.postAgentStatus();
  }

  async restartSidecar(): Promise<void> {
    await this.runtime.restart();
    await this.attachWorkspaceSnapshot();
    await this.restoreAnthropicKey();
    await this.restoreDeepgramKey();
    await this.restoreAgentProvider();
  }

  async attachWorkspaceSnapshot(): Promise<void> {
    const folder = vscode.workspace.workspaceFolders?.[0];
    if (!folder || !vscode.workspace.isTrusted) {
      return;
    }
    try {
      const snapshot = await this.runtime.attachWorkspaceSnapshot(
        folder.uri.fsPath,
      );
      this.output.appendLine(
        `[snapshot] watching ${snapshot.workspaceName}: ${String(snapshot.fileCount)} files, ${String(snapshot.directoryCount)} directories`,
      );
    } catch (error) {
      this.output.appendLine(`[snapshot] attach failed: ${String(error)}`);
    }
  }

  private async handleMessage(raw: unknown): Promise<void> {
    const result = webviewToExtensionMessageSchema.safeParse(raw);
    if (!result.success) {
      this.output.appendLine(
        "[webview] rejected message outside the allowlist",
      );
      return;
    }

    const message = result.data;
    if (message.type === "ui.ready") {
      await this.postBootstrap();
      return;
    }

    try {
      switch (message.type) {
        case "sidecar.get_status":
          this.respond(message.requestId, true, this.runtime.getStatus());
          break;
        case "sidecar.restart":
          await this.restartSidecar();
          this.respond(message.requestId, true, this.runtime.getStatus());
          break;
        case "diagnostics.open":
          this.output.show(true);
          this.respond(message.requestId, true);
          break;
        case "anthropic.connect":
          await this.connectAnthropic();
          this.respond(message.requestId, true);
          break;
        case "anthropic.disconnect":
          await this.disconnectAnthropic();
          this.respond(message.requestId, true);
          break;
        case "deepgram.connect":
          await this.connectDeepgram();
          this.respond(message.requestId, true);
          break;
        case "deepgram.disconnect":
          await this.disconnectDeepgram();
          this.respond(message.requestId, true);
          break;
        case "agent.select":
          await this.selectAgentProvider(message.provider);
          this.respond(message.requestId, true);
          break;
        case "agent.connect":
          await this.connectSelectedAgent();
          this.respond(message.requestId, true);
          break;
        case "agent.refresh":
          await this.refreshAgentStatus();
          this.respond(message.requestId, true);
          break;
        case "agent.clear_selection":
          await this.clearAgentProvider();
          this.respond(message.requestId, true);
          break;
        case "repository.ask": {
          const folder = this.requireTrustedWorkspace();
          const provider = this.agent.selectedProvider;
          if (!provider) {
            throw new Error(
              "Choose Claude Code or Codex before asking Sentia.",
            );
          }
          const answer = await this.runtime.askRepository(
            folder.uri.fsPath,
            message.question,
            provider,
          );
          this.post({
            type: "repository.answer",
            requestId: message.requestId,
            payload: answer,
          });
          if (message.responseMode === "voice") {
            try {
              const apiKey = await this.context.secrets.get(
                SentiaViewProvider.deepgramSecretKey,
              );
              if (!apiKey) {
                void vscode.window.showWarningMessage(
                  "Sentia displayed the answer, but Deepgram is disconnected so it could not speak.",
                );
                break;
              }
              const spoken = await this.runtime.renderSpeech(
                folder.uri.fsPath,
                answer.answer,
                provider,
              );
              const voiceConfiguration =
                vscode.workspace.getConfiguration("sentia.voice");
              this.speech.speak(
                {
                  apiKey,
                  endpoint: voiceConfiguration.get<string>(
                    "deepgramEndpoint",
                    "wss://api.deepgram.com",
                  ),
                  model: voiceConfiguration.get<string>(
                    "deepgramTtsModel",
                    "flux-marcus-en",
                  ),
                  text: spoken.spokenAnswer,
                },
                (error) => {
                  void vscode.window.showWarningMessage(
                    `Sentia displayed the answer, but speech failed: ${error}`,
                  );
                },
              );
            } catch (error) {
              void vscode.window.showWarningMessage(
                `Sentia displayed the answer, but could not prepare its spoken version: ${String(error)}`,
              );
            }
          }
          break;
        }
        case "voice.start": {
          this.speech.stop();
          const folder = this.requireTrustedWorkspace();
          const configuration =
            vscode.workspace.getConfiguration("sentia.voice");
          const configuredModel = fluxModelSchema.safeParse(
            configuration.get<unknown>("deepgramModel"),
          );
          const model = configuredModel.success
            ? configuredModel.data
            : "flux-general-en";
          const endpoint = configuration.get<string>(
            "deepgramEndpoint",
            "wss://api.deepgram.com",
          );
          const languageHints =
            model === "flux-general-multi"
              ? configuration
                  .get<string[]>("languageHints", [])
                  .map((hint) => hint.trim())
                  .filter(Boolean)
              : [];
          const editor = vscode.window.activeTextEditor;
          const relativeActiveFile = editor
            ? path.relative(folder.uri.fsPath, editor.document.uri.fsPath)
            : null;
          const activeFile =
            relativeActiveFile &&
            !relativeActiveFile.startsWith("..") &&
            !path.isAbsolute(relativeActiveFile)
              ? relativeActiveFile.split(path.sep).join("/")
              : null;
          await this.runtime.startVoice({
            sessionId: message.sessionId,
            workspacePath: folder.uri.fsPath,
            activeFile,
            model,
            endpoint,
            languageHints,
            encoding: "linear16",
            sampleRate: 16_000,
          });
          this.activeVoiceSessionId = message.sessionId;
          this.microphone.start(
            message.sessionId,
            (chunk) => this.runtime.sendVoiceAudio(message.sessionId, chunk),
            (error) => {
              this.runtime.cancelVoice(message.sessionId);
              this.post({
                type: "voice.error",
                sessionId: message.sessionId,
                error,
              });
            },
          );
          this.respond(message.requestId, true);
          break;
        }
        case "voice.stop":
          this.microphone.stop(message.sessionId);
          if (this.activeVoiceSessionId === message.sessionId) {
            this.activeVoiceSessionId = undefined;
          }
          this.runtime.stopVoice(message.sessionId);
          this.respond(message.requestId, true);
          break;
        case "voice.cancel":
          this.microphone.stop(message.sessionId);
          if (this.activeVoiceSessionId === message.sessionId) {
            this.activeVoiceSessionId = undefined;
          }
          this.runtime.cancelVoice(message.sessionId);
          this.respond(message.requestId, true);
          break;
        case "editor.open_evidence":
          await this.openEvidence(message.evidence);
          this.respond(message.requestId, true);
          break;
      }
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : String(error);
      if (message.type === "voice.start") {
        this.microphone.stop(message.sessionId);
        if (this.activeVoiceSessionId === message.sessionId) {
          this.activeVoiceSessionId = undefined;
        }
        this.runtime.cancelVoice(message.sessionId);
        this.post({
          type: "voice.error",
          sessionId: message.sessionId,
          error: errorMessage,
        });
      }
      if (!("requestId" in message)) {
        this.output.appendLine(`[extension] ${errorMessage}`);
        return;
      }
      this.post({
        type: "request.error",
        requestId: message.requestId,
        error: errorMessage,
      });
      this.respond(message.requestId, false, undefined, errorMessage);
    }
  }

  private async postBootstrap(): Promise<void> {
    await this.restoreAnthropicKey();
    await this.restoreDeepgramKey();
    await this.restoreAgentProvider();
    const folder = vscode.workspace.workspaceFolders?.[0];
    const metadata = this.context.extension.packageJSON as {
      version?: unknown;
    };
    this.post({
      type: "app.bootstrap",
      payload: {
        extensionVersion:
          typeof metadata.version === "string" ? metadata.version : "unknown",
        workspaceName: folder?.name ?? null,
        workspaceTrusted: vscode.workspace.isTrusted,
        sidecar: this.runtime.getStatus(),
        anthropic: this.anthropic,
        deepgram: this.deepgram,
        agent: this.agent,
      },
    });
  }

  private respond(
    requestId: string,
    ok: boolean,
    payload?: unknown,
    error?: string,
  ): void {
    this.post({ type: "response", requestId, ok, payload, error });
  }

  private post(message: ExtensionToWebviewMessage): void {
    void this.view?.webview.postMessage(message);
  }

  private setAnthropicStatus(status: AnthropicStatus): void {
    this.anthropic = status;
    this.post({ type: "anthropic.status", payload: status });
  }

  private setDeepgramStatus(status: DeepgramStatus): void {
    this.deepgram = status;
    this.post({ type: "deepgram.status", payload: status });
  }

  private postAgentStatus(): void {
    this.post({ type: "agent.status", payload: this.agent });
  }

  private async restoreAgentProvider(): Promise<void> {
    const stored = this.context.globalState.get<unknown>(
      SentiaViewProvider.agentProviderKey,
    );
    const parsed = agentProviderSchema.safeParse(stored);
    if (!parsed.success) {
      this.agent = { selectedProvider: null, connection: null };
      this.postAgentStatus();
      return;
    }
    this.agent = {
      selectedProvider: parsed.data,
      connection: this.agent.connection,
    };
    await this.refreshAgentStatus();
  }

  private async restoreAnthropicKey(): Promise<void> {
    const apiKey = await this.context.secrets.get(
      SentiaViewProvider.anthropicSecretKey,
    );
    if (!apiKey) {
      this.setAnthropicStatus({
        ...this.anthropic,
        connected: false,
        message: null,
      });
      return;
    }
    try {
      this.setAnthropicStatus(
        await this.runtime.setAnthropicKey(apiKey, false),
      );
    } catch (error) {
      this.setAnthropicStatus({
        ...this.anthropic,
        connected: false,
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }

  private async restoreDeepgramKey(): Promise<void> {
    const apiKey = await this.context.secrets.get(
      SentiaViewProvider.deepgramSecretKey,
    );
    if (!apiKey) {
      this.setDeepgramStatus({ connected: false, message: null });
      return;
    }
    try {
      this.setDeepgramStatus(await this.runtime.setDeepgramKey(apiKey));
    } catch (error) {
      this.setDeepgramStatus({
        connected: false,
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }

  private requireTrustedWorkspace(): vscode.WorkspaceFolder {
    const folder = vscode.workspace.workspaceFolders?.[0];
    if (!folder) {
      throw new Error(
        "Open a workspace folder before asking about a codebase.",
      );
    }
    if (!vscode.workspace.isTrusted) {
      throw new Error(
        "Trust this workspace before Sentia reads repository files.",
      );
    }
    return folder;
  }

  private async openEvidence(evidence: EvidenceRange): Promise<void> {
    const folder = this.requireTrustedWorkspace();
    const workspaceRoot = await realpath(folder.uri.fsPath);
    const target = path.resolve(workspaceRoot, evidence.path);
    const relative = path.relative(workspaceRoot, target);
    if (relative.startsWith("..") || path.isAbsolute(relative)) {
      throw new Error("Evidence path is outside the open workspace.");
    }
    const resolvedTarget = await realpath(target);
    const resolvedRelative = path.relative(workspaceRoot, resolvedTarget);
    if (
      resolvedRelative.startsWith("..") ||
      path.isAbsolute(resolvedRelative)
    ) {
      throw new Error("Evidence path resolves outside the open workspace.");
    }

    const document = await vscode.workspace.openTextDocument(resolvedTarget);
    const editor = await vscode.window.showTextDocument(document, {
      preview: true,
      preserveFocus: false,
    });
    const start = Math.max(0, evidence.startLine - 1);
    const end = Math.min(document.lineCount - 1, evidence.endLine - 1);
    const range = new vscode.Range(
      start,
      0,
      end,
      document.lineAt(end).text.length,
    );
    editor.revealRange(
      range,
      vscode.TextEditorRevealType.InCenterIfOutsideViewport,
    );
    editor.setDecorations(this.evidenceDecoration, [range]);
    setTimeout(() => editor.setDecorations(this.evidenceDecoration, []), 4_000);
  }

  private async getHtml(webview: vscode.Webview): Promise<string> {
    const root = vscode.Uri.joinPath(
      this.context.extensionUri,
      "media",
      "webview",
    );
    const indexUri = vscode.Uri.joinPath(root, "index.html");
    try {
      let html = await readFile(indexUri.fsPath, "utf8");
      const baseUri = webview.asWebviewUri(root).toString();
      const nonce = randomBytes(16).toString("base64");
      html = html.replaceAll("./assets/", `${baseUri}/assets/`);
      html = html.replace(
        "<head>",
        `<head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${webview.cspSource} data:; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}'; font-src ${webview.cspSource};">`,
      );
      html = html.replaceAll("<script ", `<script nonce="${nonce}" `);
      return html;
    } catch {
      return `<!doctype html><html><body><h2>Sentia UI is not built</h2><p>Run <code>pnpm build:webview</code>, then reload the window.</p></body></html>`;
    }
  }
}
