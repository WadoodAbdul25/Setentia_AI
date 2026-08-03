import { spawn } from "node:child_process";
import type { ChildProcessByStdio } from "node:child_process";
import * as path from "node:path";
import type { Readable } from "node:stream";

import type * as vscode from "vscode";

type MicrophoneProcess = ChildProcessByStdio<null, Readable, Readable>;

export class NativeMicrophone implements vscode.Disposable {
  private child: MicrophoneProcess | undefined;
  private sessionId: string | undefined;
  private stopping = false;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly output: vscode.OutputChannel,
  ) {}

  start(
    sessionId: string,
    onAudio: (chunk: Buffer) => void,
    onError: (message: string) => void,
    sampleRate = 16_000,
  ): void {
    this.stop();
    if (process.platform !== "darwin") {
      throw new Error(
        "Native microphone capture is currently available on macOS only.",
      );
    }

    const executable = this.context.asAbsolutePath(
      path.join("bin", "sentia-mic"),
    );
    this.stopping = false;
    this.sessionId = sessionId;
    if (sampleRate !== 16_000 && sampleRate !== 24_000) {
      throw new Error("Native microphone supports 16 kHz or 24 kHz PCM.");
    }
    const child = spawn(executable, [String(sampleRate)], {
      cwd: this.context.extensionPath,
      env: process.env,
      shell: false,
      stdio: ["ignore", "pipe", "pipe"],
    });
    this.child = child;

    child.stdout.on("data", (chunk: Buffer) => {
      if (this.sessionId === sessionId && chunk.length > 0) {
        onAudio(chunk);
      }
    });
    child.stderr.on("data", (chunk: Buffer) => {
      const message = chunk.toString("utf8").trim();
      if (message) {
        this.output.appendLine(`[microphone] ${message}`);
      }
    });
    child.once("error", (error) => {
      if (!this.stopping && this.sessionId === sessionId) {
        onError(`Could not start the microphone: ${error.message}`);
      }
    });
    child.once("exit", (code, signal) => {
      const expected = this.stopping || this.sessionId !== sessionId;
      if (this.child === child) {
        this.child = undefined;
        this.sessionId = undefined;
      }
      if (!expected && code !== 0) {
        onError(
          `Microphone capture stopped (${String(code ?? signal ?? "unknown error")}). Check macOS Privacy & Security > Microphone and allow Sentia Microphone.`,
        );
      }
    });
  }

  stop(sessionId?: string): void {
    if (sessionId && this.sessionId !== sessionId) {
      return;
    }
    const child = this.child;
    this.child = undefined;
    this.sessionId = undefined;
    if (child && child.exitCode === null && child.signalCode === null) {
      this.stopping = true;
      child.kill("SIGTERM");
    }
  }

  dispose(): void {
    this.stop();
  }
}
