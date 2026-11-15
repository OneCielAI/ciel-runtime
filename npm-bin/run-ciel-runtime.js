#!/usr/bin/env node
"use strict";

const { spawnSync } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const script = path.join(root, "ciel_runtime.py");
const extra = process.argv.slice(2);
const mode = Object.prototype.hasOwnProperty.call(process.env, "CIEL_RUNTIME_NPM_MODE")
  ? process.env.CIEL_RUNTIME_NPM_MODE
  : "cli";
const minimumPython = { major: 3, minor: 10 };

// The Python every launch runs on at least (user decision 2026-11-15: no
// manual updates; a lower interpreter is replaced automatically). The G:\
// ciel-Walkie routers died twice on 3.14.2 inside CPython (FOR_ITER_LIST on a
// NULL iterator). Each sha256 is pinned here, so a download is trusted only if
// it matches this package.
// - Windows: the official python.org runtime zip (the files Python's own
//   install manager extracts, as listed in
//   https://www.python.org/ftp/python/index-windows.json).
// - Linux and macOS: python.org ships only source code (Linux) and an
//   administrator .pkg installer (macOS), so these use python-build-standalone
//   (github.com/astral-sh/python-build-standalone, the relocatable CPython
//   builds uv installs), release 20261110, "install_only_stripped". Each hash
//   matched both the release's SHA256SUMS and the GitHub asset digest.
const PBS = "https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.14.10%2B20261110-";
const MANAGED_PYTHON = {
  version: "3.14.10",
  win32: {
    x64: {
      url: "https://www.python.org/ftp/python/3.14.10/python-3.14.10-amd64.zip",
      sha256: "cc28026a650970cc1570bb127312f1f9634eb67abfe157ae6ca1acbbc627d091",
    },
    arm64: {
      url: "https://www.python.org/ftp/python/3.14.10/python-3.14.10-arm64.zip",
      sha256: "a7dae9a65d9ffb674d6f3c6d0476cdd10adc77876bb4426007684f8886ac2f77",
    },
  },
  linux: {
    "x64-gnu": {
      url: PBS + "x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
      sha256: "c2ff2e97422f5d3896a16e0317fd212ba977bbf2688d93a6f7a03d817506bd30",
    },
    "arm64-gnu": {
      url: PBS + "aarch64-unknown-linux-gnu-install_only_stripped.tar.gz",
      sha256: "38fd50f07170adc29d481913d1c30b17ecb538fbe290315fe1f83d84fdd5b694",
    },
    "x64-musl": {
      url: PBS + "x86_64-unknown-linux-musl-install_only_stripped.tar.gz",
      sha256: "eff469445ef67410c1bea2370c9d3032dcacb639e9711c891d92b846a7dc5ec3",
    },
    "arm64-musl": {
      url: PBS + "aarch64-unknown-linux-musl-install_only_stripped.tar.gz",
      sha256: "75c04e5fa8105d95f8c84cca05a32b122d000dbbeb4cb4a81a1da5009636a42d",
    },
  },
  darwin: {
    x64: {
      url: PBS + "x86_64-apple-darwin-install_only_stripped.tar.gz",
      sha256: "85f8e54798e8e6aa58d2253f013d815c5616343cc0d15cbea882fca78bdfeae2",
    },
    arm64: {
      url: PBS + "aarch64-apple-darwin-install_only_stripped.tar.gz",
      sha256: "f15ec3d15460dd66c2eb88639b152cd9040e7d1ece11e38bf89e8f53f0171e02",
    },
  },
};
const DOWNLOAD_TIMEOUT_MS = 300000;

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

function parseVersion(text) {
  const match = /^(\d+)\.(\d+)(?:\.(\d+))?/.exec(String(text || "").trim());
  return match ? [Number(match[1]), Number(match[2]), Number(match[3] || 0)] : null;
}

function versionBelow(version, required) {
  const want = parseVersion(required);
  for (let index = 0; index < 3; index++) {
    if (version[index] !== want[index]) return version[index] < want[index];
  }
  return false;
}

