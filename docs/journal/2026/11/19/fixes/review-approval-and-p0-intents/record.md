---
type: fix
title: Main reviews results whose goal scope changed, P0 relaunch after an ended lease, and the null-workspace close crash
description: KOBE, Celly and Andy data after a9d4d0c. close_resolved_reviews crashed on every pump for a review with no workspace. A goal scope edit after an assignment stranded its result, its completion report and its parked worker for good. A P0 item whose lease ended kept a bound intent, so every relaunch failed as "coordination persistence failed" and hid the cause. All three are fixed. Main now decides scope-changed results; the review shows approval_changed.
timestamp: 2026-11-19T23:30:00Z
tags: [work, parallel, review, approval, p0, kobe, celly, andy, hotfix]
---

# Evidence (read-only, 2026-11-19 UTC)

## KOBE (router a9d4d0c, restarted 21:50Z)
- **Null-workspace crash.** `router_post_uncaught` fired 28 times in 34 minutes, all with one stack: `close_resolved_reviews` → `row['workspace']['path']` → TypeError, because every KOBE review has `workspace: null`. This is a regression from 1446629.
- **Approval-scope refusals.** After the restart, 9 of 12 `work_parallel_item_review` calls were refused with "stale approval fingerprint". Every refused run's approval generation was below its goal's current one (for example root n862: current 10, runs at 3–8).
- **Fork reports.** 4 completion reports were refused with "Work approval scope changed; reconcile before continuing".
- **Parked workers.** 2 SendMessage resumes were refused: "could not return to running: Work approval scope changed".

## Celly
- **Completion reports.** 10 fork completion reports were refused for an approval-scope change (10:28–12:31Z).
- **Main reviews.** 6 main reviews of n1102 were refused; that review is still pending.
- **Stranded recoveries.** 4 stranded recoveries were blocked with "stale root approval".
- **Reservations.** `coordination persistence failed` appeared 3 times.

## Andy
- **Main reviews.** 21 pending results are refused with "stale approval fingerprint". Root n133 went from generation 9 to 10 after main removed n656 and n657.
- **P0 goals.** n2442 (item n2471) and n1445 (item n2452) are recorded as `launch_refused: coordination persistence failed; reservation released`.
- **Open intents.** 17 P0 intents are open; 12 of them have a lease that is done or orphaned.

# Causes

| Problem | Cause (source) |
|---|---|
| Null-workspace crash | `close_resolved_reviews` built its evidence text from `row['workspace']`. The 1446629 review path did not cover it, because the earlier grep looked for the double-quoted form only. |
| Approval-scope refusals | `_root_scope_digest` hashes id, parent, title, note and ref of every node under the goal. `reconcile_roots` bumps the goal's generation whenever that hash changes. `_approval_current` requires the exact generation, and it gates worker reports, host resume and both review revalidations. So any edit to any item of a goal stranded every in-flight result of that goal. |
| Stale P0 intent | Orphan sweeps, finalized reviews and releases end a lease without the host event that closes its P0 intent. `create_intent` then returns the stale `bound` intent. `_reserve_assignment` reuses its lease, and `record_assignment` refuses with "assignment already exists with a different manifest". The wrapper message hid that cause. |

Reproduction: `.local/tmp/repro_reserve.py` runs the router's own reservation on a temp copy of Andy's Work files. The ACL check on the copied P0 key is skipped. The cause chain was read from it.

# Fix

- **`close_resolved_reviews`:** a result with no workspace closes with "the fork ran with no workspace, so nothing is retained".
- **Approval scope (user decision 2026-11-19: main decides):**
  - The review revalidations no longer refuse a goal scope change.
  - Worker reports (completion, question, handoff) are accepted.
  - A host resume needs only that the goal is still approved (executing) or is an exact P0 launch.
  - A rework, and a stranded recovery, needs an approved goal. Otherwise it is refused before any decision with "approve the result, or approve the goal and then rework".
  - Replacement runs take the goal's current approval generation and fingerprint (`_assignment_approval`).
  - The main projection and the REST review rows carry `approval_changed`. It is read-only: the run fingerprint is compared with the board's current scope digest. The review result carries it too.
- **P0 intents:**
  - Before creating a P0 intent, `_close_ended_p0_intents` closes this item's open intents whose lease is done, orphaned, unreported or gone.
  - A replay of an exact unbound reservation keeps its existing run instead of rewriting it.
  - The refusal now carries the cause (type and message, 300 chars).

# Verification

- **Fail-before.** These tests failed on the code before the fix:
  - `test_unconfined_review_of_an_item_resolved_on_the_board_closes_unreviewed`: TypeError at `work_parallel_mcp.py:2475`, the same stack as KOBE.
  - `test_a_terminal_lease_does_not_keep_its_old_p0_intent_open` and `test_replaying_a_prepared_assignment_preserves_its_workspace_and_run`: "assignment already exists with a different manifest".
  - The approval-change tests are new behaviour. The old fingerprint case in `test_stale_artifact_head_and_fingerprint_leave_result_pending` asserted the refusal the user removed, and it was dropped.
- **Andy state copy.** The P0 reservation of n2471 and n2452 now reserves. Before the fix it failed with "coordination persistence failed".
- **After.** `test_work_parallel_finalization.py` passed: 97 tests, 128 subtests. The P0 event and outcome tests passed: 43 tests. The wider run is recorded in [log.md](log.md).

# Not fixed here (recorded)

- **Andy, 21 submitted leases from builds before coordination receipts (11-05 to 11-10).** They have no run and no receipt, so they can never be published for review. They still fence their items.
- **KOBE, workspace preparation retries.** "not a git repository" was retried 178 times on 11-19.
- **Fork contract errors.** These repeat on all three sessions: artifact SHA256 required, invalid report event, and `work_parallel_status` without identity.
- **Celly, empty `work_node_update` loop.** A model repeated the call 538 times.
