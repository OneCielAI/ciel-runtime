#!/usr/bin/env node
"use strict";

const { spawnSync } = require("node:child_process");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const script = path.join(root, "ciel_runtime.py");
const extra = process.argv.slice(2);
const mode = Object.prototype.hasOwnProperty.call(process.env, "CIEL_RUNTIME_NPM_MODE")
  ? process.env.CIEL_RUNTIME_NPM_MODE
  : "cli";
const minimumPython = { major: 3, minor: 10 };

function candidates() {
  const configuredPython = process.env.CIEL_RUNTIME_PYTHON;
  if (configuredPython) {
    return [[configuredPython, []]];
  }
  if (process.platform === "win32") {
    return [
      ["py", ["-3"]],
      ["python", []],
      ["python3", []],
    ];
  }
  return [
    ["python3", []],
    ["python", []],
  ];
}

let lastError = null;
for (const [command, prefix] of candidates()) {
  const probe = spawnSync(
    command,
    [...prefix, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
    { encoding: "utf8", stdio: "pipe" },
  );
  if (probe.error && probe.error.code === "ENOENT") {
    lastError = probe.error;
    continue;
  }
  if (probe.error) {
    lastError = probe.error;
    continue;
  }
  if ((probe.status ?? 1) !== 0) {
    const detail = String(probe.stderr || probe.stdout || "").trim();
    lastError = new Error(detail || `${command} ${prefix.join(" ")} version probe failed`);
    continue;
  }
  const versionText = String(probe.stdout || "").trim();
  const versionMatch = /^(\d+)\.(\d+)(?:\.(\d+))?/.exec(versionText);
  if (!versionMatch) {
    lastError = new Error(`Could not parse Python version reported by ${command}: ${versionText || "<empty>"}`);
    continue;
  }
  const major = Number(versionMatch[1]);
  const minor = Number(versionMatch[2]);
  if (major < minimumPython.major || (major === minimumPython.major && minor < minimumPython.minor)) {
    lastError = new Error(
      `Found Python ${versionText} via ${command}, but Ciel Runtime requires Python ` +
        `${minimumPython.major}.${minimumPython.minor} or newer.`,
    );
    continue;
  }
  const scriptArgs = mode ? [mode, ...extra] : extra;
  const args = [...prefix, script, ...scriptArgs];
  const result = spawnSync(command, args, { stdio: "inherit" });
  if (result.error && result.error.code === "ENOENT") {
    lastError = result.error;
    continue;
  }
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  process.exit(result.status ?? 0);
}

console.error("Ciel Runtime requires Python 3.10+.");
if (lastError) {
  console.error(lastError.message);
}
process.exit(1);
