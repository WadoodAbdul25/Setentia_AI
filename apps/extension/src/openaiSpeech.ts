import type * as vscode from "vscode";
import type { ReadableStream } from "node:stream/web";

import { NativeSpeaker } from "./nativeSpeaker.js";

const FIRST_CHUNK_MAX_CHARACTERS = 300;
const LATER_CHUNK_TARGET_CHARACTERS = 180;
const LATER_CHUNK_MAX_CHARACTERS = 320;

export interface OpenAISpeechSessionOptions {
  apiKey: string;
  endpoint: string;
  model: string;
  voice: string;
  logPayloads?: boolean;
}

export interface OpenAISpeechOptions extends OpenAISpeechSessionOptions {
  text: string;
}

interface OpenAISpeechTurn {
  options: OpenAISpeechSessionOptions;
  onError: (message: string) => void;
  controller: AbortController;
  speaker: NativeSpeaker;
  chunker: SpeechSentenceChunker;
  queuedChunks: string[];
  startedAt: number;
  firstAudioAt: number | undefined;
  nextChunkIndex: number;
  processing: boolean;
  flushed: boolean;
  settled: boolean;
  completion: Promise<void>;
  resolveCompletion: () => void;
}

export class SpeechSentenceChunker {
  private pending = "";
  private readonly completeSentences: string[] = [];
  private emittedFirstChunk = false;

  append(delta: string): string[] {
    if (!delta) {
      return [];
    }
    this.pending += delta;
    this.collectCompleteSentences();
    return this.releaseReadyChunks(false);
  }

  flush(): string[] {
    this.collectCompleteSentences();
    const remainder = normalizeSpeechText(this.pending);
    this.pending = "";
    if (remainder) {
      this.completeSentences.push(remainder);
    }
    return this.releaseReadyChunks(true);
  }

  private collectCompleteSentences(): void {
    const { sentences, remainder } = takeCompleteSentences(this.pending);
    this.pending = remainder;
    this.completeSentences.push(...sentences);
  }

  private releaseReadyChunks(flush: boolean): string[] {
    const chunks: string[] = [];
    if (!this.emittedFirstChunk && this.completeSentences.length > 0) {
      chunks.push(this.takeChunk(FIRST_CHUNK_MAX_CHARACTERS, true));
      this.emittedFirstChunk = true;
    }

    while (this.completeSentences.length > 0) {
      const readyCharacters = this.completeSentences.reduce(
        (total, sentence) => total + sentence.length + 1,
        0,
      );
      if (!flush && readyCharacters < LATER_CHUNK_TARGET_CHARACTERS) {
        break;
      }
      chunks.push(this.takeChunk(LATER_CHUNK_MAX_CHARACTERS, false));
    }
    return chunks;
  }

  private takeChunk(
    maxCharacters: number,
    preferSingleSentence: boolean,
  ): string {
    const first = this.completeSentences.shift();
    if (first === undefined) {
      return "";
    }
    const { head, tail } = splitBoundedText(first, maxCharacters);
    if (tail) {
      this.completeSentences.unshift(tail);
    }
    if (preferSingleSentence || tail) {
      return head;
    }

    let chunk = head;
    while (this.completeSentences.length > 0) {
      const next = this.completeSentences[0]!;
      if (chunk.length + next.length + 1 > maxCharacters) {
        break;
      }
      chunk += ` ${this.completeSentences.shift()!}`;
    }
    return chunk;
  }
}

