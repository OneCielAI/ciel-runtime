---
type: fix-journal
title: Rework replacement stuck when the ledger bind commits but launch_bound does not
description: A concurrent finalization write could make launch_bound fail after the ledger bind committed. Neither the backend nor the mod retried, so the replacement never got a workspace bind or a review. The backend now retries, the mod resends the idempotent bind at the next boundary, and launch_bound accepts a person's request for that exact launch.
status: applied-uncommitted
timestamp: 2026-11-12T08:10:00Z
tags: [work-parallel, main-review, rework-launch, bind, launch-bound, cas-retry, restart-recovery]
---

# Defect

Reported first as a static open item in [the projection fix](review-projection-superseded-rework.md) ("Bind commit before `launch_bound`"). It is now reproduced deterministically.

Line numbers below are for the draft bytes unless marked "worktree".

- A fresh bind (`work_parallel_mcp.py:1502-1513`) runs `_check_rework_launch`, `store.bind`, `_bind_coordination_run` and then `_bind_rework_launch`. The workspace bind (`:1561-1564`) runs only after all of them.
- Before the fix, `_bind_rework_launch` read the finalization version once and called `launch_bound` with it. Finalization is its own CAS file, so any unrelated review or wake write between the read and the call raised `Work finalization changed; reread` (`work_parallel_finalization.py:318`).
- `parallelEvent` (`register.js:1465-1471`) retries only `/state changed; reread/` (`:1469`), so that wording was not retried.
- `bindReworkFork` (`:2008-2034`) treats a failed bind as success when the ledger `host_id` already equals the fork. The pump's host-id branch in `parallelStartRework` (worktree `:2206-2217`) only tracked the fork.
- Result: the ledger lease is `running` and bound, but the launch stays `spawn_observed` (or `launch_intent`) and the workspace manifest has no host.
  - The replacement's idle reconcile then fails `workspace host binding mismatch`, so no replacement review is ever published.
  - After a restart, `restart_submitted_terminal` fails the same way.
- A person can still press "Resolve stuck launch". The web shows it for `launch_intent`/`spawn_observed` after 30 s (`work_http_controller.py:449-455`). The launch then becomes `manual_requested`.
  - Before the fix, nothing processed `manual_requested` for a lease that has a `host_id`: `reconcileReworkLaunch` is reached only for reserved leases without one (`register.js:2229` onward and `parallelHydrate` `:1919`).
  - `launch_bound` refused that state. It stayed stuck for good.

Measured on the worktree bytes (`work_parallel_mcp.py` `35214869`, `work_parallel_finalization.py` `c7610d77`, `register.js` `eab3ad33`), harness `bind-launch-bound-*` (below):

- With 1 concurrent write, the bind fails once (`2 != 1` launch_bound calls expected).
- With every attempt raced, the launch stays `spawn_observed`, the manifest host stays `None`, and approve is denied with `positive matching idle workspace proof required`.
- With the person's request first, `manual_requested` stays for good, with the same approve denial.

# Fix (draft)

## 1. Backend retry: `_bind_rework_launch` (`work_parallel_mcp.py:1149-1173`)

- Up to `COORDINATION_BIND_ATTEMPTS` (8) attempts. Each attempt re-reads finalization and calls `launch_bound` with that version. It returns on success, and when no review row names this replacement.
- On exhaustion it raises `WorkParallelConflict("Work finalization state changed; reread")`.
  - Why this wording: it matches `/state changed; reread/` at `register.js:1469`, so `parallelEvent` retries the whole bind up to 3 times without any mod change. This mirrors `_bind_coordination_run` (`:948-971`, `Work coordination state changed; reread`).
  - Each mod retry finds the ledger already bound to the same host. It takes the idempotent branch (`:1496-1501`): `_bind_coordination_run` is a no-op for a run already bound to this agent, `_bind_rework_launch` runs again, then the workspace bind.
