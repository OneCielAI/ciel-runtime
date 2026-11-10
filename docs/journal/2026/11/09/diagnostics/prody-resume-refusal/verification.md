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

# Beta and installed-package proof

- CI and npm Publish passed for commit `0f1e47b`.
- Registry beta `0.2.51-beta.20261110-042134.0f1e47b` has matching git head and SHA512; all 519 source files match.
- `latest` remains `0.2.51`; `nightly` remains `0.2.51-nightly.20261106-020333.c00c8b6`.
- The verified immutable snapshot and global npm install match byte-for-byte across 573 files.
- The installed snapshot reran `tests/test_work_mod_write_gate.py`: 50 passed.
- User `CIEL_RUNTIME_HOME` now points to the immutable `0f1e47b…` snapshot. Existing live routers were not restarted.

# Managed-machine boundary

No files, ledger rows, processes, or sessions on Prody were changed. Applying the release there or repairing the three already-stuck leases requires an explicit managed-machine change authorization. The code and installed package are verified locally, but the Prody operation itself is not marked complete here.
