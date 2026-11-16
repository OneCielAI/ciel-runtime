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

# Not yet effective in the running Walkie

The running Walkie router and mod are the previous build. With the slots now free, the next pump trigger (main's turn end, Stop, or the next side fork's turn end) starts forks. F2 and F5 take effect after Walkie restarts on the new build. No session was restarted.
