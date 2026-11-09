---
type: diagnosis
title: Work store snapshot test fails with WinError 5 on this Windows host
description: read_snapshot held the board file open about 6 ms per call because it read with a 32 MiB size bound; Windows refuses os.replace onto an open file, so the writer's 20 short retries all collided. Chunked bounded reads fix it.
tags: [work-plan-store, windows, os-replace, test-failure]
timestamp: 2026-11-08
---

# Symptom

Local unit group: `test_work_plan_store.WorkPlanStoreTests.test_read_snapshot_observes_whole_atomic_documents_during_writes` raises `PermissionError: [WinError 5]` from `os.replace` in `WorkPlanStore._write_unlocked`.

- It fails every run, on this tree and on an unchanged HEAD extract.
- GitHub CI (ubuntu) passes it.
- It passed in the local unit runs earlier the same day, before the host rebooted from bugcheck `0x7E` at 14:02.

# Measurements (this host, Python 3.11.6)

| Measurement | Result |
| --- | --- |
| Writer alone, 20 replaces | 0 refused |
| Writer with a reader thread doing open/read/close on the target | refused, each replace succeeds within 3–16 ms when retried without limit |
| `os.replace` onto the target while one handle is open (Python `open`, share read/write, share read/write/delete) | refused 30/30 in every case |
| Test pattern (100 `read_snapshot` vs 20 transactions) | the writer's 20 retries span about 24–94 ms, and all 20 are refused |
| Refusals vs reader handle state in that pattern | 79 of 80 refusals happened while the reader's handle was open |
| `read_snapshot` on a 1-node board, no writer | 6.75 ms median |
| Inside it: `BufferedReader.read` | 6 ms of the 6.7 ms |
| `handle.read(32 MiB + 1)` / `read(1 MiB + 1)` / `read()` on a 600-byte file | 5.97 ms / 0.21 ms / 0.03 ms |
| open+read of the same file by name (`.json`, no extension, `.txt`, `.dat`) | 0.024–0.028 ms each; no extension-dependent cost |

`MAX_WORK_PLAN_BYTES` is 32 MiB, and `read_snapshot` calls `handle.read(MAX_WORK_PLAN_BYTES + 1)`. The bounded read costs about 6 ms with the handle open, compared with 0.03 ms for an unbounded read of the same file.

# Mechanism (established)

1. Each `read_snapshot` keeps the board file open for about 6 ms of its 6.7 ms.
2. On this host, Windows refuses `os.replace` onto a file that any handle has open, including a reader that allows delete sharing.
3. `_write_unlocked` retries the replace 20 times with 2 ms sleeps, about 50 ms in all.
4. In the test, the reader re-opens the file 100 times over roughly 650 ms. During the writer's 50 ms window the file is almost always open, so all 20 retries are refused.

The test's assertion is correct for this code path; the writer gives up before the reader stops.

# Not established

- Why the same test passed locally before the reboot. Earlier per-read timings were not recorded. Bounded-read cost or thread scheduling may have differed, but neither was measured then.
- Production impact. None of the local router logs has a `PermissionError` or `WinError 5` for the work plan. Production readers poll less densely than the test.

# Scope

The failing code is from `701ed64` (2026-10-26) and is unrelated to the side handoff change in `8db95e3`.

# Fix

`work_plan_store._read_bounded` reads in 64 KiB chunks up to `MAX_WORK_PLAN_BYTES + 1`. Both `read_snapshot` and the locked `_read_unlocked` use it.
- The size limit and its error text are unchanged.
- The writer's retry policy is unchanged.

| Measurement (same host, same scripts) | Before | After |
| --- | --- | --- |
| One `read_snapshot`, 1-node board | 6.61 ms median | 0.04 ms median |
| Reproduction, 10 runs of the test pattern | 10 failed; 205 replaces refused | 0 failed; 8 replaces refused, each succeeding on retry |
| `test_read_snapshot_observes_whole_atomic_documents_during_writes`, 10 runs | failed 10/10 (8 runs on this tree, 4 on HEAD) | passed 10/10 |

The new regression test `test_bounded_reads_never_request_a_limit_sized_buffer` fails on the old read (it asks for `32 MiB + 1` in one call). It passes with the fix and also checks the size limit with a small chunk size.

# CI groups with the fix (local)

- unit: 3447 OK (50 skipped), including the formerly failing test.
- channel: 607 OK (80 skipped).
- runtime: 458 OK (19 skipped).
- router: 1544 OK, but the runner exited 1.
  - After the tests passed, `scripts/run_test_group.py` failed to delete its isolated temp state: `OSError: [WinError 145] The directory is not empty`. The one leftover entry was `compaction-scope.json.lock`.
  - The runner and that lock file are not touched by this change. The router group exited 0 in the three earlier local runs the same day.

# Same pattern elsewhere (not changed)

`side_context_store.py:44`, `work_parallel.py:134`, `otlp_logs.py:711` and `transient_retry.py:609` also call `read(LIMIT + 1)`. No failure has been observed from them, so they were left as they are.
