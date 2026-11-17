---
type: fix-record
title: The side release-notice harness read its results after a fixed 20 ms, which failed under CI load
description: Publish run 44108671990 (5c4b85a) skipped publish because test_busy_main_reads_the_release_in_its_running_turn saw no appended notice. The harness printed its results 20 ms after completeSide returned; the notice is delivered after that. It now waits until the notice is recorded.
timestamp: 2026-11-17T15:30:00Z
tags: [ci, flaky-test, side-intake, release-notice, harness]
---

# What failed

The publish run for `5c4b85a` (run 44108671990) skipped its publish step because one test failed in `test (unit, 3/3)`. The test was `test_busy_main_reads_the_release_in_its_running_turn`, with `AssertionError: 1 != 0`: the harness's `appended` list was empty. See [evidence/ci-failure.txt](evidence/ci-failure.txt).

`5c4b85a` does not touch `side-intake.js` or this test. It removes the tool-guard Stop hook.

# Cause (reproduced)

`tests/side_release_harness.mjs` printed its results 20 ms after `completeSide` returned. The release notice is appended asynchronously after that call returns.

The main-busy case was repeated many times ([evidence/repeat-runs.txt](evidence/repeat-runs.txt)):

| Setup | Failures |
|---|---|
| Windows | 0 of 60 |
| Linux container, 1 CPU, idle | 0 of 100 |
| Linux container, 1 CPU, 4 busy-loop processes | 16 of 100 |

# Fix

The harness waits until the outcome has settled before it prints, with a 5-second cap. Settled means:
- the fork message was sent (fork case);
- `notice_appended` was recorded (main-busy, fork-parked and fork-refuses);
- `notice_appended` was recorded and the wake prompt was submitted (main-idle).

It then keeps the old 20 ms window, so a duplicate notice or prompt would still be caught.

The same fix pattern was used for `prompt-key-dedup` in `cadd1a5`. `side-intake.js` is unchanged.

# Verification

- **Linux, same loaded setup:** 0 failures in 100 runs.
- **Windows:** `tests/test_side_release_notice.py` 5/5.
