import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync } from "node:fs";
import { createServer } from "node:net";
import * as path from "node:path";
import type { ReadableStream } from "node:stream/web";

import {
  agentConnectionStatusSchema,
  agentLoginStartSchema,
  anthropicStatusSchema,
  deepgramStatusSchema,
  eventEnvelopeSchema,
  healthResponseSchema,
  openaiVoiceStatusSchema,
  projectSnapshotStatusSchema,
  PROTOCOL_VERSION,
  repositoryAnswerSchema,
  repositoryGraphSchema,
  spokenAnswerSchema,
  voiceServerMessageSchema,
  workflowStateSchema,
  type AgentConnectionStatus,
  type AgentLoginStart,
  type AgentProvider,
  type AnthropicStatus,
  type DeepgramStatus,
  type EventEnvelope,
  type ProjectSnapshotStatus,
  type RepositoryAnswer,
  type RepositoryGraph,
  type SpokenAnswer,
  type SidecarStatus,
  type OpenAIVoiceStatus,
  type VoiceProvider,
  type VoiceServerMessage,
} from "@sentia/protocol";
import WebSocket from "ws";
import * as vscode from "vscode";

const START_TIMEOUT_MS = 20_000;

export interface SidecarRuntimeListener {
  onEvent(event: EventEnvelope): void;
  onStatus(status: SidecarStatus): void;
  onVoiceMessage(message: VoiceServerMessage): void;
}

export interface VoiceSessionOptions {
  sessionId: string;
  workspacePath: string;
  activeFile: string | null;
  provider: VoiceProvider;
  model: string;
  endpoint: string;
  languageHints: string[];
  encoding: "linear16";
  sampleRate: number;
}

interface LaunchCommand {
  command: string;
  args: string[];
  cwd: string;
}