function probeVersion(command, prefix) {
  const probe = spawnSync(
    command,
    [...prefix, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
    { encoding: "utf8", stdio: "pipe" },
  );
  if (probe.error) return { error: probe.error };
  if ((probe.status ?? 1) !== 0) {
    const detail = String(probe.stderr || probe.stdout || "").trim();
    return { error: new Error(detail || `${command} ${prefix.join(" ")} version probe failed`) };
  }
  const versionText = String(probe.stdout || "").trim();
  const version = parseVersion(versionText);
  if (!version) {
    return { error: new Error(`Could not parse Python version reported by ${command}: ${versionText || "<empty>"}`) };
  }
  return { version, versionText };
}

function managedDirectory(base, key, platform = process.platform) {
  if (platform === "win32") {
    const local = base || process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local");
    return path.join(local, "ciel-runtime", "python", `${MANAGED_PYTHON.version}-${key}`);
  }
  const data = base || process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share");
  return path.join(data, "ciel-runtime", "python", `${MANAGED_PYTHON.version}-${platform}-${key}`);
}

// musl (Alpine) has no glibcVersionRuntime in the process report.
function linuxLibc() {
  try {
    return process.report.getReport().header.glibcVersionRuntime ? "gnu" : "musl";
  } catch {
    return "gnu";
  }
}

function managedEntry(platform, arch, libc) {
  const table = MANAGED_PYTHON[platform];
  if (!table || typeof table !== "object") return null;
  const key = platform === "linux" ? `${arch}-${libc || linuxLibc()}` : arch;
  return table[key] ? { key, ...table[key] } : null;
}

function managedExecutable(directory, platform) {
  return platform === "win32" ? path.join(directory, "python.exe") : path.join(directory, "python", "bin", "python3");
}

function extractTarGz(archivePath, target) {
  const tar = spawnSync("tar", ["-xzf", archivePath, "-C", target], { encoding: "utf8", stdio: "pipe" });
  if (tar.error || tar.status !== 0) {
    throw new Error("could not extract the Python runtime: " + String(
      (tar.error && tar.error.message) || tar.stderr || "unknown error").trim().slice(0, 300));
  }
}

function extractZip(zipPath, target) {
  const systemRoot = process.env.SystemRoot || process.env.windir || "C:\\Windows";
  // bsdtar ships with Windows 10 1803 and later and reads zip archives.
  const tar = spawnSync(path.join(systemRoot, "System32", "tar.exe"), ["-xf", zipPath, "-C", target],
    { encoding: "utf8", stdio: "pipe" });
  if (!tar.error && tar.status === 0) return;
  const quoted = (value) => "'" + String(value).replace(/'/g, "''") + "'";
  const shell = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command",
    `Expand-Archive -LiteralPath ${quoted(zipPath)} -DestinationPath ${quoted(target)} -Force`],
  { encoding: "utf8", stdio: "pipe" });
  if (shell.error || shell.status !== 0) {
    throw new Error("could not extract the Python runtime: " + String(
      (shell.error && shell.error.message) || shell.stderr || tar.stderr || "unknown error").trim().slice(0, 300));
  }
}

