---
type: fix
title: A submitted fork that leaves the native list gets its review without a restart
description: Celly n1070/n1135 and KOBE n878 waited for review because a same-process fork absent from the list was never observed terminal, and an idle row after a recorded failed status was skipped.
timestamp: 2026-11-20T11:50:00Z
tags: [work, parallel, review, native-observation, restart]
---

# Report

Found by the live monitor ([live-monitoring](/docs/journal/2026/11/20/operations/live-monitoring/record.md)).

- Celly n1070 (lease 78d4b4ba):
  - 10:42:48Z completion receipt. The fork was listed `running` until 10:40:44Z and absent from 10:47:14Z, with no idle row in between.
  - Main's approve at 10:57:18Z was refused (`positive matching idle workspace proof required`).
  - The review opened only after Celly's 11:14Z restart. n1135 (4ac18b2a) followed the same path.
- KOBE n878 (lease a78fac3f):
  - The run's native status was recorded `failed` at 11:01:34Z. The completion receipt landed at 11:09:00Z, and the fork was listed `idle` at 11:09:48Z.
  - No review row existed at 11:21Z.

# Cause

Confirmed in source (c955630):

1. The mod's `parallelRestartSubmitted` skipped every fork in `processNativeAgents`. A fork this process spawned or listed was never observed terminal, even when it was absent from the complete list and its run held the exact completion receipt. Only a later process could publish its review.
2. The router's `native_observation` ran the c955630 idle publication only when `_observe_coordination_row` succeeded.
   - `update_native` refuses `idle` after a recorded `failed` ("terminal native status is immutable").
   - So KOBE's idle row was skipped and the review was never published.

# Change

- Router (`work_parallel_mcp.py`): a refused native update no longer skips `_publish_native_idle_review` for that row. The publication still requires all of these:
  - a submitted, slot-free lease;
  - the row's agent is the lease's `host_id`;
  - the run belongs to that agent and holds a `completion` exit;
  - no review row exists yet.
- Mod (`register.js`):
  - `parallelRestartSubmitted` no longer skips this process's own forks. It still skips any fork listed in the current list, and the router still checks the exact run, agent, generation and receipt, and refuses another live owner.
  - `parallelObserveNative` (the 15-second timer) runs `parallelRestartSubmitted` with the list it just read. When that observed a fork, or the router published a review, it projects reviews at once.

# Verification

Fail-first, on 97b57b5 ([evidence/new-tests-fail-first.txt](/docs/journal/2026/11/20/fixes/vanished-fork-review/evidence/new-tests-fail-first.txt)):
- `test_native_idle_row_publishes_even_when_the_run_was_marked_failed_first` fails with the KOBE refusal: `skipped ... terminal native status is immutable`, no review.
- `test_own_fork_absent_after_its_receipt_is_observed_terminal_without_a_restart` (mod scenario `own-fork-absent-after-receipt`) fails: no `restart_submitted_terminal` was sent.

After the change: `test_work_parallel_restart_terminal.py` 17 pass; the new mod scenario and the native observation scenarios pass.