- The older wording `Work finalization changed; reread` is unchanged where it already exists: `work_parallel_finalization.py:318` and `work_parallel_mcp.py:760`.

## 2. Mod resend at a later boundary (`register.js` `parallelStartRework`, `:2206-2219`)

- In the host-id branch, while the projected `review.launch.state` is in `REWORK_UNRESOLVED` (`launch_intent`, `spawn_observed` or `manual_requested`, `:2096`), the pump re-sends the idempotent bind `{host_id: lease.host_id, agent_name: lease.agent_name}`. It then tracks the fork as before.
  - On failure it records one `rework-bind` failure and skips tracking for this boundary.
  - `none`, `spawn_denied` and `bound` never resend.
- It reads only the projected review row, so it adds no router call.
  - A first draft called `reworkLaunchStatus` here instead. `test_later_process_resolves_an_unlisted_launch_as_absent_then_starts_one_fork` failed with 2 extra `rework_launch_status` calls.
  - A second draft (`register.js` `c8880b13`) resent for every state except `bound`. team-lead rejected it because a persistent refusal adds one failure per boundary. The four-boundary test below fails on it.
- Unchanged: `bindReworkFork` (its ledger-host fallback now leaves recovery to this branch), the fresh-bind gate and every other function. `register.js` diff: 13 insertions and 2 deletions, all inside `parallelStartRework`.

| Function | worktree `eab3ad33` | draft `ea7768f5` |
|---|---|---|
| `reworkLaunchNonce` | `256993a4` | `256993a4` |
| `parallelRestartSubmitted` | `53c31bc0` | `53c31bc0` |
| `bindReworkFork` | `fc8e91d7` | `fc8e91d7` |
| `parallelDirectSpawn` | `8d0a7fa1` | `8d0a7fa1` |
| `parallelHydrate` | `ec418d69` | `ec418d69` |
| `reconcileReworkLaunch` | `4bcd70d7` | `4bcd70d7` |
| `parallelStartRework` | `ed8e2d68` | `eb116117` (the change) |

These are SHA-256 prefixes of each function body, from its `function` line up to the next top-level declaration.

## 3. `launch_bound` settles a matching request (`work_parallel_finalization.py:633-656`)

team-lead approved option A with exact-match conditions. For `manual_requested`, the check uses the state the request was made from (`manual_from`):

- `manual_from == "spawn_observed"`: the launch's observed `agent_id` must equal the binding agent;
- `manual_from == "launch_intent"`: `dispatch_name` must equal the agent name, like an unrequested intent.

Anything else is refused with the existing `rework launch was not observed for this agent`.

What it writes:

- On success it writes only existing fields: `state="bound"`, `agent_id`, `launch_ref=None`, `manual_from=None`, `updated_at`, and the row's `updated_at`.
- No audit kind is added; the `manual_requested` audit row stays as history.
- The validator's `bound` branch (`:157-158`) accepts this.

Bound stays terminal:

- `launch_manual_request` needs an unresolved state;
- `launch_manual` and `launch_present` need `manual_requested`;
- `launch_absent` needs `launch_intent`/`spawn_observed`.

Each is refused after the bind without changing the file (tested).

Notes (static, from source):

- For `manual_from == "launch_intent"` the store does not compare agent ids, the same as the existing intent rule.
- Two paths reach `launch_bound` while the launch is `manual_requested`. (Corrected after review-restart-terminal; an earlier version of this entry named only the first.)
  1. The idempotent branch (`work_parallel_mcp.py:1496-1501`). It requires the ledger's `host_id` to equal the binding agent.
  2. A fresh bind (`:1502-1513`).
     - `_check_rework_launch` passes against `launch_intent` or `spawn_observed`.
     - A person's request then lands before `_bind_rework_launch` runs. Nothing locks across the ledger, coordination and finalization stores, so `store.bind` and `_bind_coordination_run` may already have committed.
