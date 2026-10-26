---
type: Fix
title: Consistent transcript signatures for native checkpoint scans
description: The checkpoint scan compared os.fstat with Path.stat including st_ctime_ns, which differ on Windows for Python 3.12+, so every scan was discarded and compaction admission never reopened.
tags: [compaction, windows, python, delivery, sse]
timestamp: 2026-10-24T12:00:00Z
---
# Problem

- Diagnosis: [celly SSE injection stall](../../diagnostics/celly-sse-injection/finding.md).
- `native_checkpoint_from_transcript` compared the `os.fstat` signature with `Path.stat()`, `st_ctime_ns` included. `_signature()` used `Path.stat()` for the post-scan validity check.
- On Windows, `os.fstat().st_ctime_ns` is the change time and `Path.stat().st_ctime_ns` is the creation time, on Python 3.12–3.14 ([CPython #157671](https://github.com/python/cpython/issues/157671)). Every scan therefore returned `None`.
- Effect: the 05:49:15 compact boundary was never accepted, admission stayed `blocked / checkpoint_unconfirmed`, and SSE inputs were deferred `context_recovery`.

# Change

- `_signature()` reopens the path and reads `os.fstat` of that handle. The scan's own handle and the path check now use the same field meanings.
- `st_dev`, `st_ino`, `st_size`, `st_mtime_ns` and `st_ctime_ns` are all still compared. A replaced, truncated or appended file still invalidates the proof.

# Verification (✅)

- Exact-file isolated runs ([runner](evidence/run_focused.py); CIEL/CLAUDE/ANTHROPIC/CODEX/OPENAI env removed, temp homes):
  - Python 3.13: 395 tests OK, 42 skipped, across checkpoint evidence, contention, control, binding, channel pending injection and architecture contracts ([output](evidence/tests-py313.txt)).
  - Python 3.11: 66 tests OK ([output](evidence/tests-py311.txt)).
  - Python 3.14: 99 tests OK, including large-transcript identity ([output](evidence/tests-py314.txt)).
- New regressions:
  - A `Path.stat` that reports a different `st_ctime_ns` no longer discards the checkpoint, and the scalar cache is reused.
  - A changed path identity after the scan still discards it.
- ruff, metadata check, compileall and `npm pack --dry-run` all pass ([checks](evidence/static-checks.txt)).
- Replay of celly's pre-restart `blocked` state with the worktree code on Python 3.13 ([result](evidence/celly-replica-fixed-py313.txt)):
  - `native_checkpoint_observed`, then `checkpoint_and_history_shrink_verified evidence=next_request_shrink` (223680 tokens).
  - Admission opened on the second call; the first call was a scan conflict while the live transcript was being appended.
  - The state used was a private temp copy, and the live transcript was only read.

# Release

- **Nightly.** `5d4dc0e` was pushed as a fast-forward from `acd43e9`. CI run 40091101319 and Publish run 40091101316 both succeeded.
- **Registry.** The `nightly` tag is `0.2.51-nightly.20261024-115611.5d4dc0e`, with gitHead `5d4dc0e8f07644e9d6f81f143ca644990e00e28b`.
- **Local install** ([deploy_local.py](evidence/deploy_local.py), [deployment](evidence/deployment.json), [output](evidence/deploy-output.txt)):
  - The exact version was packed and extracted with member checks, then installed with `npm install -g`.
  - 454 files were replaced one at a time, atomically, into `~/.local/share/ciel-runtime` and `~/.local/bin`. `install.ps1` was not used.
  - `runtime_compaction_control.py` has the same hash (`3b173a8e…`) in git, the tarball, the snapshot and global npm. No `*.deploy-*.tmp` file was left behind.
- **Installed-path tests** ([installed-tests.txt](evidence/installed-tests.txt)): 99 tests OK on Python 3.14, importing `~/.local/share/ciel-runtime/ciel_runtime_support/runtime_compaction_control.py`. `ciel-runtime --version` reports the new version.
- **Live routers.** The five live routers kept their PIDs and the old version:
  - 9385 → 54704
  - 9464 → 54764
  - 9489 → 67368
  - 9505 → 5188
  - 9524 → 67176
- No session was restarted. The fix applies only to sessions launched after this install.

# Celly after its update

- **07:03–11:03, still on `a7e28f9`.** The 07:03:38 native compaction was never confirmed, and admission ended `blocked / checkpoint_unconfirmed`.
- **11:02:47.** The user updated celly's package to `5d4dc0e`. `runtime_compaction_control.py` hash is `3b173a8e…`.
- **11:03:33.** Router pid 6032 started on the new version.
  - At launch, 6772–6789 (18 inputs queued during the stall) were marked `skipped / launch_replay_window_expired` and not replayed.
- **11:03:36–11:05:41.** External input was held by the launch identity grace (`request_identity_pending`, 120 s), then `request_identity_expired` ungated it.
- **11:05:45.** 6790–6793 were injected together over the session socket and confirmed, and all four are `replied`. Delivery health shows 0 queued.
- **Checkpoint scan.** celly's installed module on Python 3.13 returns the 07:05:37 auto checkpoint 3/3 ([scan](evidence/celly-installed-scan-py313.txt)). The pre-update code returned None for the same file.
- **Not yet observed.** A native compaction released by the new code inside celly's live router.
