---
type: fix-record
title: Walkie stopped parallel work — fork slots held by dead or never-launched leases, and no pump during a long main turn
description: All P1 slots (and the P2/P3 device slots) were held by leases with no running fork, the orphan sweep refused two of them and ran only at launch, and nothing woke the pump while main worked inline.
timestamp: 2026-11-16T03:45:00Z
tags: [work, parallel, fork, slots, orphan, pump, walkie]
---

# Symptom (user, 2026-11-16)

> 또 지금 왜 멈춰있는거지?? 블락되어있음 다른 아이템을 포크세션으로 시작해야하는데??
> 지금하고있는일이 블락이면 다른 워크보드 큐로 다시 일을 이동해야지 왜 멈춰있는거지??
> 병렬화 실패. 이렇게 멈춘상황이 가장 나쁜상황
> 심지이 싸이드 액터도 아무런일을안함

# Measured (read-only, Walkie router 9505 REST and the device ledger)

- **Forks:** `forks.confirmed_count = 0`. The queue held executing P1 goals (n133, n766, n1849, n1025) and the board was `ready`.
- **Slots:** `device.active_by_priority = {P1: 3, P2: 1, P3: 1}` against limits `{P1: 3, P2: 2, P3: 1}`. P1 and P3 were full. Every slot-holding lease had no running fork:

| Lease | Item | Priority | State | Holder |
| --- | --- | --- | --- | --- |
| ee229513 | n1856 | P1 | reserved, no host | Walkie, owner process dead (rework reservation) |
| 3913bafb | n1438 | P1 | submitted, `slot: true` | Walkie, owner dead |
| af627dd6 | n1861 | P1 | waiting_input, `slot: true` (reacquired) | Walkie, owner dead |
| 334e3089 | n176 | P3 | reserved, no host, no owner, no dispatch name | live conversation b43eb527 |
| cbf3f2df | n212 | P2 | reserved, no host, no owner, no dispatch name | live conversation b43eb527 |

- **Orphan preview:** the settings row "Clean up: leases whose owner session is gone" listed all five as orphans (3 dead, 2 legacy_no_owner).
- **Launch-time sweep at the Walkie restart** (router log 19:38:14 CST): `work_orphan_kept lease=3913bafb owner=dead reason="unexpected artifact"`, `work_orphan_kept lease=ee229513 owner=dead reason="unbound worker"`, `released=0`. n1861 was not slot-holding at that moment. It reacquired its slot afterwards to resume a fork of the dead process, and the sweep runs only at launch.
- **Main:** main was not stopped. It was working inline on n2020, its transcript showing Edit calls at 03:26–03:27Z.
- **Pump triggers:** the pump has no trigger while main runs a long turn with no forks (turn.complete, Stop, worker reports, ledger events, session start, idle refresh only). After the five slots were freed at 03:31:10Z (below), no reservation appeared between 03:31:23 and 03:34:56Z (8 reads at 30 s intervals).
- **Side gate re-check:** it ran twice (02:29Z and 02:59Z). It runs only while main is idle and no fork runs, which is the stated requirement. Main was busy.

# Fix

- **F1 (`work_parallel.orphan_release`):**
  - A dead or legacy owner's unbound reservation is deleted, as a failed launch is. Previously only legacy owners got this.
  - A slot-holding lease with an artifact releases only its slot and stays `submitted` with its artifact, so it remains reviewable.
- **F2 (`work_parallel_orphan_sweep.sweep_while_running`):** the same sweep also runs from the router's authenticated host status read, at most once per 60 s. Before, it ran only at launch.
- **F4:** an unbound, owner-less reservation with no dispatch name is released even in a live conversation. It was written by a build before dispatch names (4eebfdc, 2026-11-09) and owner records (34966b6, 2026-11-12), so no launch can still bind it. A lease's `created_at` is its item's creation time, so age is not used.
- **F5 (`register.js busyTurnPump`):** main's own `turn.step` wakes the pump at most once per 30 s. Fork steps do not.

# Verification

