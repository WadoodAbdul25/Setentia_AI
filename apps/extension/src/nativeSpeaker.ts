import { spawn } from "node:child_process";
import type { ChildProcessByStdio } from "node:child_process";
import * as path from "node:path";
import type { Readable, Writable } from "node:stream";

import type * as vscode from "vscode";

type SpeakerProcess = ChildProcessByStdio<Writable, Readable, Readable>;

export class NativeSpeaker implements vscode.Disposable {
  private child: SpeakerProcess | undefined;
  private stopping = false;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly output: vscode.OutputChannel,
  ) {}

  start(onError: (message: string) => void): void {
    this.stop();
    if (process.platform !== "darwin") {
      throw new Error(
        "Native speech playback is currently available on macOS only.",
      );
    }
    this.stopping = false;
    const child = spawn(
      this.context.asAbsolutePath(path.join("bin", "sentia-speaker")),
      [],
      {
        cwd: this.context.extensionPath,
        env: process.env,
        shell: false,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    this.child = child;
    child.stderr.on("data", (chunk: Buffer) => {
      const message = chunk.toString("utf8").trim();
      if (message) {
        this.output.appendLine(`[speaker] ${message}`);
      }
    });
    child.once("error", (error) => {
      if (!this.stopping && this.child === child) {
        onError(`Could not start speech playback: ${error.message}`);
      }
    });
    child.once("exit", (code, signal) => {
      const expected = this.stopping;
      if (this.child === child) {
        this.child = undefined;
      }
      if (!expected && code !== 0) {
        onError(
          `Speech playback stopped (${String(code ?? signal ?? "unknown error")}).`,
        );
      }
    });
  }

  write(chunk: Buffer): void {
    if (chunk.length > 0 && this.child?.stdin.writable) {
      this.child.stdin.write(chunk);
    }
  }

  finish(): void {
    if (this.child?.stdin.writable) {
      this.child.stdin.end();
    }
  }

  stop(): void {
    const child = this.child;
    this.child = undefined;
    if (child && child.exitCode === null && child.signalCode === null) {
      this.stopping = true;
      child.stdin.destroy();
      child.kill("SIGTERM");
    }
  }

  dispose(): void {
    this.stop();
  }
}
