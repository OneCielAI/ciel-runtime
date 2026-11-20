---
type: fix
title: The finalization and stranded stores retry a refused atomic replace like the ledger
description: Andy's router raised an uncaught PermissionError (WinError 5) when os.replace of the stranded store was refused; the two sidecar stores now retry it as the ledger, native, P0 and receipt stores do.
timestamp: 2026-11-20T12:00:00Z
tags: [work, parallel, windows, store, review]
---

# Report

Found by the live monitor ([live-monitoring](/docs/journal/2026/11/20/operations/live-monitoring/record.md)).

Andy's router log, 2026-11-20T11:38:32Z:

```
router_post_uncaught path=/ca/mcp error=PermissionError: [WinError 5] Access is denied:
'...\work-parallel-stranded-v1.json.<hex>.tmp' -> '...\work-parallel-stranded-v1.json'
```

Stack:
- `_stranded_roll_forward` caught `WorkspaceError: workspace preparation/removal incomplete; retained` from `prepare`.
- It then recorded `stage_failed` with `rows.block`, whose `_change` raised at `os.replace`.

# Cause

Measured on this machine (Windows, Python 3.11):
- `os.replace(tmp, target)` fails with `PermissionError` (WinError 5) while any handle on `target` is open.
- This happened with a plain `open()`, and also with `CreateFileW` using all three share modes.
- After the handle closes, the same replace succeeds.

Confirmed in source:
- `WorkParallelStore` (ledger), the native, P0 and receipts stores retry `os.replace` up to 20 times on `PermissionError`. The ledger has done so since 8eee6e8.
- `WorkParallelFinalizationStore` and `WorkParallelStrandedStore` call `os.replace` once, so the error reached the router as an uncaught error.

Not confirmed: which process held the stranded file open at 11:38:32Z.

# Change

- `work_parallel_primitives.replace_with_retry(tmp, path)`: the ledger's retry, 20 attempts, 2 ms apart, re-raising the last `PermissionError`.
- The finalization and stranded stores use it.

The four stores that already retry are unchanged.

# Monitor

The live monitor read state files with a plain `open`. It now reads under the same advisory lock the router's readers hold (`<file>.lock`, `msvcrt.locking`), and never creates a lock file. With the lock held, the monitor's read cannot overlap a router write, which holds the same lock across its replace.

# Verification

Fail-first, on 97b57b5 plus the vanished-fork change ([evidence/new-tests-fail-first.txt](/docs/journal/2026/11/20/fixes/store-replace-retry/evidence/new-tests-fail-first.txt)):
- `test_a_refused_finalization_replace_is_retried_like_the_ledger`: the router call raises `PermissionError`.
- `test_a_refused_stranded_replace_is_retried_like_the_ledger`: the idle observation raises `PermissionError` while writing the stranded row.

After the change, both pass. ruff: clean.
