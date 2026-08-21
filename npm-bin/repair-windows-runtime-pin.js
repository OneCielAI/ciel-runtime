#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

function normalizedWindowsPath(value) {
  return path.win32.resolve(String(value || "")).replace(/[\\/]+$/, "").toLowerCase();
}

function pathIsWithin(candidate, parent) {
  const child = normalizedWindowsPath(candidate);
  const root = normalizedWindowsPath(parent);
  return Boolean(child && root && (child === root || child.startsWith(`${root}\\`)));
}

function shouldRepairRuntimeHome(runtimeHome, temporaryRoot, exists = fs.existsSync) {
  if (!runtimeHome || !temporaryRoot || !pathIsWithin(runtimeHome, temporaryRoot)) {
    return false;
  }
  return !exists(path.win32.join(runtimeHome, "ciel_runtime.py"));
}

function registeredRuntimeHome() {
  const result = spawnSync(
    "reg.exe",
    ["query", "HKCU\\Environment", "/v", "CIEL_RUNTIME_HOME"],
    { encoding: "utf8", windowsHide: true },
  );
  if ((result.status ?? 1) !== 0) {
    return "";
  }
  const match = /^\s*CIEL_RUNTIME_HOME\s+REG_(?:SZ|EXPAND_SZ)\s+(.+)$/im.exec(
    String(result.stdout || ""),
  );
  return match ? match[1].trim() : "";
}

function repairWindowsRuntimePin() {
  if (process.platform !== "win32") {
    return false;
  }
  const current = registeredRuntimeHome();
  if (!shouldRepairRuntimeHome(current, os.tmpdir())) {
    return false;
  }
  const packageRoot = path.resolve(__dirname, "..");
  const result = spawnSync(
    "reg.exe",
    [
      "add",
      "HKCU\\Environment",
      "/v",
      "CIEL_RUNTIME_HOME",
      "/t",
      "REG_SZ",
      "/d",
      packageRoot,
      "/f",
    ],
    { encoding: "utf8", windowsHide: true },
  );
  if ((result.status ?? 1) !== 0) {
    const detail = String(result.stderr || result.stdout || "").trim();
    console.warn(`Could not repair stale CIEL_RUNTIME_HOME: ${detail || "reg.exe failed"}`);
    return false;
  }
  console.log(`Repaired stale CIEL_RUNTIME_HOME to ${packageRoot}`);
  return true;
}

if (require.main === module) {
  repairWindowsRuntimePin();
}

module.exports = {
  pathIsWithin,
  repairWindowsRuntimePin,
  shouldRepairRuntimeHome,
};
