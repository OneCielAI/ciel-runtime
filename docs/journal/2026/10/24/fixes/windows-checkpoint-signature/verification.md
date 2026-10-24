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

# Not verified

- A live router running this fix. Live routers keep their loaded code until they are relaunched.
