---
type: defect-analysis
title: Completed Work results stuck after a restart before review
description: Root cause and fix for run-20261111-105012. The approved result blocked at archive with "run is not terminal", and the second completed result never got a review row. It is a product defect, reproduced without a real console.
status: fixed-uncommitted
timestamp: 2026-11-11T20:55:00Z
tags: [work-parallel, main-review, restart-recovery, coordination, native-observation, defect]
---

# Verdict

This is a product defect, not a harness artifact.

- The deterministic reproduction uses only the real `register.js` and the real Python stores. It fails on the exact bytes the owned run executed, with the same two symptoms.
- The harness problem (its process queries returned empty after the kill) only affected the runner's own measurements. The durable state was already wrong at checkpoint A, before the kill.

Source: [owned E2E attempts](owned-e2e-attempts.md), run folder `e2e-source/run-20261111-105012/`.

# Evidence from the run

The run's `register.js`, `work_parallel_mcp.py` and `work_parallel_receipts.py` hashes match the pre-fix worktree: `3328f520`, `1ef1170b`, `7d1ff359`.

Order recorded in the first process (`.local/owned-main-review-20261111-105012/first-claude-debug.txt`):

1. 16:51:04.54Z: n2 completes, so its run becomes `terminal`.
2. 16:51:04.87Z: `native_observation` with both forks still `running` ("confirmed: 2").
3. 16:51:08.57Z: n4 completes, so its run becomes `terminal`.
4. 16:51:09.01Z: n2's fork ends and the ciel-work `turn.complete` hook starts. This is the only ciel-work `turn.complete` that settles in this log (16:51:16.32Z, 7306 ms).
   - Inside it, an event at 16:51:09.94Z (fields `event, lease_id, expected_version`, 862 ms) is consistent with the idle reconcile that published n2's review.
   - A second event at 16:51:12.42Z is consistent with n4 being reconciled as `running`. The event names are not logged.
5. 16:51:13.38Z: n4's fork ends while that hook is still running. A `native_observation` at 16:51:14.0Z reports "confirmed: 0", both forks `idle`. No idle reconcile of n4 appears before the log ends.
6. 16:51:15.36Z: the gated `review_wake prepare` for n2 is held, then the owned tree is killed.

Durable state at checkpoint A (`03-completion-before-review.json`):