- Path 2 is safe. `launch_manual_request` (`work_parallel_finalization.py:545-570`) writes only `state`, `manual_from`, `proof`, `updated_at` and one audit row (`:562-567`). It never changes `agent_id` or `dispatch_name`, so `launch_bound` re-checks the same agent id or dispatch name that `_check_rework_launch` accepted.
- Before this fix, path 2 also stranded the lease. `launch_bound` refused `manual_requested` after the ledger and coordination halves had committed, and the workspace bind was skipped.
- Path 2 is not exercised by any test.
- The fresh-bind gate `_check_rework_launch` (`:1136-1146`) itself still refuses `manual_requested`. A reservation with no host id still needs `rework_launch_present` before bind, as before (existing test `test_manual_request_records_only_and_host_resolves_absent`).

## Schema and old readers

- No field, state, audit kind or file is added. The finalization schema stays 5. The ledger, coordination and workspace manifest gain no new values.
- `test_old_readers_accept_ledger_after_approve_and_rework` and the new settle tests validate `work-parallel.json` with the `4eebfdc` and `5c91ea8` readers. Neither commit contains a finalization reader.

# Tests (draft)

`tests/work_mod_real_backend_server.py`:

- `arm_launch_bound_race(n)` writes one real concurrent finalization change (the source review's `updated_at`, through `_change`) before each of the next `n` `launch_bound` calls. `n` is 0 to 100; 0 disarms.
- `force_launch_state` rewrites a launch as a valid `none` or `spawn_denied` row through the validating CAS writer.

`tests/work_mod_real_backend_harness.mjs` scenarios `bind-launch-bound-{race,exhausted,restart,manual,manual-restart,none,denied}` run the real `register.js` against the real backend.

| Test | Covers |
|---|---|
| `test_work_mod_real_backend.py` `test_one_concurrent_finalization_write_during_rework_bind_is_retried_in_request` | 1 conflict, then the in-request re-read: 2 `launch_bound` calls, 1 bind, then review and finalize |
| `…_exhausted_rework_bind_is_replayed_at_the_next_boundary` | Every attempt raced: 3 binds fail with the retried wording. The next `turn.complete` resends once (after one more conflict) and binds. |
| `…_exhausted_rework_bind_is_recovered_by_a_new_process` | Exhausted, report, process gone. The new process's session.start: restart call `workspace host binding mismatch`, then the pump resends and binds. The next boundary's restart call succeeds and publishes the review. |
| `…_person_request_on_an_exhausted_rework_bind_is_settled_at_the_next_boundary` | Exhausted, then a confirmed web resolve: `manual_requested` from `spawn_observed`, manifest host `None`. The next boundary sends 1 bind and it binds; audit `["manual_requested"]` kept, `manual_from` null. Review, then finalized. No later bind is sent. |
| `…_person_request_on_an_exhausted_rework_bind_is_settled_by_a_new_process` | The same with a report and a process restart in between. Restart calls: mismatch, then success. |
| `…_unresendable_launch_of_a_ledger_bound_lease_sends_nothing_at_four_boundaries` | `none` and `spawn_denied` on a bound lease: 4 boundaries, 0 bind sends, 0 new failure-ring entries |
| `test_work_parallel_finalization.py` `test_request_on_ledger_bound_observed_launch_is_settled_by_the_exact_bind_replay`, `…_named_intent_is_settled_by_its_dispatch_name` | Through the MCP event, with 8 raced `launch_bound` attempts: the idle is refused with mismatch and no review exists. The exact replay binds, and only the expected fields change. A second replay leaves the bytes identical. Request, manual resolve, present and absent are each refused with identical bytes. Then review, finalize, old readers. |
| `…_requested_observed_launch_refuses_any_other_agent`, `…_requested_named_intent_refuses_any_other_agent_or_name` | Store `launch_bound` with another agent (observed) or another or missing name (intent), and the bind event with another host id or name: all refused, bytes unchanged. Control: the exact replay binds. |

In every recovery the second exact bind (`measured_tool`) changed no store's version or bytes and no manifest. After approve, the model's pending list is empty, so the predecessor hide (`c7610d77`) still applies.

## Restart order (static, from source; corrected after review-restart-terminal)

The order differs for interactive and non-interactive sessions:

- **Interactive session.start.** It runs `parallelHydrate`, then `parallelRecoverWorkers`, then the pump (`register.js:2496-2498`).
  - `parallelRecoverWorkers` calls `parallelRestartSubmitted` (`:2406`). That call fails with `workspace host binding mismatch` and is not recorded in the once-set.
  - The pump resends and repairs the bind.
  - The review is published at the next `turn.complete`, whose `parallelRestartSubmitted` (`:2341`) now succeeds.
  - The two restart tests measure this order: every harness `start` passes `isInteractive: true` (harness `:131`).
- **Non-interactive session.start.** It runs no pump (`:2498`), so the bind is not repaired there.
  - The first `turn.complete` calls `parallelRestartSubmitted` (`:2341`) before its pump (`:2363`). It fails once more, and that pump repairs the bind.
  - The second `turn.complete` publishes the review.
  - Any other pump trigger in between can repair the bind earlier, for example a worker report (`:1560`) or a host event (`:1629`).
  - Not tested.

## Fail-before (measured 2026-11-12)

Test code is from the draft. Product bytes are hash-checked copies in `%TEMP%\skip-only-probe\retry\` (`wt-bytes`, `fb-root`, `fb-root2`), run with `fail_before.py`.

| Product bytes | real_backend (6 tests) | finalization (4 new tests) |
|---|---|---|
| worktree: mcp `35214869`, fin `c7610d77`, reg `eab3ad33` | 7 failures: race `2 != 1`; exhausted `3 != 1`; restart `('bound','agent-1') != ('spawn_observed','agent-1')`; both manual variants on the bind errors (old wording); unresendable `3 != 1` ‡ | 4 fail on the exhaustion wording |
| mcp `704cb811` + fin `c7610d77` + reg `c8880b13` | 4 failures: both manual variants `[None] != ['rework launch was not observed for this agent']`; unresendable `none` and `spawn_denied` `(0, []) != (4, [4 new rework-bind entries])` | 4 fail at the exact replay: `rework launch was not observed for this agent` |
| draft: mcp `704cb811`, fin `5c9afdc5`, reg `ea7768f5` | pass | pass |

‡ On the worktree bytes the unresendable test fails only at its setup check (1 bind, because there is no backend retry). Its 0-send/0-failure assertions hold there, because that code never resends. The rejected `c8880b13` fails them.

Cross-checks on the final bytes, 07:28-07:29Z, five real-backend tests (all but the unresendable one):

- draft backend + worktree register: the race test passes. Exhausted fails `[None] != []`, restart fails `('bound','agent-1') != ('spawn_observed','agent-1')`, and both manual variants fail `[None] != []`. Nothing resends at a later boundary.
- worktree backend + draft register: all 5 fail. There is no in-request retry, the wording is not retried, and a `manual_requested` launch is refused.

Both halves are needed.

# Main-review E2E checkpoint B: durable fields unchanged

Measured: harness scenarios `intent-absent-restart`, `observed-absent-restart`, `rework-restart`, `intent-listed-restart`, `manual-present-restart` and `manual-pane-absent`, on worktree bytes and on the draft. After random ids and times are normalised, the outputs differ only in seed host ids, temp paths and git heads. They contain:

- launch states `launch_intent`/`spawn_observed`/`manual_requested` → `bound`;
- the `absent` audit rows (2, 1, 0, 0, 0, 0);
- the ledger `host_id` values.

Static (driver `owned_main_review_probe_v2.py` `9be390ea`, `:1631-1715`, `:1840-1851`):

- At checkpoint B the gate holds the replacement's first bind, so it never reaches the backend: launch `launch_intent`/`spawn_observed`, ledger `host_id` `None`.
- The later absence resolution goes through `reconcileReworkLaunch` for a reserved lease with no `host_id`. The new branch requires `lease.host_id`, and `_check_rework_launch` is unchanged.

# Runs (draft, foreground, every `CIEL_RUNTIME_*` unset, fresh temp config per group, `-rs`)

| Group | Window (UTC 2026-11-12) | Result |
|---|---|---|
| write_gate (`test_work_mod_write_gate`) | 07:17:23-07:17:57 | 109 passed, 58 subtests |
| finalization (`test_work_parallel_finalization`) | 07:17:57-07:20:24 | 79 passed, 129 subtests |
| restart_terminal (`test_work_parallel_restart_terminal`) | 07:20:24-07:20:50 | 11 passed, 9 subtests |
| real_backend (`test_work_mod_real_backend`) | 07:20:58-07:23:46 | 29 passed, 2 subtests |
| web/observatory (`test_work_http_parallel_review`, `test_work_http_controller`, `test_work_observatory`, `test_work_web_ui`) | 07:23:46-07:24:18 | 67 passed, 4 skipped (opt-in browser verification), 12 subtests |

`tests/test_side_intake_inbox.py` (`171ea549`) and `side-intake.js` (`9b955068`) are unchanged.

# Not covered

- **Paused or disabled policy (static).** The resend lives in `parallelStartRework`, which the pump reaches only when the policy is enabled and not paused (`register.js:2297-2298`). While paused, a replacement in this state stays unbound. Its review is not published until dispatch resumes. Not measured.
- **Persistent refusal in a resendable state (static).** A `manual_requested`/`spawn_observed`/`launch_intent` launch whose exact match fails would add one `rework-bind` failure per boundary.
  - Through product paths the ledger's `host_id` and `agent_name` come from a fresh bind that passed `_check_rework_launch` against this same launch.
  - Neither `spawn_observed` nor `manual_requested` changes the observed agent or the dispatch name.
  - Not reached in any test.
- **Late-fork rejection skipped on a failed resend (static, low; from review-restart-terminal).**
  - When the resend fails, `continue` (`register.js:2217`) skips the rest of the host-id branch for that lease at that boundary. That includes `rejectLateReworkAgents` (`:2226`).
  - A late fork of an earlier launch attempt is therefore not rejected at that boundary. The next boundary tries again.
  - Ownership is already set before any bind by both spawn paths (`parallelDirectSpawn` `:1720`, `bindReworkFork` `:2009`) and by `parallelHydrate` (`:1910`).
  - `parallelBound` is set by `parallelHydrate` (`:1911`) and `bindReworkFork` (`:2027`).
    - Refinement read from source: after `parallelDirectSpawn`'s own failed rework bind (`:1737-1742`), this module sets it only when a resend here succeeds (`:2223`) or at the next session.start's hydrate.
    - Until then `turn.complete` does not reconcile that lease. Its idle could not succeed anyway while the workspace is unbound.
- **Broken retained worktree (static, pre-existing; from review-restart-terminal).**
  - The workspace bind and the idle write both go through `_verify` (`work_parallel_workspace.py:115-136`, via `_activity` `:322-332`).
  - If the retained worktree was deleted, its branch switched, or its registration removed, they raise `owned path unavailable`, `Git ownership/branch changed` or `owned registration missing` (each "retained").
  - In a bind event this happens after the ledger, coordination and launch halves have committed (`work_parallel_mcp.py:1561-1564`). The idle reconcile then fails the same way, so the replacement is stranded before review.
  - By then the launch is `bound`, so the mod does not resend, and a resend would fail the same check.
  - The same was true before this fix. Not measured.
- The harness calls the web controller's resolve directly. The browser shows the button only once the launch is 30 s old.
- Not run in an owned E2E console.
