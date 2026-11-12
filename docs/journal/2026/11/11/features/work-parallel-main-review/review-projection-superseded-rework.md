---
type: fix-journal
title: Decided rework predecessor left the Main review queue after its replacement published a review
description: Every rework predecessor stayed under "Main review" forever, even after its replacement was approved. The finalization projection now hides a completed, unblocked rework_ready predecessor once its exact replacement generation has its own review row. Backend only, read-side only.
status: draft-frozen-uncommitted
timestamp: 2026-11-12T05:45:00Z
tags: [work-parallel, main-review, rework, projection, ui-state, backend-only]
---

# Evidence (measured)

Owned main-review E2E `e2e-source/run-20261111-213818` (30/30 passed):

- `07-finalized.txt` (terminal, "done 9:45 PM", "[work] 1/1 complete") still shows `MAIN REVIEW (1) · … Rework · n4 … rework launch: bound · attempt 2`.
- `07-finalized-desktop.png` shows the same row under "Main review (1)" in the web Parallel tab.
- In `07-final-state.json`, `finalization.reviews` holds:
  - predecessor `26c5461a…`: n4, generation 1, `rework_ready`, decision `rework`, all stages true, launch `bound`, attempt 2, `replacement_lease_id` `ab47409e…`, `replacement_generation` 2;
  - replacement `ab47409e…`: n4, generation 2, `finalized`, decision `approve`.

# Cause (static, from source)

- `work_parallel_finalization.py` `projection()` (`:682-697` before the change) projects every row in `pending`, `awaiting_capacity`, `deciding` or `rework_ready`.
- The validator (`:222-229`) allows a rework decision only in `awaiting_capacity`, `deciding` or `rework_ready`. `rework_ready` is the end of the rework saga, and there is no later state for it.
- The status projection (`work_parallel_mcp.py` `project()`, `:303`) carries that list as `review.pending`. Each consumer lists every row:
  - the terminal `/work-mode parallel` (`register.js` `:1406-1408`);
  - the web Parallel tab (`work_http_controller.py` `_public_reviews` `:493-536`, `work_web_ui.py` `renderMainReviews`);
  - the observatory (`register.js` `:768`, `:1294`).
- So every decided rework predecessor is shown as a Main review row for good, and the count grows with each rework.

# Fix (draft, `work_parallel_finalization.py` `projection()` only, `:682-717`)

A row is hidden only when every one of these holds; any mismatch keeps it visible:

1. its decision is `rework` and its state is `rework_ready`;
2. every stage is true and `blocked` is null;
3. the same finalization snapshot holds a review row keyed by its `replacement_lease_id`, and that row:
   - has `lease_id` equal to `replacement_lease_id`;
   - has the same scope and the same `item_id`;
   - has `generation` equal to the predecessor's `replacement_generation`.

Notes:

- The replacement row can be in any state: `pending`, `deciding`, `finalized`, or `rework_ready` after another rework.
- `awaiting_capacity` and `deciding` are never hidden.
- Condition 2 restates validator invariants:
  - `:228-229`: `rework_ready` exactly when every stage is complete;
  - `:205`: `blocked` is null outside `awaiting_capacity` and `deciding`.

  A stored file cannot break them. team-lead accepted condition 2 without code. The guard in the rule (two lines) was added before that acceptance and is kept as a defensive read check.
- Read-side only. No stored field, state, schema or write path changes. Both rows stay in the journal, so the history is kept.
- `register.js` (`eab3ad33`), `side-intake.js` (`9b955068`), `work_parallel_mcp.py` (`35214869`), `work_http_controller.py` (`b7cd27c1`) and `work_web_ui.py` (`2cade9ac`) are byte-identical.

## Why the finalization projection

- Every input of the rule is a finalization fact. One `read()` snapshot holds all of them, so the check needs no second store and no ledger read, and it cannot race.
- The MCP `project()` would need the same finalization facts, and the ledger adds nothing to the rule. A replacement review exists only after the replacement's completion and a positive idle.
- Every consumer reads this one list, so one backend change makes the terminal, web, observatory and model views agree.

## Consumers checked (static, then tested)

