#!/usr/bin/env node
"use strict";

const { spawnSync } = require("node:child_process");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const script = path.join(root, "claude_any.py");
const extra = process.argv.slice(2);
const mode = Object.prototype.hasOwnProperty.call(process.env, "CLAUDE_ANY_NPM_MODE")
  ? process.env.CLAUDE_ANY_NPM_MODE
  : "cli";

function candidates() {
  if (process.env.CLAUDE_ANY_PYTHON) {
    return [[process.env.CLAUDE_ANY_PYTHON, []]];
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
  const probe = spawnSync(command, [...prefix, "--version"], { encoding: "utf8", stdio: "pipe" });
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
    lastError = new Error(detail || `${command} ${prefix.join(" ")} --version failed`);
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

console.error("Claude Any requires Python 3.10+.");
if (lastError) {
  console.error(lastError.message);
}
process.exit(1);
