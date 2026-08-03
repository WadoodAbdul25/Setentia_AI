import type * as vscode from "vscode";
import type { ReadableStream } from "node:stream/web";

import { NativeSpeaker } from "./nativeSpeaker.js";

export interface OpenAISpeechOptions {
  apiKey: string;
  endpoint: string;
  model: string;
  voice: string;
  text: string;
}

export class OpenAISpeech implements vscode.Disposable {
  private controller: AbortController | undefined;
  private speaker: NativeSpeaker | undefined;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly output: vscode.OutputChannel,
  ) {}

  async speak(
    options: OpenAISpeechOptions,
    onError: (message: string) => void,
  ): Promise<void> {
    this.stop();
    const text = options.text.trim();
    if (!text) {
      return;
    }

    const controller = new AbortController();
    this.controller = controller;
    try {
      const response = await fetch(buildOpenAISpeechUrl(options.endpoint), {
        method: "POST",
        headers: {
          Authorization: `Bearer ${options.apiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          model: options.model,
          voice: options.voice,
          input: text,
          instructions:
            "Speak naturally and warmly. Preserve every fact, code identifier, and technical term. Do not add or remove information.",
          response_format: "pcm",
        }),
        signal: controller.signal,
      });
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(
          `OpenAI speech request failed (${String(response.status)}): ${detail.slice(0, 300)}`,
        );
      }
      if (!response.body) {
        throw new Error("OpenAI speech returned no audio stream.");
      }

      const speaker = new NativeSpeaker(this.context, this.output);
      this.speaker = speaker;
      speaker.start((message) => {
        if (this.speaker === speaker) {
          this.fail(message, onError);
        }
      });
      const reader = (
        response.body as ReadableStream<Uint8Array<ArrayBuffer>>
      ).getReader();
      while (this.controller === controller) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        if (value.byteLength > 0) {
          speaker.write(
            Buffer.from(value.buffer, value.byteOffset, value.byteLength),
          );
        }
      }
      if (this.controller === controller) {
        this.controller = undefined;
        speaker.finish();
      }
    } catch (error) {
      if (this.controller !== controller || controller.signal.aborted) {
        return;
      }
      this.fail(
        error instanceof Error ? error.message : String(error),
        onError,
      );
    }
  }

  stop(): void {
    this.controller?.abort();
    this.controller = undefined;
    this.speaker?.stop();
    this.speaker = undefined;
  }

  dispose(): void {
    this.stop();
  }

  private fail(message: string, onError: (message: string) => void): void {
    this.output.appendLine(`[speech] ${message}`);
    this.stop();
    onError(message);
  }
}

export function buildOpenAISpeechUrl(endpoint: string): string {
  const url = new URL(endpoint.trim());
  if (url.protocol !== "https:" && url.protocol !== "http:") {
    throw new Error("OpenAI speech endpoint must use http:// or https://.");
  }
  const basePath = url.pathname.replace(
    /\/(?:v1\/(?:audio\/speech|realtime))?\/?$/,
    "",
  );
  url.pathname = `${basePath}/v1/audio/speech`;
  url.search = "";
  return url.toString();
}