- **Sweep on a copy of the live ledger** (version 603) with the fixed code: all five released; free slots P1 3, P2 2, P3 1. n1438 stays `submitted` with its artifact ([evidence/sweep-on-ledger-copy.txt](evidence/sweep-on-ledger-copy.txt)).
- **Live release** at 03:31:10Z with the fixed code, the same operation as the launch sweep. No session or router was restarted. All five released, ledger 603 → 608, slots `{P1: 0, P2: 0, P3: 0}` ([evidence/live-release.txt](evidence/live-release.txt)).
- **Tests:**
  - `tests/test_work_parallel_slot_stall.py` (5): dead unbound reservation released; submitted-with-slot frees it and stays reviewable; the full Walkie set is freed while a named owner-less reservation is kept; the running-router throttle; only the host status read sweeps.
  - Parallel/MCP/HTTP suites (36 files): 603 passed.
- **`tests/work_mod_harness.mjs` scenario `main-steps-wake-the-pump-during-a-long-turn`:**
  - with the previous register.js (d5560bf): `firstStepPumped: false, nextWindowPumped: false`;
  - with the fix: `true, throttled, true`, and a fork step does not pump.
- **`claude plugin test ciel-work`:** 103/103.

# Release (F1–F5)

- **Commits:** `306d1ab` + `e37272d` (ruff F401) on `beta` and `feature/priority-models`.
- **Publish run 43802033942:** success, every job (quality, python-minimum, unit 1–3/3, unit-real-backend, runtime, router, channel, publish).
- **npm `beta`:** `0.2.51-beta.20261116-042320.e37272d`, gitHead `e37272d200bf…`. The installed tree equals the commit except the version stamp in `package.json` and `runtime_constants.py`.
- **Installed-code check:** the slot-stall tests and the busy-turn pump scenario, run from a copy of the installed package, passed 8/8 ([evidence/installed-e37272d.txt](evidence/installed-e37272d.txt)).

# Second cause: no runnable leaf, and most gates never re-checked

After the slots were freed, the pump still had nothing to start.

- **Measured (read-only):**
  - At 04:09:51Z (board version 5926), `runnable_leaves` was 0. 269 leaves were excluded by `waiting_on` and 2 because their goal was still planning. Every executing root had no runnable leaf ([evidence/walkie-candidates-0410Z.txt](evidence/walkie-candidates-0410Z.txt)).
  - At board version 5928, `work_gate_check.gates()` returned 270 gates (waiting or blocked items).
- **Gate re-check covered 24 of them:** `work_gate_check.gates()` returned `rows[:MAX_GATES]` with `MAX_GATES = 24`, and one check queued one side event. The other 246 gates were never shown to the side actor, so their waits could not be released.

## Fix

- **F6 (`work_gate_check`):** every gate is checked each interval. `due()` splits them into events of 24 (`BATCH_GATES`), up to 20 events (`MAX_BATCHES`, 480 gates). Each event says which part it is ("gates 25-48 of 60 (event 2 of 3)") and has its own event key. `work_side_gate_check` queues one durable side receipt per event and records all receipt ids.
- **F7 (one round at a time):** a new round is not queued while any receipt of the last round is still received, claimed or backed off for retry. Processed, duplicate and quarantined receipts are finished. The response reason is `previous_round_pending`. This keeps a slow side queue from piling up rounds.

## Verification

- **Walkie's live board (read-only, version 5928):** 270 gates → 12 events of `[24, …, 6]`, largest event text 18015 bytes ([evidence/gate-batches-walkie-board.txt](evidence/gate-batches-walkie-board.txt)).
- **`tests/test_work_gate_check.py` (15):**
  - 60 gates → 3 events covering every gate in order, distinct keys, part label;
  - the MCP check queues 3 receipts;
  - the next round waits for the last round, then for the interval;
  - a retrying receipt holds the round; a quarantined one does not.
- **Side, MCP, gate and parallel suites (56 files, Windows):** 725 passed, 2 skipped, 1 failed. The failure was `test_work_parallel_rework_worktree.py::IdleSettlesLaunchTests` (a launch-binding subtest, not on the gate-check path). The same class passed 3/3 when rerun alone. CI passed the same file on `e37272d`.
- **Linux (python:3.11-slim):** gate, MCP tools, side MCP, side real-backend and mod real-backend modules: 195 passed, 1 skipped.

# Not yet effective in the running Walkie

The running Walkie router and mod are the previous build. With the slots now free, the next pump trigger (main's turn end, Stop, or the next side fork's turn end) starts forks. F2 and F5 take effect after Walkie restarts on the new build. No session was restarted.