export class OpenAISpeech implements vscode.Disposable {
  private turn: OpenAISpeechTurn | undefined;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly output: vscode.OutputChannel,
    private readonly fetcher: typeof fetch = fetch,
    private readonly speakerFactory: () => NativeSpeaker = () =>
      new NativeSpeaker(context, output),
  ) {}

  async speak(
    options: OpenAISpeechOptions,
    onError: (message: string) => void,
  ): Promise<void> {
    const text = options.text.trim();
    if (!text) {
      return;
    }
    this.beginTurn(options, onError);
    this.appendText(text);
    await this.flushTurn();
  }

  beginTurn(
    options: OpenAISpeechSessionOptions,
    onError: (message: string) => void,
  ): void {
    this.stop();
    const speaker = this.speakerFactory();
    let resolveCompletion = (): void => undefined;
    const completion = new Promise<void>((resolve) => {
      resolveCompletion = resolve;
    });
    const turn: OpenAISpeechTurn = {
      options,
      onError,
      controller: new AbortController(),
      speaker,
      chunker: new SpeechSentenceChunker(),
      queuedChunks: [],
      startedAt: performance.now(),
      firstAudioAt: undefined,
      nextChunkIndex: 0,
      processing: false,
      flushed: false,
      settled: false,
      completion,
      resolveCompletion,
    };
    speaker.start((message) => {
      if (this.turn === turn) {
        this.failTurn(turn, message);
      }
    });
    this.turn = turn;
    this.logMetric("turn_started");
  }

  appendText(delta: string): void {
    const turn = this.requireTurn();
    if (turn.flushed) {
      throw new Error(
        "Cannot append OpenAI speech text after the turn was flushed.",
      );
    }
    this.enqueueChunks(turn, turn.chunker.append(delta));
  }

  flushTurn(): Promise<void> {
    const turn = this.requireTurn();
    if (!turn.flushed) {
      turn.flushed = true;
      this.enqueueChunks(turn, turn.chunker.flush());
      this.logMetric("turn_flushed", {
        queuedChunks: turn.queuedChunks.length,
        elapsedMs: performance.now() - turn.startedAt,
      });
      this.finishTurnIfReady(turn);
    }
    return turn.completion;
  }

  abortTurn(): void {
    this.stop();
  }

  stop(): void {
    const turn = this.turn;
    this.turn = undefined;
    if (!turn) {
      return;
    }
    turn.controller.abort();
    turn.speaker.stop();
    this.settleTurn(turn);
    this.logMetric("turn_cancelled", {
      elapsedMs: performance.now() - turn.startedAt,
    });
  }

  dispose(): void {
    this.stop();
  }

  private enqueueChunks(turn: OpenAISpeechTurn, chunks: string[]): void {
    turn.queuedChunks.push(...chunks.filter(Boolean));
    if (turn.queuedChunks.length > 0) {
      void this.processQueue(turn);
    }
  }

  private async processQueue(turn: OpenAISpeechTurn): Promise<void> {
    if (turn.processing) {
      return;
    }
    turn.processing = true;
    try {
      while (this.turn === turn && turn.queuedChunks.length > 0) {
        const text = turn.queuedChunks.shift()!;
        const chunkIndex = turn.nextChunkIndex++;
        await this.synthesizeChunk(turn, text, chunkIndex);
      }
    } catch (error) {
      if (this.turn !== turn || turn.controller.signal.aborted) {
        return;
      }
      this.failTurn(
        turn,
        error instanceof Error ? error.message : String(error),
      );
    } finally {
      turn.processing = false;
      this.finishTurnIfReady(turn);
    }
  }

  private async synthesizeChunk(
    turn: OpenAISpeechTurn,
    text: string,
    chunkIndex: number,
  ): Promise<void> {
    const requestStartedAt = performance.now();
    const requestUrl = buildOpenAISpeechUrl(turn.options.endpoint);
    this.logMetric("chunk_started", {
      chunkIndex,
      inputCharacters: text.length,
    });
    this.output.appendLine(
      `[speech:openai:request] chunk=${String(chunkIndex)} method=POST url=${requestUrl} model=${turn.options.model} voice=${turn.options.voice} inputCharacters=${String(text.length)}${turn.options.logPayloads === true ? ` input=${JSON.stringify(text)}` : ""}`,
    );
    const response = await this.fetcher(requestUrl, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${turn.options.apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: turn.options.model,
        voice: turn.options.voice,
        input: text,
        instructions:
          "Speak naturally and warmly. Preserve every fact, code identifier, and technical term. Do not add or remove information.",
        response_format: "pcm",
      }),
      signal: turn.controller.signal,
    });
    this.output.appendLine(
      `[speech:openai:response] chunk=${String(chunkIndex)} status=${String(response.status)} statusText=${JSON.stringify(response.statusText)} contentType=${JSON.stringify(response.headers.get("content-type"))} contentLength=${JSON.stringify(response.headers.get("content-length"))} requestId=${JSON.stringify(response.headers.get("x-request-id"))} headersMs=${(performance.now() - requestStartedAt).toFixed(1)}`,
    );
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(
        `OpenAI speech request failed (${String(response.status)}): ${detail.slice(0, 300)}`,
      );
    }
    if (!response.body) {
      throw new Error("OpenAI speech returned no audio stream.");
    }
    this.logMetric("chunk_headers", {
      chunkIndex,
      elapsedMs: performance.now() - requestStartedAt,
    });

    const reader = (
      response.body as ReadableStream<Uint8Array<ArrayBuffer>>
    ).getReader();
    let audioBytes = 0;
    try {
      while (this.turn === turn) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        if (value.byteLength === 0) {
          continue;
        }
        if (turn.firstAudioAt === undefined) {
          turn.firstAudioAt = performance.now();
          this.logMetric("first_audio", {
            timeToFirstAudioMs: turn.firstAudioAt - turn.startedAt,
          });
        }
        audioBytes += value.byteLength;
        turn.speaker.write(
          Buffer.from(value.buffer, value.byteOffset, value.byteLength),
        );
      }
    } finally {
      reader.releaseLock();
    }
    this.logMetric("chunk_completed", {
      chunkIndex,
      audioBytes,
      elapsedMs: performance.now() - requestStartedAt,
    });
    this.output.appendLine(
      `[speech:openai:response-complete] chunk=${String(chunkIndex)} audioBytes=${String(audioBytes)} elapsedMs=${(performance.now() - requestStartedAt).toFixed(1)}`,
    );
  }

  private finishTurnIfReady(turn: OpenAISpeechTurn): void {
    if (
      this.turn !== turn ||
      !turn.flushed ||
      turn.processing ||
      turn.queuedChunks.length > 0 ||
      turn.settled
    ) {
      return;
    }
    turn.speaker.finish();
    this.settleTurn(turn);
    this.logMetric("turn_completed", {
      chunks: turn.nextChunkIndex,
      elapsedMs: performance.now() - turn.startedAt,
    });
  }

  private failTurn(turn: OpenAISpeechTurn, message: string): void {
    if (this.turn !== turn) {
      return;
    }
    this.turn = undefined;
    turn.controller.abort();
    turn.speaker.stop();
    this.settleTurn(turn);
    this.output.appendLine(`[speech] ${message}`);
    turn.onError(message);
  }

  private settleTurn(turn: OpenAISpeechTurn): void {
    if (turn.settled) {
      return;
    }
    turn.settled = true;
    turn.resolveCompletion();
  }

  private requireTurn(): OpenAISpeechTurn {
    if (!this.turn) {
      throw new Error("OpenAI speech does not have an active turn.");
    }
    return this.turn;
  }

  private logMetric(name: string, fields?: Record<string, number>): void {
    const values = fields
      ? Object.entries(fields)
          .map(([key, value]) => `${key}=${value.toFixed(1)}`)
          .join(" ")
      : "";
    this.output.appendLine(
      `[speech:metric] openai_${name}${values ? ` ${values}` : ""}`,
    );
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

function takeCompleteSentences(value: string): {
  sentences: string[];
  remainder: string;
} {
  const sentences: string[] = [];
  let sentenceStart = 0;
  for (let index = 0; index < value.length; index += 1) {
    if (!".!?".includes(value[index]!)) {
      continue;
    }
    let sentenceEnd = index + 1;
    while (
      sentenceEnd < value.length &&
      `"')]} `.includes(value[sentenceEnd]!) &&
      !/\s/u.test(value[sentenceEnd]!)
    ) {
      sentenceEnd += 1;
    }
    if (sentenceEnd < value.length && !/\s/u.test(value[sentenceEnd]!)) {
      continue;
    }
    const sentence = normalizeSpeechText(
      value.slice(sentenceStart, sentenceEnd),
    );
    if (sentence) {
      sentences.push(sentence);
    }
    while (sentenceEnd < value.length && /\s/u.test(value[sentenceEnd]!)) {
      sentenceEnd += 1;
    }
    sentenceStart = sentenceEnd;
    index = sentenceEnd - 1;
  }
  return { sentences, remainder: value.slice(sentenceStart) };
}

function splitBoundedText(
  value: string,
  maxCharacters: number,
): { head: string; tail: string } {
  if (value.length <= maxCharacters) {
    return { head: value, tail: "" };
  }
  const candidate = value.slice(0, maxCharacters + 1);
  const whitespace = candidate.lastIndexOf(" ");
  const splitAt =
    whitespace >= Math.floor(maxCharacters * 0.6) ? whitespace : maxCharacters;
  return {
    head: value.slice(0, splitAt).trim(),
    tail: value.slice(splitAt).trim(),
  };
}

function normalizeSpeechText(value: string): string {
  return value.replace(/\s+/gu, " ").trim();
}
