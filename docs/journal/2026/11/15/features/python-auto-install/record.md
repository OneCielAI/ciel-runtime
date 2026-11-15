---
type: implementation-record
title: The npm launcher installs the pinned Python when the machine's is older
description: User decision 2026-11-15, client machines cannot be updated by hand, so a launch below Python 3.14.10 (or without Python) installs the official python.org runtime on Windows and runs on it.
timestamp: 2026-11-15T17:30:00Z
tags: [python, launcher, npm, windows, auto-install]
---

# Why

The G:\ciel-Walkie router died twice on Python 3.14.2 inside CPython (see [router crash recovery](../../fixes/router-crash-recovery/record.md)). Nothing in the ciel-runtime update path touches Python:

- the npm package, `install.ps1` and `install.sh` copy only ciel-runtime files;
- the README asks for Python 3.10 or newer;
- every machine runs whatever `CIEL_RUNTIME_PYTHON`, `py -3` or `python3` gives it.

The user ruled that machines cannot be updated by hand.

# Behavior

`npm-bin/run-ciel-runtime.js` is the single entry for `ciel-runtime`, `ciel-runtimectl` and `ciel-runtime-stop`.

**The pin**
- `MANAGED_PYTHON.version = 3.14.10`.
- Windows x64 and arm64 each have a python.org runtime zip URL and its sha256, copied from `https://www.python.org/ftp/python/index-windows.json`. Python's own install manager extracts the same zips.

**When it installs**
- The selected interpreter reports a version below the pin. This includes an explicit `CIEL_RUNTIME_PYTHON`.
- Or no usable Python 3.10+ is found at all.

**How it installs**
1. Download the zip with a 300 s timeout.
2. Compare its sha256 with the pinned value. On a mismatch, install nothing.
3. Extract to a staging directory, using `System32\tar.exe` with `Expand-Archive` as a fallback.
4. Check that the staged `python.exe` reports 3.14.10.
5. Rename it into `%LOCALAPPDATA%\ciel-runtime\python\3.14.10-<arch>`. If a concurrent launch got there first, keep its working copy.
6. Run with that interpreter. Later launches find it and reuse it.

**When it does not install**
- On any failure it prints one warning and continues with the interpreter it found. A launch never fails because of the install.
- `CIEL_RUNTIME_PYTHON_AUTO_INSTALL=0` turns it off.
- Linux and macOS print a notice and continue. No auto-install exists there.

The router is spawned with the launcher's `sys.executable`, so it runs on the managed interpreter too.

# Evidence

**Real install on this PC**
- Setup: a temporary `LOCALAPPDATA` and `CIEL_RUNTIME_PYTHON=Python 3.11.6`.
- First launch: `Python 3.11.6 … is older than the required 3.14.10; installing …`, then `Python 3.14.10 installed`, `ciel-runtime 0.2.51`, exit 0, in 5 s.
- Second launch: 2 s, no download.
- The script the launcher started printed `interpreter …\ciel-runtime\python\3.14.10-x64\python.exe 3.14.10`.

**Tests**
- `tests/test_npm_launcher_python.py`: 6 passed. Download, extraction and probe are injected. Covered:
  - version cases: 3.14.2, 3.13.5, 3.11.6 and 3.10.0 are replaced; 3.14.10, 3.14.11 and 3.15.2 are kept;
  - pinned official URLs;
  - verify, install and reuse, with no staging left behind;
  - a hash mismatch installs nothing and the launch goes on;
  - other platforms and the opt-out variable install nothing.
- Results: 13 passed on Windows together with the install diagnostics, 16 on Linux (node 24 + Python 3.11).

**ciel-runtime on Python 3.14.10**

| Group | Tests | Result |
| --- | --- | --- |
| runtime | 458 | OK (19 skipped) |
| channel | 626 | OK (80 skipped) |
| router | 1672 | OK |
| fault-log tests | 3 | OK |

# Open

- **Updating the pin:** a later release changes the version and both hashes together.
- **Auto-install off Windows:** Linux and macOS would need another trusted source, which is not decided.
