---
type: Verification
title: Prody deferred resume fix verification
description: Focused deterministic proof that the authorization crosses the deferred spawn boundary once.
timestamp: 2026-11-10T04:22:00Z
tags: [work-parallel, tests, prody]
---

# Automated

| Check | Result |
|---|---|
| `tests/test_work_mod_write_gate.py` | 50 tests passed |
| `ruff` on the Python test | clean |
| `git diff --check` on the three changed files | clean |

The new harness scenario deliberately separates the two callbacks:

1. `SendMessage` returns `{sent: true}`.
2. Only after that return, a native engine `agent.spawn` arrives with the exact persisted name.
3. The spawn is accepted and returns `agent-new`.
4. A second identical spawn is rejected with `no pending work_parallel_run dispatch`.

This reproduces the ordering seen in Prody's transcript and proves that the exact one-shot authorization survives that boundary.

# Live application boundary

No files, ledger rows, processes, or sessions on Prody were changed. Applying a release or repairing the three already-stuck leases requires an explicit managed-machine change authorization. The code path is therefore verified locally, but the Prody operation itself is not marked complete here.