| | n2 | n4 |
|---|---|---|
| run state | `active` | `terminal` |
| native status | `idle` | `idle` |
| lease state | `submitted` | `submitted` |
| idle proof | `Native agent.list status: idle` | `provided failing test failed` (the report's evidence) |
| review row | `pending`, wake 0 attempts | none |

# Root cause 1: n2's run stayed `active`

- `persist_receipt` sets a completion run to `terminal` (`work_parallel_receipts.py:446`).
- The native row that arrived after the receipt still said `running`. Before the fix, `update_native` (pre-fix `work_parallel_receipts.py:394-395`) set `state="active"` for `pending`/`running`, whatever the exit receipt said.
- The later `idle` row changed only `native_status`, because `idle` maps to no state. So the run stayed `active`.
- n4 escaped only because its receipt landed after that `running` observation.
- Approve's archive stage calls `archive_terminal`, which needs `terminal`/`handed_off`/`superseded` (`work_parallel_receipts.py:525`). It was blocked by `_roll_forward` (`work_parallel_mcp.py:760-771`). Nothing in the pump can ever make it terminal, so the block was permanent.

# Root cause 2: n4 had no review row

- A review is published only by `reconcile state=idle`, which writes the workspace idle proof (`work_parallel_mcp.py:1499-1509`). Separately, `_review_facts` refuses on the workspace snapshot from `review_observe` (`work_parallel_workspace.py:233-235`).
- n4's fork ended, but that process was killed before its `reconcile` ran.
- After the restart, the forks belong to the dead process, so `$.agent.list()` is empty.
  - `parallelHydrate`, `parallelRecoverWorkers` and `parallelTurnComplete` only look at running leases, reserved leases, or ids in the in-memory `parallelBound` map. The restart clears that map.
  - `native_observation` with `complete: true` only sets `missing_since`. Absence is never death (`work_parallel_native.py:176-181`).
- So a `submitted` lease with a completion receipt and an absent fork had no path to `reconcile idle`. Its review could never be published.

# Restart contract

The required behavior was not covered: "a submitted lease whose agent is absent after restart is treated as observed-terminal."

The existing recovery covered only these cases:

- running leases (`parallelRecoverWorkers`);
- unbound reservations and rework launches (`parallelHydrate`);
- live forks seen at `turn.complete`.

# Fix

1. **Receipt-terminal immutability** (`work_parallel_receipts.py`).
   - `update_native` (`:394-411`) still records the native status word.
     - A run with a `completion` or `review` exit stays `terminal` under every native status, in any order.
     - A `handoff` or `host_salvage` exit stays `handed_off`.
     - A `question` exit is not an exit and still resumes.
     - A native update does not reliably repair a run an older build already reopened: a same-status row is a no-op, and a lease that is already `done` gets no native row. That state is repaired by the archive stage (item 5).
   - `persist_receipt` (`:435-437`) refuses any new receipt after a completion exit, so the exit cannot be replaced either. An identical completion replay stays idempotent.
2. **One readiness path.** `_observe_idle_and_publish_review` (`work_parallel_mcp.py:637-665`) is the only path that does all of this: reconciles the lease idle, writes the matching workspace idle proof, and publishes through `_review_facts` and the finalization `pending` call.
   - The normal `reconcile state=idle` path calls it (`:1560`).
   - The restart event calls it too, but only while no review row exists or the review is `pending` (item 3). No second readiness path exists.
   - Backend invariant: a submitted result's ledger idle proof and workspace idle proof must agree. They live in separate stores and are written separately, so the helper re-reads the ledger after each workspace write. It rewrites the workspace with the ledger's current proof until the two match, then publishes.
   - A review that already exists with mismatched proofs is repaired by the same helper. `pending` is idempotent, so the repair creates no second review row and no wake.
   - This is a defensive liveness repair for a state the backend can enter under any writer order. A production race that reaches it has not been reproduced. The deterministic test forces that order in-process.
3. **Host-internal `restart_submitted_terminal`** (`work_parallel_mcp.py:1355-1366`, `_restart_submitted_terminal` `:1067-1117`).
   - It uses the same fence as the launch events: `_host.internal is True` and no `agent_id`.
   - It requires all of the following:
     - the exact `lease_id`, `submitted` with no slot in this conversation;
     - the exact `generation` and `receipt_digest`, equal to the run's `{kind: completion, digest}` exit;
     - `_host.absent_agent_id` equal to both the lease's bound `host_id` and the run's agent;
     - `_host.absent_from_complete_list: true`.
   - It refuses before any write when there is no receipt, and on a wrong generation, a wrong or other-lease digest, a forged or model shape, or a running lease.
   - With no review row or a pending one, it terminalizes a reopened run (`update_native("missing")` with a CAS retry), then calls the shared readiness path (`:1110-1116`).
     - With no review row, it passes the proof `Submitted completion fork absent from agent.list after restart`.
     - With a pending review, it keeps the ledger's current proof and only converges the workspace to it. Matched proofs write nothing.
   - It is idempotent: no duplicate review row, so no duplicate wake. No slot is released and no lease state changes.
   - **Decided review: successful no-op** (`:1090-1098`, combined fix).
     - The finalization validator makes `state == "pending"` equivalent to `decision is None` (`work_parallel_finalization.py:211-231`). The code tests `review["decision"] is not None`.
     - With a decision recorded, the event still checks the exact run, agent, generation and receipt. It then returns `review_pending: false` without writing anything: no `update_native`, no `set_activity`, no finalization call.
     - The saga owns every later write. Approve restores a reopened run before archive (`_archive_retry`). Rework may already have rebound the workspace, and `set_activity` → `_owned` refuses a rebound manifest (`work_parallel_workspace.py:138-142`).
     - Convergence (`_observe_idle_and_publish_review`) runs only with no review row or a `pending` one.
4. **Mod** (`register.js`).
   - `parallelRestartSubmitted` (`:2309-2332`) runs at session.start (`:2339`) and at `turn.complete` (`:2274`).
   - It sends the event only for a submitted lease that has the run's own completion exit, generation and agent, and whose `host_id` is in neither the current `$.agent.list()` nor the ids this loaded process spawned or listed. Those ids are tracked by `processNativeAgents` (`:995-1008`), which survives session.start like the launch nonce.
   - The event is in the host-only set, so a model call is denied before the router (`:1422-1425`, `:1550-1551`).
   - `side-intake.js` is untouched.
   - **Reachability gap** (`135fc5e6`):
     - `parallelRestartSubmitted` skipped any lease that already had a review and a terminal run. A pending review whose ledger and workspace proofs disagreed therefore never reached the backend repair. Approve was denied with `positive matching idle workspace proof required`.
     - `a923bb10` deleted that skip and its `reviewed` set, and nothing else: 2 lines removed, 0 added.
     - The reviewer stated from the diff (static, not measured) that `a923bb10` sends the event to every submitted lease on every `turn.complete`. The fixer measured it: `a923bb10` fails the boundary-count tests with `3 != 8` and `2 != 9` (fail-before table below).
   - **Once-set** (combined fix, `:2306-2332`):
     - `restartSubmittedObserved` comes back from `de72a5c3`: key `lease:generation:receipt digest`, at most 400 keys, recorded only after a successful call.
     - The set lives in the loaded module, so each process and each reload sends once per exact receipt. A failed call is not recorded and is retried at the next boundary.
     - Once a lease is finalized it is no longer `submitted`, so nothing is sent.
   - **Retry loop on a decided rework** (reviewer, confirmed on `de72a5c3`; it applies to `a923bb10` too):
     - Rework is decided, the workspace stage has rebound the predecessor's manifest, and the board stage is blocked. The predecessor stays `submitted`.
     - Every `restart_submitted_terminal` threw `workspace ownership was handed off; retained` from `set_activity` → `_owned`.
     - The mod never recorded the key, so it retried at every boundary. Each failure filled the 5-entry `parallelFailures` ring (`:1066-1069`), pushing real failures out of `/work-mode parallel`.
     - The fix is the decided-review no-op above, together with the once-set.
   - **`completion-restart` count 1 → 3.**
     - On `135fc5e6` the skip removed n2 from process 1's restart events, so the count was 1 (n4 only).
     - With the skip removed, process 1's session.start sends n2 (already reviewed) and n4, and process 2's sends n4 only, because n2 is `done` by then. Total: 3.
     - Review rows (2), wakes (2) and decisions (1) do not change.
5. **Archive repair for a run an older build reopened** (`work_parallel_receipts.py:523-545`, `work_parallel_mcp.py:668-690`, `:845-848`).
   - The approve saga's archive stage passes the reviewed `completion_receipt`.
   - If the run is not terminal, `restore_completion_terminal` sets it `terminal` only when all of these hold:
     - the run's exit is exactly `{completion, reviewed digest}`;
     - that receipt is a durable completion receipt of this same lease.
   - Then the run is archived and the review reaches `finalized`.
   - Any mismatch stays blocked with the reason `run is not terminal: completion receipt does not match the reviewed receipt`.
   - `review_roll_forward` repairs the same old-build state.

Old-reader compatibility: no field, file or state is added. Only existing values are written:

- run state `terminal`;
- native status `missing`, already in `NATIVE_STATUSES`;
- the lease idle proof, an existing ledger field;
- the workspace manifest `idle_proof`, an existing field.

`test_old_readers_accept_ledger_after_approve_and_rework` validates `work-parallel.json` with the 4eebfdc and 5c91ea8 readers. Neither commit contains `work_parallel_receipts.py`, so neither has a coordination reader. The sidecar schema is unchanged from HEAD (aa06216).

# Deterministic reproduction and regressions

- `tests/work_mod_real_backend_server.py` `seed_restart_pair` replays the recorded order up to checkpoint A, using the real stores.
- `tests/work_mod_real_backend_harness.mjs` `completion-restart` restarts the mod with no native rows. It lets session.start wake main, approves one result, then restarts again.

| | pre-fix bytes (`7d1ff359` / `1ef1170b` / `3328f520`) | fixed |
|---|---|---|
| checkpoint runs | `active` / `terminal` | `terminal` / `terminal` |
| reviews after restart | 1 (n2 only) | 2 |
| approve result | `deciding`, `stage_failed: run is not terminal` | `finalized`, all stages true, run archived |
| wakes after a second restart | 1 | 2 (one per review; none re-sent) |
| decisions | 1 | 1 |

New tests:

- `tests/test_work_parallel_receipts.py` adds five tests:
  - idle after receipt;
  - every native order (60 sequences);
  - handoff vs question;
  - a run reopened by an older build;
  - completion finality.
- `tests/test_work_parallel_restart_terminal.py` adds six tests:
  - the receipt-before-running-row order;
  - a single review on restart, with idempotent replay;
  - nine refused shapes, including the forwarded model shape, with all four stores unchanged;
  - a running lease with no receipt;
  - a submitted lease with no coordination receipt;
  - a reopened run that is restored and then finalized.
- `tests/test_work_parallel_restart_terminal.py` adds five more tests, built from the review's probes (`probe_idle_restart_race.py`, `probe_blocked_archive.py`, `probe_same_status.py`):
  - the forced restart/idle order: approve finalizes on the first attempt, with no new idle event;
  - an existing review with mismatched proofs: one restart replay repairs it, a second replay changes no store, and approve finalizes;
  - approve blocked at archive by an old build: it reaches `finalized` (or reaches it through `review_roll_forward`);
  - the same-status idle no-op followed by approve: `finalized`;
  - a non-matching receipt: the run stays blocked with the explicit reason.
  All five fail on the refreeze bytes (`fa8f52f9` / `c5ccc8f5`, hash-verified throwaway copy).
- `tests/test_work_mod_real_backend.py` adds two tests:
  - `test_completions_killed_before_review_converge_after_restart` covers the exact two-worker sequence and a forged model call that both the mod and the router refuse;
  - `test_absent_running_worker_without_a_receipt_is_not_observed_terminal_after_restart`.

- The real-backend server reads every store just before and just after each `restart_submitted_terminal` call, with nothing in between (`measured_tool`). It records version and SHA-256 of the on-disk bytes for the board, ledger, coordination, finalization and native evidence; a digest of receipts; and every lease's workspace manifest bytes.
- `test_reviewed_terminal_result_with_mismatched_proofs_is_repaired_by_a_process_restart` (scenario `proof-mismatch-restart`), for a pending review:
  - session.start alone sends exactly 1 event, and the proofs converge on the ledger proof. That call changes only this lease's workspace manifest bytes.
  - A new process and a reload each send 1 more; same-module `turn.complete` boundaries send 0 (total 3).
  - With the proofs matched, none of those calls and none of the seven boundaries change any store's version or bytes. The native evidence file is created during the first `turn.complete`, outside every restart call.
  - One review and one wake. No restart error, and an empty `/work-mode parallel` failure log in every module.
  - Approve reaches `finalized` and the run is archived. Later boundaries send 0 events.
- `test_rework_blocked_after_rebind_is_a_restart_no_op_until_the_stage_unblocks` (scenario `rework-blocked-rebind`). The server makes the resume run's assignment fail (`fault_resume_assignment`), so the board stage blocks after the rebind.
  - Blocked state: `deciding`, `stage_failed`, stages accepted and workspace true, predecessor `submitted`, manifest `rebound`.
  - Each restart call succeeds and changes no store's version or bytes, including both workspace manifests.
  - Sends: 1 per module load (process 1, process 2, reload) and 0 at seven `turn.complete` boundaries. The failure log is empty in every module.
  - After the fault clears, the same decision reaches `rework_ready`, the predecessor is `done`, and exactly one resume fork is bound.
- Fail-before, measured on hash-verified copies (`run_scenario.py`/`fail_before.py` in a temp dir; register variants rebuilt and hash-checked as `a923bb10`/`de72a5c3`):

| register.js / backend | rework-blocked | proof-mismatch |
|---|---|---|
| `135fc5e6` / `ec15586d` | not run | fails `1 != 0` (skip: session.start sends nothing; approve denied) |
| `a923bb10` / `ec15586d` | fails: 10 restart errors `workspace ownership was handed off; retained`; per-module failure logs of 2, 4 and 3 entries | fails `3 != 8` (sends every boundary) |
| `de72a5c3` / `ec15586d` | fails: 8 restart errors | passes (once-set plus pending repair) |
| `a923bb10` / fixed backend | fails `2 != 9` (no errors; sends every boundary) | fails `3 != 8` |
| fixed / fixed | passes | passes |

- The same run on `135fc5e6` fails `completion-restart` with `3 != 1`.
- The decided-review condition also covers `awaiting_capacity` and `rework_ready` with `decision: rework`, and `deciding` with either decision, because each has a recorded decision. Only the blocked `deciding` rework case has its own test.

On a hash-verified throwaway copy of the pre-fix product files, every new test fails except one. `test_absent_running_worker_without_a_receipt_is_not_observed_terminal_after_restart` passes there by construction, because the old code has no restart path at all. It guards the new path. All of them pass on the fixed tree.

# Remaining uncertainty

- Not re-run in a real owned console. The fixed tree is frozen (restart work: `register.js` `09255642`, `work_parallel_mcp.py` `35214869`; review-restart-terminal re-reviewed `8d02d969`, whose code differs only inside `reworkLaunchNonce`). It is not yet installed or run in an owned E2E.
- The id-tracking guard assumes Claude Code native agent ids are never reused across processes. The existing ownership tombstones make the same assumption. This is not verified against the Claude Code source.
- It is not established whether a ciel-work `turn.complete` for n4's own end was queued behind n2's hook and lost in the kill. The debug log shows no such hook settling. The fix does not depend on it.
