import type { RawData } from "ws";
import WebSocket from "ws";

import type * as vscode from "vscode";

import { NativeSpeaker } from "./nativeSpeaker.js";

export interface DeepgramSpeechOptions {
  apiKey: string;
  endpoint: string;
  model: string;
  text: string;
}

export class DeepgramSpeech implements vscode.Disposable {
  private socket: WebSocket | undefined;
  private speaker: NativeSpeaker | undefined;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly output: vscode.OutputChannel,
  ) {}

  speak(
    options: DeepgramSpeechOptions,
    onError: (message: string) => void,
  ): void {
    this.stop();
    const text = options.text.trim();
    if (!text) {
      return;
    }

    const speaker = new NativeSpeaker(this.context, this.output);
    const fail = (message: string): void => {
      if (this.speaker !== speaker) {
        return;
      }
      this.output.appendLine(`[speech] ${message}`);
      this.stop();
      onError(message);
    };
    speaker.start(fail);
    this.speaker = speaker;

    const socket = new WebSocket(
      buildDeepgramSpeechUrl(options.endpoint, options.model),
      { headers: { Authorization: `Token ${options.apiKey}` } },
    );
    this.socket = socket;
    let audioComplete = false;

    socket.once("open", () => {
      socket.send(JSON.stringify({ type: "Speak", text }));
      socket.send(JSON.stringify({ type: "Flush" }));
    });
    socket.on("message", (data: RawData, isBinary: boolean) => {
      if (this.socket !== socket) {
        return;
      }
      if (isBinary) {
        speaker.write(rawDataToBuffer(data));
        return;
      }
      try {
        const payload = JSON.parse(rawDataToBuffer(data).toString("utf8")) as {
          type?: unknown;
          description?: unknown;
        };
        if (payload.type === "Error") {
          fail(
            typeof payload.description === "string"
              ? payload.description
              : "Deepgram could not synthesize the response.",
          );
          return;
        }
        if (payload.type === "SpeechMetadata") {
          audioComplete = true;
          speaker.finish();
          if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: "Close" }));
          }
        }
      } catch (error) {
        this.output.appendLine(
          `[speech] ignored malformed Deepgram message: ${String(error)}`,
        );
      }
    });
    socket.once("error", (error) =>
      fail(`Deepgram speech failed: ${error.message}`),
    );
    socket.once("close", () => {
      if (this.socket === socket) {
        this.socket = undefined;
      }
      if (!audioComplete && this.speaker === speaker) {
        fail("Deepgram speech ended before the audio was complete.");
      }
    });
  }

  stop(): void {
    const socket = this.socket;
    this.socket = undefined;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "Close" }));
    }
    socket?.close();
    this.speaker?.stop();
    this.speaker = undefined;
  }

  dispose(): void {
    this.stop();
  }
}

export function buildDeepgramSpeechUrl(
  endpoint: string,
  model: string,
): string {
  const url = new URL(endpoint.trim());
  if (url.protocol === "https:") {
    url.protocol = "wss:";
  } else if (url.protocol === "http:") {
    url.protocol = "ws:";
  }
  if (url.protocol !== "wss:" && url.protocol !== "ws:") {
    throw new Error("Deepgram speech endpoint must use ws:// or wss://.");
  }
  const basePath = url.pathname.replace(
    /\/(?:v[12]\/(?:listen|speak))?\/?$/,
    "",
  );
  url.pathname = `${basePath}/v2/speak`;
  url.search = "";
  url.searchParams.set("model", model);
  url.searchParams.set("encoding", "linear16");
  url.searchParams.set("sample_rate", "24000");
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