// The managed interpreter for this machine, installing it once when missing.
// Returns its python.exe, or null (with a warning) when it cannot be provided;
// the launch then goes on with the interpreter it already found.
async function ensureManagedPython(reason, options = {}) {
  if (process.env.CIEL_RUNTIME_PYTHON_AUTO_INSTALL === "0") return null;
  const platform = options.platform || process.platform;
  const arch = options.arch || process.arch;
  const resolved = managedEntry(platform, arch, options.libc);
  const entry = resolved ? { ...resolved, ...(options.entry || {}) } : null;
  const log = options.log || ((line) => console.error(line));
  if (!entry) {
    log(`ciel-runtime: ${reason}; no pinned Python ${MANAGED_PYTHON.version} runtime exists for ${platform}/${arch}, ` +
      "so this launch continues with the current Python.");
    return null;
  }
  const probe = options.probe || ((exe) => probeVersion(exe, []));
  const fetchImpl = options.fetch || globalThis.fetch;
  const directory = managedDirectory(options.base, entry.key, platform);
  const executable = managedExecutable(directory, platform);
  const ready = (exe) => {
    if (!fs.existsSync(exe)) return false;
    const found = probe(exe);
    return Boolean(found.version) && found.version.join(".") === MANAGED_PYTHON.version;
  };
  if (ready(executable)) return executable;
  const staging = `${directory}.staging-${process.pid}-${Date.now()}`;
  const zipPath = `${staging}.zip`;
  try {
    log(`ciel-runtime: ${reason}; installing Python ${MANAGED_PYTHON.version} from ${new URL(entry.url).host} ` +
      `into ${directory} (one time, about 30 MB).`);
    fs.mkdirSync(staging, { recursive: true });
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.timeoutMs || DOWNLOAD_TIMEOUT_MS);
    let body;
    try {
      const response = await fetchImpl(entry.url, { signal: controller.signal });
      if (!response.ok) throw new Error(`download failed: HTTP ${response.status}`);
      body = Buffer.from(await response.arrayBuffer());
    } finally {
      clearTimeout(timer);
    }
    const digest = crypto.createHash("sha256").update(body).digest("hex");
    if (digest !== entry.sha256) throw new Error(`download sha256 ${digest} does not match the pinned ${entry.sha256}`);
    fs.writeFileSync(zipPath, body);
    (options.extract || (platform === "win32" ? extractZip : extractTarGz))(zipPath, staging);
    const stagedExe = managedExecutable(staging, platform);
    if (!ready(stagedExe)) throw new Error("the extracted runtime did not report Python " + MANAGED_PYTHON.version);
    try {
      fs.renameSync(staging, directory);
    } catch (error) {
      // Another launch installed it first, or an earlier attempt left a
      // broken directory behind: keep a working one, replace a broken one.
      if (!ready(executable)) {
        fs.rmSync(directory, { recursive: true, force: true });
        fs.renameSync(staging, directory);
      }
    }
    if (!ready(executable)) throw new Error("the installed runtime did not start");
    log(`ciel-runtime: Python ${MANAGED_PYTHON.version} installed; launches use it from now on.`);
    return executable;
  } catch (error) {
    log(`ciel-runtime: automatic Python ${MANAGED_PYTHON.version} install failed (${error.message}); ` +
      "this launch continues with the current Python.");
    return null;
  } finally {
    fs.rmSync(zipPath, { force: true });
    fs.rmSync(staging, { recursive: true, force: true });
  }
}

function run(command, prefix) {
  const scriptArgs = mode ? [mode, ...extra] : extra;
  return spawnSync(command, [...prefix, script, ...scriptArgs], { stdio: "inherit" });
}

async function main() {
  let lastError = null;
  for (const [command, prefix] of candidates()) {
    const probe = probeVersion(command, prefix);
    if (probe.error) {
      lastError = probe.error;
      continue;
    }
    const versionText = probe.versionText;
    const [major, minor] = probe.version;
    let runCommand = command;
    let runPrefix = prefix;
    if (versionBelow(probe.version, MANAGED_PYTHON.version)) {
      const managed = await ensureManagedPython(
        `Python ${versionText} via ${command} is older than the required ${MANAGED_PYTHON.version}`);
      if (managed) {
        runCommand = managed;
        runPrefix = [];
      }
    }
    if (runCommand === command
        && (major < minimumPython.major || (major === minimumPython.major && minor < minimumPython.minor))) {
      lastError = new Error(
        `Found Python ${versionText} via ${command}, but Ciel Runtime requires Python ` +
          `${minimumPython.major}.${minimumPython.minor} or newer.`,
      );
      continue;
    }
    const result = run(runCommand, runPrefix);
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
  // No usable Python at all: the managed runtime is the interpreter.
  const managed = await ensureManagedPython("no usable Python 3.10+ was found");
  if (managed) {
    const result = run(managed, []);
    if (!result.error) process.exit(result.status ?? 0);
    lastError = result.error;
  }
  console.error("Ciel Runtime requires Python 3.10+.");
  if (lastError) {
    console.error(lastError.message);
  }
  process.exit(1);
}

module.exports = { MANAGED_PYTHON, ensureManagedPython, managedDirectory, managedEntry, managedExecutable, parseVersion,
  versionBelow };

if (process.env.CIEL_RUNTIME_LAUNCHER_LIBRARY !== "1") {
  main().catch((error) => {
    console.error(String((error && error.stack) || error));
    process.exit(1);
  });
}
