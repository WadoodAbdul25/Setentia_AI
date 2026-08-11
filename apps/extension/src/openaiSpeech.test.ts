import type * as vscode from "vscode";
import { describe, expect, it } from "vitest";

import type { NativeSpeaker } from "./nativeSpeaker.js";
import {
  buildOpenAISpeechUrl,
  OpenAISpeech,
  SpeechSentenceChunker,
} from "./openaiSpeech.js";

describe("buildOpenAISpeechUrl", () => {
  it("uses the OpenAI speech endpoint", () => {
    expect(buildOpenAISpeechUrl("https://api.openai.com")).toBe(
      "https://api.openai.com/v1/audio/speech",
    );
  });

  it("normalizes an existing realtime path", () => {
    expect(
      buildOpenAISpeechUrl("https://voice.example.com/gateway/v1/realtime"),
    ).toBe("https://voice.example.com/gateway/v1/audio/speech");
  });

  it("rejects WebSocket origins", () => {
    expect(() => buildOpenAISpeechUrl("wss://api.openai.com")).toThrow();
  });
});

describe("SpeechSentenceChunker", () => {
  it("releases the first sentence immediately and groups later sentences", () => {
    const chunker = new SpeechSentenceChunker();

    expect(chunker.append("Sentia starts speaking quickly.")).toEqual([
      "Sentia starts speaking quickly.",
    ]);
    expect(chunker.append(" A second sentence waits.")).toEqual([]);
    expect(chunker.append(" A third sentence joins it.")).toEqual([]);
    expect(chunker.flush()).toEqual([
      "A second sentence waits. A third sentence joins it.",
    ]);
  });

  it("waits for a complete sentence across formatter deltas", () => {
    const chunker = new SpeechSentenceChunker();

    expect(chunker.append("The repository scanner applies ignore")).toEqual([]);
    expect(chunker.append(" rules before reading files.")).toEqual([
      "The repository scanner applies ignore rules before reading files.",
    ]);
    expect(chunker.flush()).toEqual([]);
  });
});

describe("OpenAISpeech", () => {
  it("synthesizes ordered sentence groups into one persistent speaker", async () => {
    class FakeSpeaker {
      readonly audio: Buffer[] = [];
      finishes = 0;
      stops = 0;

      start(): void {}

      write(chunk: Buffer): void {
        this.audio.push(chunk);
      }

      finish(): void {
        this.finishes += 1;
      }

      stop(): void {
        this.stops += 1;
      }
    }

    const requests: Array<Record<string, unknown>> = [];
    const speaker = new FakeSpeaker();
    const output: string[] = [];
    const fetcher = ((_input, init) => {
      if (typeof init?.body !== "string") {
        throw new Error("Expected a JSON request body.");
      }
      requests.push(JSON.parse(init.body) as Record<string, unknown>);
      const marker = requests.length;
      return Promise.resolve(new Response(Uint8Array.from([marker, marker])));
    }) satisfies typeof fetch;
    const speech = new OpenAISpeech(
      {} as vscode.ExtensionContext,
      {
        appendLine: (line: string): void => {
          output.push(line);
        },
      } as unknown as vscode.OutputChannel,
      fetcher,
      () => speaker as unknown as NativeSpeaker,
    );

    speech.beginTurn(
      {
        apiKey: "openai-test-key", // pragma: allowlist secret
        endpoint: "https://api.openai.com",
        model: "gpt-4o-mini-tts",
        voice: "marin",
        logPayloads: true,
      },
      () => undefined,
    );
    speech.appendText("The first sentence starts playback.");
    speech.appendText(" The second sentence is grouped.");
    speech.appendText(" The third sentence stays with it.");
    await speech.flushTurn();

    expect(requests.map((request) => request.input)).toEqual([
      "The first sentence starts playback.",
      "The second sentence is grouped. The third sentence stays with it.",
    ]);
    expect(speaker.audio).toEqual([Buffer.from([1, 1]), Buffer.from([2, 2])]);
    expect(speaker.finishes).toBe(1);
    expect(speaker.stops).toBe(0);
    expect(output.some((line) => line.includes("openai_first_audio"))).toBe(
      true,
    );
    expect(output.some((line) => line.includes("openai_turn_completed"))).toBe(
      true,
    );
    expect(
      output.some(
        (line) =>
          line.includes("[speech:openai:request]") &&
          line.includes('input="The first sentence starts playback."'),
      ),
    ).toBe(true);
    expect(
      output.some(
        (line) =>
          line.includes("[speech:openai:response]") &&
          line.includes("status=200"),
      ),
    ).toBe(true);
    expect(
      output.some((line) => line.includes("[speech:openai:response-complete]")),
    ).toBe(true);
  });

  it("aborts an in-flight chunk without reporting a playback error", async () => {
    class FakeSpeaker {
      stops = 0;

      start(): void {}
      write(): void {}
      finish(): void {}
      stop(): void {
        this.stops += 1;
      }
    }

    let requestStarted = (): void => undefined;
    const started = new Promise<void>((resolve) => {
      requestStarted = resolve;
    });
    const fetcher: typeof fetch = (_input, init) =>
      new Promise((_resolve, reject) => {
        requestStarted();
        init?.signal?.addEventListener("abort", () => {
          reject(new Error("aborted"));
        });
      });
    const speaker = new FakeSpeaker();
    const errors: string[] = [];
    const speech = new OpenAISpeech(
      {} as vscode.ExtensionContext,
      { appendLine: (): void => undefined } as unknown as vscode.OutputChannel,
      fetcher,
      () => speaker as unknown as NativeSpeaker,
    );

    speech.beginTurn(
      {
        apiKey: "openai-test-key", // pragma: allowlist secret
        endpoint: "https://api.openai.com",
        model: "gpt-4o-mini-tts",
        voice: "marin",
      },
      (message) => errors.push(message),
    );
    speech.appendText("This sentence starts a request.");
    await started;
    speech.stop();

    expect(speaker.stops).toBe(1);
    expect(errors).toEqual([]);
  });

  it("stops the turn and surfaces an OpenAI synthesis failure", async () => {
    class FakeSpeaker {
      stops = 0;

      start(): void {}
      write(): void {}
      finish(): void {}
      stop(): void {
        this.stops += 1;
      }
    }

    const speaker = new FakeSpeaker();
    const errors: string[] = [];
    const speech = new OpenAISpeech(
      {} as vscode.ExtensionContext,
      { appendLine: (): void => undefined } as unknown as vscode.OutputChannel,
      () =>
        Promise.resolve(new Response("provider unavailable", { status: 503 })),
      () => speaker as unknown as NativeSpeaker,
    );

    speech.beginTurn(
      {
        apiKey: "openai-test-key", // pragma: allowlist secret
        endpoint: "https://api.openai.com",
        model: "gpt-4o-mini-tts",
        voice: "marin",
      },
      (message) => errors.push(message),
    );
    speech.appendText("This complete sentence reaches OpenAI.");
    await speech.flushTurn();

    expect(speaker.stops).toBe(1);
    expect(errors).toEqual([
      "OpenAI speech request failed (503): provider unavailable",
    ]);
  });
});
