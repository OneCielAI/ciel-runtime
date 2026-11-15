---
type: implementation-record
title: Released-wait lane over the existing reserve and reacquire paths
description: The end-to-end released-wait behavior implemented on the existing pump paths, and every point where it deviates from design v1.
timestamp: 2026-11-15T01:20:00Z
tags: [work, sse, wait, queue, sidecar, scheduler]
---

# What now happens

When an SSE side decision releases a recorded wait whose exact text, goal and unfinished state still match:

1. **Board (one transaction).** `side_work_decision._release_leaf` replaces only that leaf. It clears `waiting_on` and turns `blocked` into `pending`; every other status stays as it was. The bounded audit line is appended to the leaf's note and its `updated_at` is set. Ancestors and descendants stay byte-for-byte unchanged. It deliberately avoids `update_node`, which reopens ancestors. A wait on a non-leaf is refused as `non_leaf_wait`. The committed result carries `unblocked_waits` and the bounded `wait_boost` receipt from [the design](/design.md#board-recovery-receipt), which holds ids and the SHA-256 of the wait text only.
2. **Sidecar (after the board commit).** `side_intake_mcp` reads the ledger first and builds a bounded parked projection of slot-free `waiting_input` leases for this scope. After the board commit, `work_wait_release.record_released` creates one `boosted` row per receipt by strict CAS. The same receipt is idempotent, and a `consuming` row is never replaced.
3. **Scheduling (`_reserve_assignment`, ordinary branch).** `WaitLane.reconcile` re-reads the board on every pass. It removes rows whose item is gone, done, waiting again, blocked again, or under a different root. It consumes any row whose item already has an active lease: a running worker is recorded as `running_notified`, and a launch the earlier pass reserved is removed. Then `WaitLane.order` puts boosted launches ahead of ordinary candidates, newest release first, tie-broken by `(-release_at, release_receipt, item_id)`. Effective P0 stays first. Each priority keeps its own capacity, and the stored priority and queue order never change.
4. **Parked worker.** A boosted row whose pinned parked lease is still `waiting_input` with no slot holds one free slot in that lease's priority. Ordinary reserves in that priority are refused with "the next P1 slot is kept for a released parked worker; main resumes it with SendMessage". The existing side notice already tells main which worker to resume. When main's `SendMessage` reacquires the slot, the row becomes `consumed` with outcome `reacquired`. A capacity refusal changes nothing.
5. **Running worker.** It keeps the existing direct `session.send` wake. The next selection pass consumes its row as `running_notified`.
6. **Surfaces.** One allowlisted `wait_boost` block (`work_wait_surfaces.project_wait_status`) is shared by all four surfaces:
   - the model and public projections;
   - REST `/ca/work/api/v1/parallel`;
   - `ciel-runtimectl work parallel` and `parallel capacity`;
   - the web Parallel tab's "Released waits" section, with the design's help text;
   - the terminal `/work-mode parallel` line `RELEASED WAITS`.

   Behaviors are `parked_first_eligible`, `launch_first_eligible` and `consuming`. The block never shows lease, host, receipt or wait text.

# Deviations from design v1

| Design v1 | This lane | Why |
|---|---|---|
| A new launch goes through `wait_boost_*` host events, a BindSaga and a WorkspaceChild, and is consumed `native_bound` at bind | The existing pump `reserve_next` launches it. The row is removed after the reservation commits, and the ledger lease is the launch record | The store reserves `native_bound` for the full saga (`native bound terminal children`), and no JS host caller of the saga exists. The pump's reserve, bind and release path is already durable |
| A crash between board and sidecar is recovered from the board receipt | Only the first commit of a decision creates the row; a replay creates nothing | A replay after the launch removed the row would boost the item again. Losing a boost leaves the released item in ordinary order, which is the safe failure |
| A stale row is cancelled with terminal audit (`reblocked`, `terminal`, `identity_mismatch`) | The stale row is removed | The store's cancel manifest requires an open attempt, and a boosted row has none. The board note keeps the release audit |
| The mod reacquires and sends to the parked worker itself | Main resumes it with `SendMessage`, as the existing side notice instructs, and the slot stays held for it | The mod cannot call the native `SendMessage`. The existing wrapper already reacquires |
| Menu | The terminal `/work-mode parallel` status line | The settings menu is device-wide and per-key; the released-wait block is per session |

The accepted L1 store and L2–L4 modules stay in place unchanged. The host-only `wait_boost_*` route is still unused.

# Verification

- Unit tests `tests/test_wait_boost_lane.py`, 12 tests:
  - a blocked exact leaf becomes pending, and the blocked ancestor still gates selection;
  - a waiting-only leaf keeps its status;
  - a blocked non-leaf is refused;
  - stale text writes nothing;
  - the receipt creates one row, a replay creates nothing, and an invalid receipt creates nothing;
  - newest release first, then the other boost, then ordinary;
  - a capacity refusal keeps the boost;
  - a parked hold refuses the ordinary launch, and reacquire consumes `reacquired`;
  - a re-block removes the row, and a later release replaces it;
  - a done item's row is removed;
  - an unreadable sidecar falls back to ordinary order.
- Focused regression, 650 tests across the side decision, side intake MCP, side notices, ctl parity, web UI, parallel MCP, HTTP parallel, mod write gate, architecture and wait-boost bridge suites. One web marker assertion failed; it was fixed by keeping the review section's exact call, and the web suite then passed 39/39, including the opt-in Chrome browser tests.
- Owned end-to-end run on the real backend, 16/16 facts ([facts](/evidence/lane-e2e/facts.json), [driver](/evidence/lane-e2e/lane_e2e.py)). It used a temporary config dir with every inherited `CIEL_RUNTIME_*` removed. A real inbox event, real claim and real `work_side_resolve` unblocked leaf B and created its row. REST and the web console showed `launch_first_eligible`, and the real `reserve_next` took B before the newer ordinary C, consuming the row. A real parked worker on A was released: REST and the web showed `parked_first_eligible` with its P1 slot held, `reserve_next` was refused for ordinary C, and the real `reacquire` consumed the row as `reacquired`. Afterwards REST and the web showed `none`, and the stored priority was unchanged.
- Screenshots: [launch, desktop](/evidence/lane-e2e/01-launch-first-eligible-desktop-section.png), [launch, 390px](/evidence/lane-e2e/02-launch-first-eligible-390.png), [parked, desktop](/evidence/lane-e2e/03-parked-first-eligible-desktop-section.png), [parked, 390px](/evidence/lane-e2e/04-parked-first-eligible-390.png), [none](/evidence/lane-e2e/05-none-desktop-section.png).

This run did not include a live Claude session, a native fork or a real Walkie SSE. The side decision JSON was supplied as the actor would return it.
