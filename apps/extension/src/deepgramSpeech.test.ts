import { EventEmitter } from "node:events";

import type * as vscode from "vscode";
import type WebSocket from "ws";
import { describe, expect, it } from "vitest";

import type { NativeSpeaker } from "./nativeSpeaker.js";
import { buildDeepgramSpeechUrl, DeepgramSpeech } from "./deepgramSpeech.js";

describe("buildDeepgramSpeechUrl", () => {
  it("uses the Flux TTS v2 endpoint with raw 24 kHz audio", () => {
    expect(
      buildDeepgramSpeechUrl(
        "https://api.eu.deepgram.com/v2/listen",
        "flux-marcus-en",
      ),
    ).toBe(
      "wss://api.eu.deepgram.com/v2/speak?model=flux-marcus-en&encoding=linear16&sample_rate=24000",
    );
  });

  it("preserves a dedicated endpoint base path", () => {
    expect(
      buildDeepgramSpeechUrl(
        "wss://deepgram.example.com/sentia",
        "flux-haley-en",
      ),
    ).toContain("/sentia/v2/speak?model=flux-haley-en");
  });

  it("uses Aura-2 v1 and applies explicit speed control", () => {
    expect(
      buildDeepgramSpeechUrl("wss://api.deepgram.com", "aura-2-thalia-en", 0.9),
    ).toBe(
      "wss://api.deepgram.com/v1/speak?model=aura-2-thalia-en&encoding=linear16&sample_rate=24000&speed=0.9",
    );
  });

  it("does not send Aura-only speed controls to Flux", () => {
    expect(
      buildDeepgramSpeechUrl("wss://api.deepgram.com", "flux-bruce-en", 0.9),
    ).not.toContain("speed=");
  });

  it("rejects unsupported model families", () => {
    expect(() =>
      buildDeepgramSpeechUrl("wss://api.deepgram.com", "unknown-voice"),
    ).toThrow("must start with flux- or aura-");
  });
});

describe("DeepgramSpeech", () => {
  it("reuses one Flux socket across completed speech turns", async () => {
    class FakeSocket extends EventEmitter {
      readyState = 0;
      readonly sent: string[] = [];

      send(message: string): void {
        this.sent.push(message);
      }

      ping(): void {}

      close(): void {
        this.readyState = 3;
        this.emit("close", 1000, Buffer.alloc(0));
      }

      open(): void {
        this.readyState = 1;
        this.emit("open");
      }

      control(payload: object): void {
        this.emit("message", Buffer.from(JSON.stringify(payload)), false);
      }

      audio(payload: Buffer): void {
        this.emit("message", payload, true);
      }
    }

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

    const sockets: FakeSocket[] = [];
    const speakers: FakeSpeaker[] = [];
    const output: string[] = [];
    const speech = new DeepgramSpeech(
      {} as vscode.ExtensionContext,
      {
        appendLine: (line: string): void => {
          output.push(line);
        },
      } as unknown as vscode.OutputChannel,
      () => {
        const socket = new FakeSocket();
        sockets.push(socket);
        return socket as unknown as WebSocket;
      },
      () => {
        const speaker = new FakeSpeaker();
        speakers.push(speaker);
        return speaker as unknown as NativeSpeaker;
      },
    );
    const options = {
      apiKey: "deepgram-test-key", // pragma: allowlist secret
      endpoint: "wss://api.deepgram.com",
      model: "flux-bruce-en",
    };

    const first = speech.beginTurn(options, () => undefined);
    sockets[0]!.open();
    await first;
    speech.appendText("First answer.");
    speech.flushTurn();
    sockets[0]!.control({ type: "SpeechStarted", speech_id: "dg_sp_first" });
    sockets[0]!.audio(Buffer.from([1, 2, 3, 4]));
    sockets[0]!.control({
      type: "SpeechMetadata",
      speech_id: "dg_sp_first",
      audio_duration_ms: 100,
      input_character_count: 13,
    });

    await speech.beginTurn(options, () => undefined);
    speech.appendText("Second answer.");
    speech.flushTurn();

    expect(sockets).toHaveLength(1);
    expect(speakers).toHaveLength(2);
    expect(speakers[0]!.audio).toEqual([Buffer.from([1, 2, 3, 4])]);
    expect(speakers[0]!.finishes).toBe(1);
    expect(sockets[0]!.sent).toEqual([
      JSON.stringify({ type: "Speak", text: "First answer." }),
      JSON.stringify({ type: "Flush" }),
      JSON.stringify({ type: "Speak", text: "Second answer." }),
      JSON.stringify({ type: "Flush" }),
    ]);
    expect(output.some((line) => line.includes("turn_completed"))).toBe(true);

    speech.closeSession();
  });
});
