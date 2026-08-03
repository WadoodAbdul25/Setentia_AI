import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const workspacePath = path.resolve(scriptDirectory, "..");
const extensionPath = path.join(workspacePath, "apps", "extension");
const sidecarPath = path.join(workspacePath, ".venv", "bin", "sentia-sidecar");

if (process.platform !== "darwin") {
  console.error("Sentia's one-command launcher currently supports macOS only.");
  process.exit(1);
}

if (!existsSync(sidecarPath)) {
  console.error(
    "Sentia's Python sidecar is missing. Run `uv sync`, then try `npm start` again.",
  );
  process.exit(1);
}

const codeCandidates = [
  process.env.SENTIA_CODE_CLI,
  "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
  path.join(
    homedir(),
    "Applications",
    "Visual Studio Code.app",
    "Contents",
    "Resources",
    "app",
    "bin",
    "code",
  ),
  "/Applications/Visual Studio Code - Insiders.app/Contents/Resources/app/bin/code",
].filter(Boolean);

const codeCli = codeCandidates.find((candidate) => existsSync(candidate));
if (!codeCli) {
  console.error(
    "Visual Studio Code was not found. Install it in Applications, or set SENTIA_CODE_CLI to its `code` executable.",
  );
  process.exit(1);
}

const launchArguments = [
  "--new-window",
  "--disable-extensions",
  `--extensionDevelopmentPath=${extensionPath}`,
  workspacePath,
];

if (process.argv.includes("--dry-run")) {
  console.log(
    JSON.stringify({ command: codeCli, arguments: launchArguments }, null, 2),
  );
  process.exit(0);
}

console.log("Starting Sentia in a new VS Code Extension Development Host…");
const launch = spawnSync(codeCli, launchArguments, { stdio: "inherit" });

if (launch.error) {
  console.error(`Could not launch VS Code: ${launch.error.message}`);
  process.exit(1);
}
if (launch.status !== 0) {
  process.exit(launch.status ?? 1);
}

console.log("Sentia launched. Use the Sentia icon in the new VS Code window.");