export class SidecarRuntime implements vscode.Disposable {
  private child: ChildProcessWithoutNullStreams | undefined;
  private socket: WebSocket | undefined;
  private voiceSocket: WebSocket | undefined;
  private voiceSessionId: string | undefined;
  private voiceQueue: Buffer[] = [];
  private voiceQueueBytes = 0;
  private token: string | undefined;
  private port: number | undefined;
  private lastSequence = 0;
  private nextHttpRequestId = 1;
  private stopping = false;
  private status: SidecarStatus = {
    status: "stopped",
    workflowState: "disconnected",
    version: null,
    protocolVersion: null,
    message: null,
  };

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly output: vscode.OutputChannel,
    private readonly listener: SidecarRuntimeListener,
  ) {}

  getStatus(): SidecarStatus {
    return this.status;
  }

  async start(): Promise<void> {
    if (this.child && this.status.status !== "failed") {
      return;
    }

    this.stopping = false;
    this.updateStatus({
      status: "starting",
      workflowState: "starting",
      version: null,
      protocolVersion: null,
      message: "Starting private local sidecar…",
    });

    this.port = await findAvailablePort();
    this.token = randomBytes(32).toString("base64url");
    const launch = this.resolveLaunchCommand();
    this.output.appendLine(`[sidecar] launching ${launch.command}`);

    this.child = spawn(launch.command, launch.args, {
      cwd: launch.cwd,
      env: {
        ...process.env,
        SENTIA_HOST: "127.0.0.1",
        SENTIA_PORT: String(this.port),
        SENTIA_TOKEN: this.token,
      },
      shell: false,
      stdio: "pipe",
    });

    this.child.stdout.on("data", (chunk: Buffer) => {
      this.output.append(`[sidecar:out] ${chunk.toString()}`);
    });
    this.child.stderr.on("data", (chunk: Buffer) => {
      this.output.append(`[sidecar:err] ${chunk.toString()}`);
    });
    this.child.once("error", (error) =>
      this.handleProcessFailure(error.message),
    );
    this.child.once("exit", (code, signal) => {
      if (!this.stopping) {
        this.handleProcessFailure(
          `Sidecar exited unexpectedly (${String(code ?? signal)})`,
        );
      }
    });

    try {
      const health = await this.waitForHealth();
      if (health.protocolVersion !== PROTOCOL_VERSION) {
        throw new Error(
          `Protocol mismatch: extension ${PROTOCOL_VERSION}, sidecar ${health.protocolVersion}`,
        );
      }
      this.updateStatus({
        status: "healthy",
        workflowState: health.workflowState,
        version: health.version,
        protocolVersion: health.protocolVersion,
        message: null,
      });
      this.connectEvents();
    } catch (error) {
      await this.stop();
      const message = error instanceof Error ? error.message : String(error);
      this.handleProcessFailure(message);
      throw error;
    }
  }

  async restart(): Promise<void> {
    await this.stop();
    await this.start();
  }

  async setAnthropicKey(
    apiKey: string,
    validate: boolean,
  ): Promise<AnthropicStatus> {
    return await this.request(
      "/api/v1/auth/anthropic",
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ apiKey, validate }),
      },
      anthropicStatusSchema,
    );
  }

  async clearAnthropicKey(): Promise<AnthropicStatus> {
    return await this.request(
      "/api/v1/auth/anthropic",
      { method: "DELETE" },
      anthropicStatusSchema,
    );
  }

  async setDeepgramKey(apiKey: string): Promise<DeepgramStatus> {
    return await this.request(
      "/api/v1/auth/deepgram",
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ apiKey }),
      },
      deepgramStatusSchema,
    );
  }

  async clearDeepgramKey(): Promise<DeepgramStatus> {
    return await this.request(
      "/api/v1/auth/deepgram",
      { method: "DELETE" },
      deepgramStatusSchema,
    );
  }

  async setOpenAIVoiceKey(apiKey: string): Promise<OpenAIVoiceStatus> {
    return await this.request(
      "/api/v1/auth/openai-voice",
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ apiKey }),
      },
      openaiVoiceStatusSchema,
    );
  }

  async clearOpenAIVoiceKey(): Promise<OpenAIVoiceStatus> {
    return await this.request(
      "/api/v1/auth/openai-voice",
      { method: "DELETE" },
      openaiVoiceStatusSchema,
    );
  }

  async startVoice(options: VoiceSessionOptions): Promise<void> {
    await this.start();
    this.closeVoiceSocket();
    const query = new URLSearchParams({
      sessionId: options.sessionId,
      workspacePath: options.workspacePath,
      model: options.model,
      endpoint: options.endpoint,
      encoding: options.encoding,
      sampleRate: String(options.sampleRate),
    });
    if (options.activeFile) {
      query.set("activeFile", options.activeFile);
    }
    for (const hint of options.languageHints) {
      query.append("languageHint", hint);
    }
    const voicePath =
      options.provider === "deepgram"
        ? "/api/v1/voice/deepgram/transcribe"
        : "/api/v1/voice/openai/transcribe";
    const socket = new WebSocket(
      `ws://127.0.0.1:${String(this.port)}${voicePath}?${query.toString()}`,
      { headers: { Authorization: `Bearer ${this.token ?? ""}` } },
    );
    this.voiceSocket = socket;
    this.voiceSessionId = options.sessionId;

    socket.on("message", (data) => {
      try {
        const messageText = Array.isArray(data)
          ? Buffer.concat(data).toString("utf8")
          : data instanceof ArrayBuffer
            ? Buffer.from(data).toString("utf8")
            : data.toString("utf8");
        const parsed = voiceServerMessageSchema.parse(
          JSON.parse(messageText) as unknown,
        );
        this.listener.onVoiceMessage(parsed);
      } catch (error) {
        this.output.appendLine(
          `[voice] rejected malformed sidecar message: ${String(error)}`,
        );
      }
    });
    socket.on("error", (error) => {
      this.output.appendLine(`[voice] websocket error: ${error.message}`);
      if (this.voiceSessionId === options.sessionId) {
        this.listener.onVoiceMessage({
          type: "voice.error",
          sessionId: options.sessionId,
          error: error.message,
        });
      }
    });
    socket.on("close", () => {
      if (this.voiceSessionId === options.sessionId) {
        this.voiceSocket = undefined;
        this.voiceSessionId = undefined;
        this.voiceQueue = [];
        this.voiceQueueBytes = 0;
      }
    });

    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error("Voice connection to the sidecar timed out.")),
        10_000,
      );
      socket.once("open", () => {
        clearTimeout(timer);
        for (const chunk of this.voiceQueue) {
          socket.send(chunk);
        }
        this.voiceQueue = [];
        this.voiceQueueBytes = 0;
        resolve();
      });
      socket.once("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
    });
  }

  sendVoiceAudio(sessionId: string, chunk: Buffer): void {
    if (this.voiceSessionId !== sessionId || !this.voiceSocket) {
      return;
    }
    if (chunk.length === 0 || chunk.length > 300_000) {
      return;
    }
    if (this.voiceSocket.readyState === WebSocket.OPEN) {
      this.voiceSocket.send(chunk);
      return;
    }
    if (
      this.voiceSocket.readyState === WebSocket.CONNECTING &&
      this.voiceQueueBytes + chunk.length <= 2_000_000
    ) {
      this.voiceQueue.push(chunk);
      this.voiceQueueBytes += chunk.length;
    }
  }

  stopVoice(sessionId: string): void {
    if (
      this.voiceSessionId === sessionId &&
      this.voiceSocket?.readyState === WebSocket.OPEN
    ) {
      this.voiceSocket.send(JSON.stringify({ type: "voice.stop" }));
    }
  }

  cancelVoice(sessionId: string): void {
    if (this.voiceSessionId !== sessionId) {
      return;
    }
    if (this.voiceSocket?.readyState === WebSocket.OPEN) {
      this.voiceSocket.send(JSON.stringify({ type: "voice.cancel" }));
    }
    this.closeVoiceSocket();
  }

  async getAgentStatus(
    provider: AgentProvider,
  ): Promise<AgentConnectionStatus> {
    return await this.request(
      `/api/v1/agents/${provider}/status`,
      { method: "GET" },
      agentConnectionStatusSchema,
    );
  }

  async startCodexLogin(): Promise<AgentLoginStart> {
    return await this.request(
      "/api/v1/agents/codex/login",
      { method: "POST" },
      agentLoginStartSchema,
    );
  }

  async askRepository(
    workspacePath: string,
    question: string,
    provider: AgentProvider,
  ): Promise<RepositoryAnswer> {
    return await this.request(
      "/api/v1/repository/questions",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspacePath, question, provider }),
      },
      repositoryAnswerSchema,
    );
  }

  async repositoryGraph(workspacePath: string): Promise<RepositoryGraph> {
    return await this.request(
      "/api/v1/repository/graph",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspacePath, depth: 1 }),
      },
      repositoryGraphSchema,
    );
  }

  async renderSpeech(
    workspacePath: string,
    answer: string,
    provider: AgentProvider,
  ): Promise<SpokenAnswer> {
    return await this.request(
      "/api/v1/voice/render",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspacePath, answer, provider }),
      },
      spokenAnswerSchema,
    );
  }

  async *renderSpeechStream(
    workspacePath: string,
    answer: string,
    provider: AgentProvider,
  ): AsyncGenerator<string> {
    await this.start();
    const requestId = this.nextHttpRequestId++;
    const pathname = "/api/v1/voice/render/stream";
    const startedAt = performance.now();
    const logPayloads = vscode.workspace
      .getConfiguration("sentia.development")
      .get<boolean>("logVoicePayloads", false);
    this.output.appendLine(
      `[http:request] id=${String(requestId)} method=POST path=${pathname} provider=${provider} answerCharacters=${String(answer.length)} streaming=true`,
    );
    let response: Response;
    try {
      response = await fetch(
        `http://127.0.0.1:${String(this.port)}${pathname}`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${this.token ?? ""}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ workspacePath, answer, provider }),
        },
      );
    } catch (error) {
      this.output.appendLine(
        `[http:error] id=${String(requestId)} path=${pathname} elapsedMs=${(performance.now() - startedAt).toFixed(1)} error=${JSON.stringify(error instanceof Error ? error.message : String(error))}`,
      );
      throw error;
    }
    this.output.appendLine(
      `[http:response] id=${String(requestId)} path=${pathname} status=${String(response.status)} contentType=${JSON.stringify(response.headers.get("content-type"))} headersMs=${(performance.now() - startedAt).toFixed(1)} streaming=true`,
    );
    if (!response.ok) {
      const payload = await readResponseJson(response);
      const detail =
        typeof payload === "object" &&
        payload !== null &&
        "detail" in payload &&
        typeof payload.detail === "string"
          ? payload.detail
          : `Speech rendering failed (${String(response.status)}).`;
      throw new Error(detail);
    }
    if (!response.body) {
      throw new Error("Speech rendering returned no event stream.");
    }

    const reader = (
      response.body as ReadableStream<Uint8Array<ArrayBuffer>>
    ).getReader();
    const decoder = new TextDecoder();
    let pending = "";
    let completed = false;
    let deltaCount = 0;
    let outputCharacters = 0;
    let firstDeltaAt: number | undefined;
    try {
      while (true) {
        const { done, value } = await reader.read();
        pending += decoder.decode(value, { stream: !done });
        const lines = pending.split("\n");
        pending = lines.pop() ?? "";
        for (const line of lines) {
          const event = parseSpeechStreamEvent(line);
          if (event.type === "delta") {
            deltaCount += 1;
            outputCharacters += event.text.length;
            if (firstDeltaAt === undefined) {
              firstDeltaAt = performance.now();
            }
            this.output.appendLine(
              `[http:stream] id=${String(requestId)} event=delta index=${String(deltaCount)} characters=${String(event.text.length)} totalCharacters=${String(outputCharacters)}${logPayloads ? ` text=${JSON.stringify(event.text)}` : ""}`,
            );
            yield event.text;
          } else if (event.type === "error") {
            this.output.appendLine(
              `[http:stream] id=${String(requestId)} event=error message=${JSON.stringify(event.message)}`,
            );
            throw new Error(event.message);
          } else {
            completed = true;
          }
        }
        if (done) {
          break;
        }
      }
      if (pending.trim()) {
        const event = parseSpeechStreamEvent(pending);
        if (event.type === "delta") {
          deltaCount += 1;
          outputCharacters += event.text.length;
          if (firstDeltaAt === undefined) {
            firstDeltaAt = performance.now();
          }
          this.output.appendLine(
            `[http:stream] id=${String(requestId)} event=delta index=${String(deltaCount)} characters=${String(event.text.length)} totalCharacters=${String(outputCharacters)}${logPayloads ? ` text=${JSON.stringify(event.text)}` : ""}`,
          );
          yield event.text;
        } else if (event.type === "error") {
          this.output.appendLine(
            `[http:stream] id=${String(requestId)} event=error message=${JSON.stringify(event.message)}`,
          );
          throw new Error(event.message);
        } else {
          completed = true;
        }
      }
      if (!completed) {
        throw new Error("Speech rendering ended before its completion event.");
      }
      this.output.appendLine(
        `[http:stream-complete] id=${String(requestId)} deltas=${String(deltaCount)} outputCharacters=${String(outputCharacters)} firstDeltaMs=${firstDeltaAt === undefined ? "none" : (firstDeltaAt - startedAt).toFixed(1)} totalMs=${(performance.now() - startedAt).toFixed(1)}`,
      );
    } finally {
      reader.releaseLock();
    }
  }

  async attachWorkspaceSnapshot(
    workspacePath: string,
  ): Promise<ProjectSnapshotStatus> {
    return await this.request(
      "/api/v1/repository/snapshot",
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspacePath }),
      },
      projectSnapshotStatusSchema,
    );
  }

  async stop(): Promise<void> {
    this.stopping = true;
    this.closeVoiceSocket();
    this.socket?.close();
    this.socket = undefined;

    const child = this.child;
    this.child = undefined;
    if (child && child.exitCode === null && child.signalCode === null) {
      child.kill("SIGTERM");
      await new Promise<void>((resolve) => {
        const timer = setTimeout(() => {
          if (child.exitCode === null && child.signalCode === null) {
            child.kill("SIGKILL");
          }
          resolve();
        }, 2_000);
        child.once("exit", () => {
          clearTimeout(timer);
          resolve();
        });
      });
    }
    this.token = undefined;
    this.port = undefined;
    this.updateStatus({
      status: "stopped",
      workflowState: "disconnected",
      version: null,
      protocolVersion: null,
      message: null,
    });
  }

  dispose(): void {
    void this.stop();
  }

  private closeVoiceSocket(): void {
    this.voiceSocket?.close();
    this.voiceSocket = undefined;
    this.voiceSessionId = undefined;
    this.voiceQueue = [];
    this.voiceQueueBytes = 0;
  }

  private resolveLaunchCommand(): LaunchCommand {
    const configured = vscode.workspace
      .getConfiguration("sentia")
      .get<string>("development.sidecarExecutable", "")
      .trim();
    const repositoryRoot = path.resolve(this.context.extensionPath, "../..");
    if (configured) {
      return { command: configured, args: [], cwd: repositoryRoot };
    }

    const packagedName =
      process.platform === "win32" ? "sentia-sidecar.exe" : "sentia-sidecar";
    const packagedPath = this.context.asAbsolutePath(
      path.join("bin", packagedName),
    );
    if (this.context.extensionMode === vscode.ExtensionMode.Production) {
      return {
        command: packagedPath,
        args: [],
        cwd: this.context.globalStorageUri.fsPath,
      };
    }

    const virtualEnvironmentExecutable = path.join(
      repositoryRoot,
      ".venv",
      process.platform === "win32"
        ? "Scripts/sentia-sidecar.exe"
        : "bin/sentia-sidecar",
    );
    if (existsSync(virtualEnvironmentExecutable)) {
      return {
        command: virtualEnvironmentExecutable,
        args: [],
        cwd: repositoryRoot,
      };
    }

    return {
      command: "uv",
      args: ["run", "sentia-sidecar"],
      cwd: repositoryRoot,
    };
  }

  private async waitForHealth(): Promise<{
    version: string;
    protocolVersion: string;
    workflowState: SidecarStatus["workflowState"];
  }> {
    const deadline = Date.now() + START_TIMEOUT_MS;
    let lastError = "Sidecar did not respond";
    while (Date.now() < deadline) {
      try {
        const response = await fetch(
          `http://127.0.0.1:${String(this.port)}/health`,
          {
            headers: { Authorization: `Bearer ${this.token ?? ""}` },
          },
        );
        if (response.ok) {
          return healthResponseSchema.parse(await readResponseJson(response));
        }
        lastError = `Health check returned ${String(response.status)}`;
      } catch (error) {
        lastError = error instanceof Error ? error.message : String(error);
      }
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    throw new Error(`Sidecar start timed out: ${lastError}`);
  }

  private connectEvents(): void {
    const socket = new WebSocket(
      `ws://127.0.0.1:${String(this.port)}/ws?lastSequence=${String(this.lastSequence)}`,
      { headers: { Authorization: `Bearer ${this.token ?? ""}` } },
    );
    this.socket = socket;
    socket.on("message", (data) => {
      try {
        const text = Array.isArray(data)
          ? Buffer.concat(data).toString("utf8")
          : data instanceof ArrayBuffer
            ? Buffer.from(data).toString("utf8")
            : data.toString("utf8");
        const parsed = eventEnvelopeSchema.parse(JSON.parse(text) as unknown);
        if (parsed.type === "workflow.state") {
          const workflowState = workflowStateSchema.safeParse(
            parsed.payload.state,
          );
          if (workflowState.success) {
            this.updateStatus({
              ...this.status,
              workflowState: workflowState.data,
              message: null,
            });
          }
        }
        if (parsed.type !== "transport.heartbeat") {
          this.lastSequence = Math.max(this.lastSequence, parsed.sequence);
          socket.send(
            JSON.stringify({
              type: "transport.ack",
              sequence: parsed.sequence,
            }),
          );
        }
        this.listener.onEvent(parsed);
      } catch (error) {
        this.output.appendLine(
          `[sidecar] rejected malformed event: ${String(error)}`,
        );
      }
    });
    socket.on("error", (error) => {
      this.output.appendLine(`[sidecar] websocket error: ${error.message}`);
    });
    socket.on("close", () => {
      if (!this.stopping && this.status.status === "healthy") {
        this.updateStatus({
          ...this.status,
          message: "Event stream disconnected",
        });
      }
    });
  }

  private handleProcessFailure(message: string): void {
    this.child = undefined;
    this.updateStatus({
      status: "failed",
      workflowState: "failed",
      version: null,
      protocolVersion: null,
      message,
    });
    this.output.appendLine(`[sidecar] ${message}`);
  }

  private updateStatus(status: SidecarStatus): void {
    this.status = status;
    this.listener.onStatus(status);
  }

  private async request<T>(
    pathname: string,
    init: RequestInit,
    schema: ResponseSchema<T>,
  ): Promise<T> {
    await this.start();
    const requestId = this.nextHttpRequestId++;
    const method = init.method ?? "GET";
    const startedAt = performance.now();
    const bodyCharacters =
      typeof init.body === "string" ? init.body.length : undefined;
    this.output.appendLine(
      `[http:request] id=${String(requestId)} method=${method} path=${pathname}${bodyCharacters === undefined ? "" : ` bodyCharacters=${String(bodyCharacters)}`}`,
    );
    let response: Response;
    try {
      response = await fetch(
        `http://127.0.0.1:${String(this.port)}${pathname}`,
        {
          ...init,
          headers: {
            ...init.headers,
            Authorization: `Bearer ${this.token ?? ""}`,
          },
        },
      );
    } catch (error) {
      this.output.appendLine(
        `[http:error] id=${String(requestId)} path=${pathname} elapsedMs=${(performance.now() - startedAt).toFixed(1)} error=${JSON.stringify(error instanceof Error ? error.message : String(error))}`,
      );
      throw error;
    }
    this.output.appendLine(
      `[http:response] id=${String(requestId)} path=${pathname} status=${String(response.status)} contentType=${JSON.stringify(response.headers.get("content-type"))} elapsedMs=${(performance.now() - startedAt).toFixed(1)}`,
    );
    let payload: unknown;
    try {
      payload = await readResponseJson(response);
    } catch (error) {
      this.output.appendLine(
        `[http:error] id=${String(requestId)} path=${pathname} stage=response_parse error=${JSON.stringify(error instanceof Error ? error.message : String(error))}`,
      );
      throw error;
    }
    if (!response.ok) {
      const detail =
        typeof payload === "object" &&
        payload !== null &&
        "detail" in payload &&
        typeof payload.detail === "string"
          ? payload.detail
          : `Sentia request failed (${String(response.status)})`;
      throw new Error(detail);
    }
    try {
      const parsed = schema.parse(payload);
      this.output.appendLine(
        `[http:complete] id=${String(requestId)} path=${pathname} totalMs=${(performance.now() - startedAt).toFixed(1)}`,
      );
      return parsed;
    } catch (error) {
      this.output.appendLine(
        `[http:error] id=${String(requestId)} path=${pathname} stage=schema_validation error=${JSON.stringify(error instanceof Error ? error.message : String(error))}`,
      );
      throw error;
    }
  }
}

