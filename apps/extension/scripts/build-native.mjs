import { spawnSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { arch, platform } from "node:process";
import { fileURLToPath } from "node:url";

if (platform !== "darwin") {
  process.exit(0);
}

const extensionRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const outputDirectory = path.join(extensionRoot, "bin");
mkdirSync(outputDirectory, { recursive: true });

function buildHelper(sourceName, plistName, outputName, identifier) {
  const source = path.join(extensionRoot, "native", "macos", sourceName);
  const infoPlist = path.join(extensionRoot, "native", "macos", plistName);
  const output = path.join(outputDirectory, outputName);
  const compile = spawnSync(
    "xcrun",
    [
      "clang",
      "-O",
      "-fobjc-arc",
      `-fmodules-cache-path=${path.join(extensionRoot, ".module-cache")}`,
      "-framework",
      "AVFoundation",
      "-framework",
      "AudioToolbox",
      "-framework",
      "Foundation",
      `-Wl,-sectcreate,__TEXT,__info_plist,${infoPlist}`,
      source,
      "-o",
      output,
    ],
    { cwd: extensionRoot, encoding: "utf8" },
  );
  if (compile.status !== 0) {
    process.stderr.write(compile.stderr || compile.stdout);
    process.exit(compile.status ?? 1);
  }

  const sign = spawnSync(
    "codesign",
    ["--force", "--sign", "-", "--identifier", identifier, output],
    { cwd: extensionRoot, encoding: "utf8" },
  );
  if (sign.status !== 0) {
    process.stderr.write(sign.stderr || sign.stdout);
    process.exit(sign.status ?? 1);
  }
}

buildHelper("sentia-mic.m", "Info.plist", "sentia-mic", "ai.sentia.microphone");
buildHelper(
  "sentia-speaker.m",
  "SpeakerInfo.plist",
  "sentia-speaker",
  "ai.sentia.speaker",
);
process.stdout.write(`built macOS audio helpers (${arch})\n`);
