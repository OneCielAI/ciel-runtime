#!/usr/bin/env node
"use strict";

const { spawnSync } = require("node:child_process");
const path = require("node:path");

if (process.env.CIEL_RUNTIME_SKIP_POSTINSTALL_STOP === "1") {
  process.exit(0);
}

const root = path.resolve(__dirname, "..");
const script = path.join(root, "ciel_runtime.py");

function candidates() {
  if (process.env.CIEL_RUNTIME_PYTHON) {
    return [[process.env.CIEL_RUNTIME_PYTHON, []]];
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

for (const [command, prefix] of candidates()) {
  const probe = spawnSync(command, [...prefix, "--version"], { encoding: "utf8", stdio: "pipe", timeout: 5000 });
  if (probe.error || (probe.status ?? 1) !== 0) {
    continue;
  }
  const result = spawnSync(command, [...prefix, script, "cli", "stop"], {
    encoding: "utf8",
    stdio: "ignore",
    timeout: 10000,
  });
  if (!result.error) {
    process.exit(0);
  }
}

process.exit(0);
