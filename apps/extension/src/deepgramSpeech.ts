import type { RawData } from "ws";
import WebSocket from "ws";

import type * as vscode from "vscode";

import { NativeSpeaker } from "./nativeSpeaker.js";

export interface DeepgramSpeechSessionOptions {
  apiKey: string;
  endpoint: string;
  model: string;
  speed?: number;
}

export interface DeepgramSpeechOptions extends DeepgramSpeechSessionOptions {
  text: string;
}

type DeepgramSpeechFamily = "flux" | "aura";

interface SpeechTurn {
  family: DeepgramSpeechFamily;
  speaker: NativeSpeaker;
  onError: (message: string) => void;
  startedAt: number;
  firstAudioAt: number | undefined;
  audioBytes: number;
  inputCharacters: number;
  flushed: boolean;
  discarded: boolean;
  settled: boolean;
  completion: Promise<void>;
  resolveCompletion: () => void;
}

export class DeepgramSpeech implements vscode.Disposable {
  private socket: WebSocket | undefined;
  private connectionKey: string | undefined;
  private heartbeat: NodeJS.Timeout | undefined;
  private turn: SpeechTurn | undefined;
  private playbackSpeaker: NativeSpeaker | undefined;
  private connectionOpenedAt: number | undefined;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly output: vscode.OutputChannel,
    private readonly socketFactory: (
      url: string,
      apiKey: string,
    ) => WebSocket = (url, apiKey) =>
      new WebSocket(url, {
        headers: { Authorization: `Token ${apiKey}` },
      }),
    private readonly speakerFactory: () => NativeSpeaker = () =>
      new NativeSpeaker(context, output),
  ) {}

  async speak(
    options: DeepgramSpeechOptions,
    onError: (message: string) => void,
  ): Promise<void> {
    const text = options.text.trim();
    if (!text) {
      return;
    }
    await this.beginTurn(options, onError);
    this.appendText(text);
    this.flushTurn();
  }

  async beginTurn(
    options: DeepgramSpeechSessionOptions,
    onError: (message: string) => void,
  ): Promise<void> {
    const previousTurn = this.turn;
    if (previousTurn) {
      await previousTurn.completion;
    }
    await this.ensureConnection(options);
    if (this.turn) {
      throw new Error("Deepgram speech already has an active turn.");
    }

    this.playbackSpeaker?.stop();
    const speaker = this.speakerFactory();
    let resolveCompletion = (): void => undefined;
    const completion = new Promise<void>((resolve) => {
      resolveCompletion = resolve;
    });
    const turn: SpeechTurn = {
      family: deepgramSpeechFamily(options.model),
      speaker,
      onError,
      startedAt: performance.now(),
      firstAudioAt: undefined,
      audioBytes: 0,
      inputCharacters: 0,
      flushed: false,
      discarded: false,
      settled: false,
      completion,
      resolveCompletion,
    };
    speaker.start((message) => {
      if (this.turn === turn) {
        this.failTurn(message);
      }
    });
    this.playbackSpeaker = speaker;
    this.turn = turn;
    this.logMetric("turn_started", {
      family: turn.family,
      connectionAgeMs:
        this.connectionOpenedAt === undefined
          ? undefined
          : performance.now() - this.connectionOpenedAt,
    });
  }

  appendText(text: string): void {
    if (!text) {
      return;
    }
    const socket = this.requireOpenSocket();
    const turn = this.requireTurn();
    if (turn.flushed) {
      throw new Error("Cannot append speech text after the turn was flushed.");
    }
    socket.send(JSON.stringify({ type: "Speak", text }));
    turn.inputCharacters += text.length;
  }

  flushTurn(): void {
    const socket = this.requireOpenSocket();
    const turn = this.requireTurn();
    if (turn.flushed) {
      return;
    }
    turn.flushed = true;
    socket.send(JSON.stringify({ type: "Flush" }));
    this.logMetric("turn_flushed", {
      inputCharacters: turn.inputCharacters,
      elapsedMs: performance.now() - turn.startedAt,
    });
  }

  cancelPlayback(): void {
    const turn = this.turn;
    const speaker = this.playbackSpeaker;
    if (!turn && !speaker) {
      return;
    }
    if (turn) {
      turn.discarded = true;
    }
    speaker?.stop();
    this.playbackSpeaker = undefined;
    this.logMetric("playback_cancelled", {
      family: turn?.family,
      audioBytes: turn?.audioBytes,
      elapsedMs:
        turn === undefined ? undefined : performance.now() - turn.startedAt,
    });
    if (turn?.family === "aura" && this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: "Clear" }));
    }
  }

  abortTurn(): void {
    this.closeSession();
  }

  stop(): void {
    this.closeSession();
  }

  closeSession(): void {
    this.clearHeartbeat();
    const socket = this.socket;
    this.socket = undefined;
    this.connectionKey = undefined;
    this.connectionOpenedAt = undefined;
    const turn = this.turn;
    this.turn = undefined;
    if (turn) {
      turn.discarded = true;
      turn.speaker.stop();
      this.settleTurn(turn);
    }
    this.playbackSpeaker?.stop();
    this.playbackSpeaker = undefined;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "Close" }));
    }
    socket?.close();
  }

  dispose(): void {
    this.closeSession();
  }

  private async ensureConnection(
    options: DeepgramSpeechSessionOptions,
  ): Promise<void> {
    const url = buildDeepgramSpeechUrl(
      options.endpoint,
      options.model,
      options.speed,
    );
    const connectionKey = `${url}\u0000${options.apiKey}`;
    if (
      this.connectionKey === connectionKey &&
      this.socket?.readyState === WebSocket.OPEN
    ) {
      return;
    }
    this.closeSession();

    const socket = this.socketFactory(url, options.apiKey);
    this.socket = socket;
    this.connectionKey = connectionKey;
    const connectingAt = performance.now();
    this.installSocketHandlers(socket);
    await new Promise<void>((resolve, reject) => {
      const opened = (): void => {
        cleanup();
        resolve();
      };
      const failed = (error: Error): void => {
        cleanup();
        reject(error);
      };
      const closed = (): void => {
        cleanup();
        reject(new Error("Deepgram closed while speech was connecting."));
      };
      const cleanup = (): void => {
        socket.off("open", opened);
        socket.off("error", failed);
        socket.off("close", closed);
      };
      socket.once("open", opened);
      socket.once("error", failed);
      socket.once("close", closed);
    });
    if (this.socket !== socket) {
      throw new Error("Deepgram speech connection was replaced while opening.");
    }
    this.connectionOpenedAt = performance.now();
    this.heartbeat = setInterval(() => {
      if (this.socket === socket && socket.readyState === WebSocket.OPEN) {
        socket.ping();
      }
    }, 10_000);
    this.logMetric("connection_opened", {
      family: deepgramSpeechFamily(options.model),
      connectMs: performance.now() - connectingAt,
    });
  }

  private installSocketHandlers(socket: WebSocket): void {
    socket.on("message", (data: RawData, isBinary: boolean) => {
      if (this.socket !== socket) {
        return;
      }
      if (isBinary) {
        this.handleAudio(rawDataToBuffer(data));
        return;
      }
      this.handleControl(rawDataToBuffer(data).toString("utf8"));
    });
    socket.on("error", (error) => {
      if (
        this.socket === socket &&
        socket.readyState !== WebSocket.CONNECTING
      ) {
        this.failTurn(`Deepgram speech failed: ${error.message}`);
      }
    });
    socket.on("close", (code, reason) => {
      if (this.socket !== socket) {
        return;
      }
      this.socket = undefined;
      this.connectionKey = undefined;
      this.connectionOpenedAt = undefined;
      this.clearHeartbeat();
      if (this.turn) {
        this.failTurn(
          `Deepgram speech connection closed (${String(code)}${reason.length ? `: ${reason.toString()}` : ""}).`,
        );
      }
    });
  }

  private handleAudio(audio: Buffer): void {
    const turn = this.turn;
    if (!turn || audio.length === 0) {
      return;
    }
    if (turn.firstAudioAt === undefined) {
      turn.firstAudioAt = performance.now();
      this.logMetric("first_audio", {
        timeToFirstAudioMs: turn.firstAudioAt - turn.startedAt,
      });
    }
    turn.audioBytes += audio.length;
    if (!turn.discarded) {
      turn.speaker.write(audio);
    }
  }

  private handleControl(message: string): void {
    let payload: {
      type?: unknown;
      description?: unknown;
      code?: unknown;
      speech_id?: unknown;
      audio_duration_ms?: unknown;
      input_character_count?: unknown;
    };
    try {
      payload = JSON.parse(message) as typeof payload;
    } catch (error) {
      this.output.appendLine(
        `[speech] ignored malformed Deepgram message: ${String(error)}`,
      );
      return;
    }
    if (payload.type === "Error") {
      this.failTurn(
        typeof payload.description === "string"
          ? payload.description
          : "Deepgram could not synthesize the response.",
      );
      return;
    }
    if (payload.type === "Warning") {
      this.output.appendLine(
        `[speech] Deepgram warning${typeof payload.code === "string" ? ` ${payload.code}` : ""}: ${typeof payload.description === "string" ? payload.description : "No description."}`,
      );
      return;
    }
    if (payload.type === "SpeechStarted") {
      this.logMetric("speech_started", {
        speechId:
          typeof payload.speech_id === "string" ? payload.speech_id : undefined,
        elapsedMs:
          this.turn === undefined
            ? undefined
            : performance.now() - this.turn.startedAt,
      });
      return;
    }
    if (payload.type === "Flushed") {
      this.logMetric("server_flushed", {
        family: this.turn?.family,
        elapsedMs:
          this.turn === undefined
            ? undefined
            : performance.now() - this.turn.startedAt,
      });
      if (this.turn?.family === "aura") {
        this.finishTurn(payload);
      }
      return;
    }
    if (payload.type === "Cleared") {
      if (this.turn?.family === "aura") {
        this.finishTurn(payload);
      }
      return;
    }
    if (payload.type === "SpeechMetadata") {
      this.finishTurn(payload);
      return;
    }
    if (payload.type === "SessionMetadata") {
      this.logMetric("session_metadata", {});
    }
  }

  private finishTurn(payload: {
    audio_duration_ms?: unknown;
    input_character_count?: unknown;
  }): void {
    const turn = this.turn;
    if (!turn) {
      return;
    }
    this.turn = undefined;
    if (!turn.discarded) {
      turn.speaker.finish();
    }
    this.logMetric("turn_completed", {
      family: turn.family,
      elapsedMs: performance.now() - turn.startedAt,
      audioBytes: turn.audioBytes,
      audioDurationMs:
        typeof payload.audio_duration_ms === "number"
          ? payload.audio_duration_ms
          : undefined,
      inputCharacters:
        typeof payload.input_character_count === "number"
          ? payload.input_character_count
          : turn.inputCharacters,
      discarded: turn.discarded,
    });
    this.settleTurn(turn);
  }

  private failTurn(message: string): void {
    const turn = this.turn;
    this.output.appendLine(`[speech] ${message}`);
    if (turn) {
      this.turn = undefined;
      turn.discarded = true;
      turn.speaker.stop();
      if (this.playbackSpeaker === turn.speaker) {
        this.playbackSpeaker = undefined;
      }
      this.settleTurn(turn);
      turn.onError(message);
    }
    this.closeSession();
  }

  private settleTurn(turn: SpeechTurn): void {
    if (!turn.settled) {
      turn.settled = true;
      turn.resolveCompletion();
    }
  }

  private requireOpenSocket(): WebSocket {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      throw new Error("Deepgram speech is not connected.");
    }
    return this.socket;
  }

  private requireTurn(): SpeechTurn {
    if (!this.turn) {
      throw new Error("Deepgram speech has no active turn.");
    }
    return this.turn;
  }

  private clearHeartbeat(): void {
    if (this.heartbeat) {
      clearInterval(this.heartbeat);
      this.heartbeat = undefined;
    }
  }

  private logMetric(
    name: string,
    fields: Record<string, string | number | boolean | undefined>,
  ): void {
    const values = Object.entries(fields)
      .filter(
        (entry): entry is [string, string | number | boolean] =>
          entry[1] !== undefined,
      )
      .map(
        ([key, value]) =>
          `${key}=${typeof value === "number" ? value.toFixed(1) : String(value)}`,
      )
      .join(" ");
    this.output.appendLine(
      `[speech:metric] ${name}${values ? ` ${values}` : ""}`,
    );
  }
}

