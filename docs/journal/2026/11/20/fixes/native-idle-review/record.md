---
type: fix
title: A listed idle fork publishes its submitted result's review without waiting for main's turn end
description: Andy n2505's review opened ten minutes after its completion because only a main turn end wrote the idle workspace proof; the native observation now publishes it and wakes main.
timestamp: 2026-11-20T09:40:00Z
tags: [work, parallel, review, native-observation, idle-proof]
---

# Report

A peer (Andy's main session) reported on 2026-11-20:
- n2505 (lease 5a257caa…) submitted its completion cleanly.
- The result was absent from `review.pending`.
- `work_parallel_item_review` approve was refused with `positive matching idle workspace proof required`.

The peer later reported that the review opened at a later turn boundary and approve finalized it. It withdrew the report as a false alarm. The delay itself is the defect fixed here. It matches the open journal item "n2040 surfaced for review about 8 minutes after its receipt" ([celly-stuck-recoveries](/docs/journal/2026/11/19/fixes/celly-stuck-recoveries/record.md)).

# Evidence

Andy's state files and main transcript were read only ([evidence/andy-n2505-timeline.txt](/docs/journal/2026/11/20/fixes/native-idle-review/evidence/andy-n2505-timeline.txt)).
- Timeline:
  - 08:54:18Z: completion receipt.
  - 08:54:55Z: the 15-second native observation listed the worker `idle`.
  - 08:55:26Z: the worker was absent from the list.
  - No main turn ended between 08:49:11Z and 09:01:35Z.
- The workspace manifest stayed `idle=false` until 09:04:57Z. It was then written with the turn-end reconcile proof text `Native agent.list status: idle`.
- The review wake reached main at 09:05:23Z.
- Main's approve at 09:00:12Z was refused by `review_observe` because the manifest was not idle.

Confirmed in source (`work_parallel_mcp.py`, `register.js`):
- A review is published only by `_observe_idle_and_publish_review`. Three paths call it:
  - the `reconcile state=idle` event, which the mod sends only from `parallelReconcileAtTurnEnd` when the fork's row is listed idle at a turn end;
  - `restart_submitted_terminal`;
  - `_main_review`, which needs the idle manifest first.
- `native_observation` recorded the same `idle` row from the same host list, but only updated the native status.

Not confirmed: which `turn.complete` wrote the 09:04:57Z proof, and why the worker was listed again then. `work-parallel-native.json` still shows `last_seen_at` 08:54:55Z and `missing_since` 08:55:26Z.

# Change

- Router `native_observation`: a valid row with status `idle`, `completed`, `failed` or `killed` calls the existing readiness path `_observe_idle_and_publish_review`. These are the statuses the mod's turn-end path treats as terminal. The proof is `Native agent.list status: <status>`, the same text the turn-end reconcile uses. The row must also meet all of these:
  - Its lease is `submitted` and slot-free.
  - Its lease's `host_id` is the row's agent.
  - Its coordination run belongs to that agent and holds a `completion` exit.
  - It has no review row yet.
- A row that fails is reported in `skipped`. It no longer aborts the batch.
- The result gains `reviews_published`.
- An absent row publishes nothing; absence is still never death.
- Mod `parallelObserveNative`: when `reviews_published` is not empty, it re-reads the host status and runs `projectParallelReviews`. That sends the existing review wake (append, plus prompt when main is idle) without waiting for a turn end.

No new store field, schema or model-facing text. The wake text is unchanged.

# Test changes

- `tests/test_work_parallel_restart_terminal.py`
  - `checkpoint_a` now ends with the second fork still listed `running`. It reproduces the recorded 2026-11-11 run up to the restart.
  - That run also listed the second fork idle at 16:51:14Z, and its review was never published.
  - Under this change, that row would publish the review before the restart. The restart cases need it unpublished, so their precondition keeps the second fork running.
- `tests/test_work_mod_real_backend.py`, `tests/work_mod_real_backend_harness.mjs`, `tests/work_mod_real_backend_server.py`
  - Three pre-existing tests assumed that a native observation of an idle submitted fork never publishes a review (zero product regressions):
    - `test_completions_killed_before_review_converge_after_restart`: `seed_restart_pair` had the same pre-restart idle observation as `checkpoint_a`; the second fork now stays `running` before the restart so the restart path is still exercised.
    - `test_broken_worktree_publishes_one_stranded_row_beside_the_healthy_review`: the `"after session.start"` step ran `/work-mode parallel`, which runs `native_observation` and therefore published both reviews before the first `turn.complete`. Skipping that command at `"after session.start"` leaves the first `turn.complete` exercising reconcile.
    - `test_one_lease_whose_reconcile_is_refused_does_not_stop_the_others_or_the_pump`: its injected refusal covers only `reconcile`, not `native_observation`. The `native_observation` right after the reconcile loop on the real backend now publishes the refused lease's review too, while only the healthy fork is stopped by reconcile. The test's expected counts now reflect both reviews and wakes.
- New router tests:
  - `test_native_idle_row_publishes_the_review_without_a_turn_end`: idle row, then review pending with matching ledger and workspace proofs, then approve finalized.
  - `test_native_idle_row_of_a_published_review_writes_nothing_more`: a replayed idle row writes nothing.
  - `test_native_rows_never_publish_a_running_or_absent_fork`: an empty complete list, or an idle row of a fork with no completion receipt, publishes nothing.
- New mod scenario `native-observation-publishes-review` (test `test_native_observation_that_publishes_a_review_wakes_main_without_a_turn_end`): the 15-second timer observation that publishes a review prepares the wake once and appends the review once, with no turn end.

# Verification

See [evidence/new-tests-fail-first.txt](/docs/journal/2026/11/20/fixes/native-idle-review/evidence/new-tests-fail-first.txt).
- On 9fa764d (before the change):
  - The publication test fails because no review is published.
  - The mod scenario fails because no wake is prepared, both with its first and its final assertion. For the final run, the 9fa764d `register.js` was swapped in and then restored, hash-checked.
  - The two boundary tests also fail there, but only because the result has no `reviews_published` field. Their behavior assertions hold on both trees.
- After the change (f51f8eb), Windows, isolated config:
  - `test_work_parallel_restart_terminal.py`: 16 pass.
  - Affected bundle (restart_terminal, native, receipts, cross_process_ownership, p0_events, rework_worktree, content_baseline, finalization, side_intake_mod, work_mod_write_gate): 510 tests, 0 failures, 33 skipped.
  - `claude plugin validate`: passed. `claude plugin test` on ciel-work: 89 pass, 0 fail. ruff: clean.
- Linux Docker (Python 3.11.2, Node 22) on the archived beta merge c955630, same bundle: 510 tests, 0 failures, 81 skipped.
- Windows real-backend suite (`tests/test_work_mod_real_backend.py`) after the three fixture updates: 117 tests, 0 failures, 3 skipped.

# Post-restart live observation (Andy, c955630)

Read-only check after the user restarted Andy's session:
- P0 n2471 (prior lease 0109bfc1) recovered and launched Gen-4 (lease a58c3157…) cleanly.
- P1 n2503 Gen-2 (lease 13a093c6…) was running at the moment of exit. Startup orphan sweep released it as `orphaned` (router log 04:13:47 local), before `parallelRecoverWorkers` could attempt a resume.
  - The host then launched a fresh Gen-1 lease (570bc713…) in a new worktree (`work-parallel-a1ccbb1c…`).
  - The old Gen-2 worktree (`work-parallel-81fae5ca…`) still holds commit `62c5ac03` and 2 modified files that the new worktree does not have.
  - Residuals left by the orphaned Gen-2: its coordination run stayed `state="active"`, and Gen-1's `rework_ready` review row (`189d4d72…`) stays in `review.pending` because its `replacement_lease_id` (`13a093c6…`) never published a review. Neither blocks `570bc713…` from submitting or being approved.
  - Open defect to track: startup orphan sweep runs before `parallelRecoverWorkers`, so a running fork at restart is always orphaned and restarted from scratch in a new worktree instead of resumed, leaving its coordination run `active` and its predecessor `rework_ready` row visible.

# Installed (not yet effective)

The two changed files (`work_parallel_mcp.py` 570F1C2D…, `register.js` FA499625…) were hash-checked overlays with backups on local, Celly and KOBE.
- Every target's prior hash was the 9e718ac file (2BA145B7…, 83603E1A…).
- KOBE reports version 0.2.51-beta.20261120-020612.39825c0 underneath the overlay.
- No process was restarted. A running router and mod keep the old code until each session restarts.