- **`parallelRollForwardDue`** (`register.js` `:2035-2039`) selects only `awaiting_capacity` rows and `deciding` rows with `blocked`. Neither is ever hidden, and `rework_ready` is never roll-forward due.
- **Rework launch reconcile** (`:2201-2208`) needs `rework_ready` rows whose replacement has not completed. The predecessor stays visible until the replacement has its own review, which requires the replacement's completion and a positive idle.
- **Web launches merge** (`work_http_controller.py` `_public_launches` `:423-460`, merged at `:517` and watched at `:601-604`):
  - `_public_launches` still lists every rework launch in the journal, including a hidden predecessor's.
  - The merge attaches a launch only to rows in `review.pending`, and the stream's `launching` flag reads only those rows.
  - So a hidden predecessor shows no launch row, no "Resolve stuck launch" control and no `launching` refresh.

# Tests (draft)

`tests/test_work_parallel_finalization.py`:

| Test | Covers |
|---|---|
| `test_rework_predecessor_stays_projected_until_its_replacement_publishes_a_review` (`:346`) | Visible with launch `none`, `launch_intent`, `spawn_observed`, and `bound` + running, and while the replacement is submitted but not idle. Hidden once the replacement review is `pending`. Nothing projected after `finalized`. The stored predecessor row is unchanged. |
| `test_twice_reworked_item_projects_only_its_latest_live_generation` (`:393`) | A three-generation chain. Generation 1 is hidden by generation 2 in `rework_ready`; generation 2 is hidden by generation 3 in `pending`; then nothing. Also old readers: `4eebfdc` and `5c91ea8` validate and read `work-parallel.json`, and neither commit contains a finalization, receipts or native reader. |
| `test_replacement_review_in_deciding_hides_the_predecessor` (`:426`) | The replacement's approve is blocked at the board stage (`deciding`, `stage_failed`). Only the replacement is shown; after roll-forward, nothing. |
| `test_incomplete_or_blocked_rework_ready_row_is_never_hidden` (`:447`) | Injected through `read()`, since the validator refuses these files (asserted): a `rework_ready` predecessor with one stage false, or with `blocked` set, stays visible even with a matching replacement review. |
| `test_hidden_predecessor_leaves_no_launch_indicator_in_the_web_view` (`:481`) | Injected state: `launch_bound` is skipped, so the predecessor's launch stays `spawn_observed` while the replacement binds, completes and is reviewed. `_public_launches` still lists it, but `_public_parallel` shows only the replacement with no launch, and `_parallel_fingerprint` reports `launching` false. |
| `test_rework_waiting_for_capacity_or_blocked_stays_visible_to_the_pump` (`:525`) | `awaiting_capacity` (code `capacity`), then `deciding` + `stage_failed` after the workspace rebind, then `rework_ready`: visible at every step. |
| `test_unrelated_review_row_never_hides_a_rework_predecessor` (`:576`) | Another item's review, and a row under the replacement id with a different generation or scope, both leave the predecessor visible. The exact identity hides it (control). |
| `test_replacement_row_for_another_item_never_hides_the_predecessor` (`:590`) | A row under the replacement id with the same scope and generation but another `item_id`, written straight into the store, leaves the predecessor visible. The same row for this item hides it (control). |

`tests/test_work_mod_real_backend.py` `test_rework_predecessor_leaves_terminal_web_mod_and_model_review_views` runs harness scenario `rework-review-lifecycle`: real `register.js` against the real backend. At each step it compares `/work-mode parallel` text, the web `/parallel` payload and the model `review.pending`:

- bound and running, and submitted with the native fork still running: 1 Rework row (the predecessor);
- replacement review pending: 1 row, the replacement;
- finalized, and after a new process: no MAIN REVIEW line, empty web list, empty model list.

Fail-before on `9484324f`:

- These seven fail:
  - stays-projected, three-generation chain and deciding: 2 rows where 1 is expected;
  - incomplete/blocked, unrelated and another-item: at their control assertions;
  - web indicator: the predecessor's `spawn_observed` launch is still shown.
- The integration test fails with the predecessor still listed beside the pending replacement.
- `test_rework_waiting_for_capacity_or_blocked…` passes on both versions; it guards against hiding too much.

# Runs (draft, foreground, every `CIEL_RUNTIME_*` unset, fresh temp config per group, `-rs`: no skips)

