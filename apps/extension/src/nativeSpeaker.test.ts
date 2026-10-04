import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";

import type * as vscode from "vscode";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const spawn = vi.hoisted(() => vi.fn());
vi.mock("node:child_process", () => ({ spawn }));

import { NativeSpeaker } from "./nativeSpeaker.js";

function fakeChild() {
  return Object.assign(new EventEmitter(), {
    stdin: new PassThrough(),
    stdout: new PassThrough(),
    stderr: new PassThrough(),
    exitCode: null,
    signalCode: null,
    kill: vi.fn(),
  });
}

function speaker(): NativeSpeaker {
  return new NativeSpeaker(
    {
      asAbsolutePath: (file: string) => file,
      extensionPath: "/fixture",
    } as vscode.ExtensionContext,
    { appendLine: vi.fn() } as unknown as vscode.OutputChannel,
  );
}

describe("native device completion", () => {
  beforeEach(() => {
    spawn.mockReset();
    vi.stubGlobal(
      "process",
      Object.create(process, { platform: { value: "darwin" } }),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("acknowledges actual successful process exit, not finish()", () => {
    const child = fakeChild();
    spawn.mockReturnValue(child);
    const device = speaker();
    const finished = vi.fn();
    device.start(vi.fn(), finished);
    device.write(Buffer.from([0, 0]));
    device.finish();
    expect(finished).not.toHaveBeenCalled();
    child.emit("exit", 0, null);
    expect(finished).toHaveBeenCalledOnce();
  });

  it("ignores a cancelled old process after a new segment starts", () => {
    const oldChild = fakeChild();
    const nextChild = fakeChild();
    spawn.mockReturnValueOnce(oldChild).mockReturnValueOnce(nextChild);
    const device = speaker();
    const finished = vi.fn();
    const error = vi.fn();
    device.start(error, finished);
    device.start(error, finished);
    oldChild.emit("exit", 0, null);
    expect(finished).not.toHaveBeenCalled();
    expect(error).not.toHaveBeenCalled();
    nextChild.emit("exit", 0, null);
    expect(finished).toHaveBeenCalledOnce();
  });

  it("reports a pipe failure without acknowledging playback", () => {
    const child = fakeChild();
    spawn.mockReturnValue(child);
    const device = speaker();
    const finished = vi.fn();
    const error = vi.fn();
    device.start(error, finished);
    child.stdin.emit("error", new Error("EPIPE"));
    expect(error).toHaveBeenCalledOnce();
    expect(finished).not.toHaveBeenCalled();
    device.stop();
  });
});
