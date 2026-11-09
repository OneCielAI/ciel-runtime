---
type: Verification
title: Work parallel dispatch and side review — test evidence
description: Focused and full CI-group results; real-session proof and Prody ledger repair are recorded separately.
timestamp: 2026-11-09T20:40:00Z
tags: [work-parallel, side-actor, tests]
---

# Automated

| Check | Result |
|---|---|
| Focused: `test_work_parallel`, `test_work_parallel_mcp`, `test_work_parallel_menu`, `test_side_work_decision`, `test_work_mod_write_gate`, settings menus | all pass |
| `unit` group (inherited `CIEL_RUNTIME_*` unset) | 3484 tests OK (51 skipped) |
| `router` group | 1550 OK |
| `channel` group | 607 OK (80 skipped) |
| `runtime` group | 458 OK (19 skipped) |
| ruff (changed files) | clean |
| `claude plugin validate` | passed |
| `claude plugin test` | 23 pass / 77 fail, identical on the pre-change tree `069d57e` (baseline, not introduced here) |
| Live routers 9467/9505/9524 | same PIDs and health 200 before and after the groups |

The first unit run failed one test: the lease owner was taken from the router's bound scope even when that scope belonged to another conversation. The owner is now recorded only when the bound scope is the reserving conversation (`test_owner_comes_only_from_this_conversations_bound_scope`).

# Test cases added

- Store:
  - token hash only;
  - `unreported` frees the slot and the item can be dispatched again;
  - a dead owner is released;
  - unknown owner is refused;
  - a legacy lease is released only with operator observations.
- Router:
  - root priority overrides caller input;
  - the token report needs no host identity and cannot be replayed;
  - `reserve_next` skips waiting leaves;
  - orphan preview mutates nothing.
- Plugin harness:
  - an unreported fork is stopped with TaskStop, its notification is consumed, and the next runnable item starts with `cwd` = worktree and its exact note and token;
  - a side review blocks Stop once and goes through the host bridge.
- Side:
  - two events for Walkie message 119649 → one actor, the other is `duplicate_source` and not in the outbox;
  - a side answer leaves a pending review until main marks it.
- Menu: the orphan row previews read-only, `apply` releases only dead owners, and `legacy <id>` releases only the confirmed legacy lease.

# Pending

- Real owned console proof, with screenshots.
- Beta release and local install.
- Prody ledger repair after the user shuts Prody down.