interface ResponseSchema<T> {
  parse(value: unknown): T;
}

type SpeechStreamEvent =
  | { type: "delta"; text: string }
  | { type: "done" }
  | { type: "error"; message: string };

export function parseSpeechStreamEvent(line: string): SpeechStreamEvent {
  let value: unknown;
  try {
    value = JSON.parse(line) as unknown;
  } catch (error) {
    throw new Error(
      `Speech rendering returned malformed JSON: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
  if (typeof value !== "object" || value === null || !("type" in value)) {
    throw new Error("Speech rendering returned an invalid event.");
  }
  if (value.type === "done") {
    return { type: "done" };
  }
  if (
    value.type === "delta" &&
    "text" in value &&
    typeof value.text === "string" &&
    value.text.length > 0
  ) {
    return { type: "delta", text: value.text };
  }
  if (
    value.type === "error" &&
    "message" in value &&
    typeof value.message === "string" &&
    value.message.length > 0
  ) {
    return { type: "error", message: value.message };
  }
  throw new Error("Speech rendering returned an unsupported event.");
}

async function readResponseJson(response: Response): Promise<unknown> {
  const body = await response.text();
  try {
    return JSON.parse(body) as unknown;
  } catch (error) {
    throw new Error(
      `Sentia returned malformed JSON: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

async function findAvailablePort(): Promise<number> {
  return await new Promise<number>((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("Could not allocate a loopback port"));
        return;
      }
      const { port } = address;
      server.close((error) => (error ? reject(error) : resolve(port)));
    });
  });
}