export function deepgramSpeechFamily(model: string): DeepgramSpeechFamily {
  if (model.startsWith("flux-")) {
    return "flux";
  }
  if (model.startsWith("aura-")) {
    return "aura";
  }
  throw new Error("Deepgram TTS model must start with flux- or aura-.");
}

export function buildDeepgramSpeechUrl(
  endpoint: string,
  model: string,
  speed = 1,
): string {
  const family = deepgramSpeechFamily(model);
  const url = new URL(endpoint.trim());
  if (url.protocol === "https:") {
    url.protocol = "wss:";
  } else if (url.protocol === "http:") {
    url.protocol = "ws:";
  }
  if (url.protocol !== "wss:" && url.protocol !== "ws:") {
    throw new Error("Deepgram speech endpoint must use ws:// or wss://.");
  }
  if (family === "aura" && (speed < 0.7 || speed > 1.5)) {
    throw new Error("Deepgram Aura-2 speed must be between 0.7 and 1.5.");
  }
  const basePath = url.pathname.replace(
    /\/(?:v[12]\/(?:listen|speak))?\/?$/,
    "",
  );
  url.pathname = `${basePath}/${family === "flux" ? "v2" : "v1"}/speak`;
  url.search = "";
  url.searchParams.set("model", model);
  url.searchParams.set("encoding", "linear16");
  url.searchParams.set("sample_rate", "24000");
  if (family === "aura") {
    url.searchParams.set("speed", String(speed));
  }
  return url.toString();
}

function rawDataToBuffer(data: RawData): Buffer {
  if (Array.isArray(data)) {
    return Buffer.concat(data);
  }
  if (data instanceof ArrayBuffer) {
    return Buffer.from(data);
  }
  return Buffer.from(data.buffer, data.byteOffset, data.byteLength);
}