| Group | Result |
|---|---|
| finalization | 75 passed, 108 subtests |
| real_backend | 23 passed |
| web + observatory (`test_work_http_parallel_review`, `test_work_http_controller`, `test_work_observatory`) | 49 passed, 12 subtests |
| write_gate + packaging | 113 passed, 58 subtests |
| parallel core (restart_terminal, receipts, native, mcp, parallel) | 114 passed, 12 subtests |

- In the earlier draft round, the write_gate group failed once on `test_both_idle_prompts_are_issued_at_once_and_each_records_its_own_outcome`: prompt order `['L2', 'L1']`.
  - That test uses a scripted host with no backend. Its files are identical to the repo.
  - It has passed in every run since. The cause of the one failure is not established.

# Not covered

- **Late fork (accepted by team-lead as documented and unmeasured).** Some late forks first appear only after the replacement published its review: forks of an earlier launch attempt of the replacement. Such a fork is no longer stopped.
  - The only path that stops such a fork is `register.js:2215`, `rejectLateReworkAgents` inside `parallelStartRework` (`:2196`). It is reached only through a projected `rework_ready` row whose replacement lease has a host id.
  - Whether a fork can first appear that late has not been measured.
  - After the replacement's review, the retained workspace is finalized or retained by the replacement's own saga:
    - approve marks it retained (`work_parallel_mcp.py:828-834`, "retained verified workspace");
    - another rework rebinds it to the next generation (`:874-886`, `handoff_rebind`).
- **Bind commit before `launch_bound` (static; reviewer, confirmed by team-lead against the code).**
  - In a fresh bind (`work_parallel_mcp.py:1487-1498`), `store.bind` (`:1495`) and `_bind_coordination_run` (`:1497`) commit before `_bind_rework_launch` (`:1498`). If `launch_bound` raises, the ledger lease is bound while the review's launch stays `launch_intent` or `spawn_observed`.
  - The same exception also skips the workspace bind at `:1549`. The replacement's later idle reconcile reaches `set_activity` at `:1551`, which fails with "workspace host binding mismatch". So no replacement review is published, and the predecessor stays visible.
  - The projection hide is therefore safe in this case: it never hides a predecessor whose replacement is stuck here.
  - Nothing in the mod acts on that launch for a lease that has a `host_id`. `parallelHydrate` (`register.js:1905-1912`) only owns and tracks every non-done host id, and the launch reconcile considers only `reserved` leases with no host id (`:1919`).
  - Whether `launch_bound` ever fails after a ledger bind is not measured.
  - The earlier web-indicator test injects a skipped `launch_bound` without this exception. It covers the projection and web rows only, not this bind path. While writing it, a crash inside the bind event was observed to make the replacement's idle fail with the same mismatch.
- **Open item, pre-existing, not caused by the projection change (static).** A single-attempt `launch_bound` can leave a replacement stuck before review.
  - `_bind_rework_launch` (`work_parallel_mcp.py:1149-1158`) reads the finalization version once and calls `launch_bound` with it. A concurrent finalization write makes it raise "Work finalization changed; reread" (`work_parallel_finalization.py:318`).
  - `parallelEvent` retries only errors matching `/state changed; reread/` (`register.js:1469`), which this message does not match.
  - `bindReworkFork` (`register.js:2017-2025`) treats a failed bind as success when the ledger `host_id` already equals the fork. So the mod never re-sends bind.
  - An idempotent bind replay (`work_parallel_mcp.py:1481-1486`) would repair both the launch and the workspace bind, but nothing sends one.
  - Possible result, not measured: the replacement reports, its idle reconcile keeps failing, no review is published, and the item is stuck before main review. Whether `restart_submitted_terminal` repairs it is also not measured.
  - Status: not fixed. team-lead has queued a deterministic real-backend reproduction in the private draft (one injected concurrent finalization write between the read and `launch_bound`, no product change), to run after the SSE E2E is clear.
  - Update 2026-11-12: reproduced and fixed in a separate draft; see [the bind/launch_bound retry](bind-launch-bound-retry.md). The predecessor hide is unchanged and still applies after recovery.
- Not re-run in an owned E2E console.
