import * as vscode from "vscode";

import { SentiaViewProvider } from "./sentiaViewProvider.js";
import { SidecarRuntime } from "./sidecarRuntime.js";

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel("Sentia", { log: true });
  const providerRef: { current?: SentiaViewProvider } = {};
  const runtime = new SidecarRuntime(context, output, {
    onEvent: (event) => providerRef.current?.postEvent(event),
    onStatus: (status) => providerRef.current?.postStatus(status),
    onVoiceMessage: (message) => providerRef.current?.postVoiceMessage(message),
  });
  const provider = new SentiaViewProvider(context, runtime, output);
  providerRef.current = provider;

  context.subscriptions.push(
    output,
    runtime,
    vscode.window.registerWebviewViewProvider(
      SentiaViewProvider.viewType,
      provider,
      {
        webviewOptions: { retainContextWhenHidden: true },
      },
    ),
    vscode.commands.registerCommand("sentia.restartSidecar", async () => {
      await provider.restartSidecar();
    }),
    vscode.commands.registerCommand("sentia.open", async () => {
      await vscode.commands.executeCommand<void>(
        "workbench.view.extension.sentia",
      );
    }),
    vscode.commands.registerCommand("sentia.connectAnthropic", async () => {
      await provider.connectAnthropic();
    }),
    vscode.commands.registerCommand("sentia.disconnectAnthropic", async () => {
      await provider.disconnectAnthropic();
    }),
    vscode.commands.registerCommand("sentia.openDiagnostics", () =>
      output.show(true),
    ),
    vscode.workspace.onDidChangeWorkspaceFolders(() => {
      void provider.attachWorkspaceSnapshot();
    }),
    vscode.workspace.onDidGrantWorkspaceTrust(() => {
      void provider.attachWorkspaceSnapshot();
    }),
  );

  if (context.extensionMode === vscode.ExtensionMode.Development) {
    setTimeout(() => {
      void vscode.commands.executeCommand<void>(
        "workbench.view.extension.sentia",
      );
    }, 250);
  }
}

export function deactivate(): void {
  // Runtime disposal is owned by ExtensionContext subscriptions.
}
