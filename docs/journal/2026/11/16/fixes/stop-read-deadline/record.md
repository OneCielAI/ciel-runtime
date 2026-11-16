---
type: fix-record
title: Turns stopped after compaction — Stop's 2 s Work board read deadline expired under turn-end load
description: Walkie's turns ended with runnable Work because the Stop hook's board read (work_status plus host pages) took longer than its 2 s deadline at a turn end; the deadline is now 10 s.
timestamp: 2026-11-16T06:10:00Z
tags: [work, stop, auto-continue, compaction, deadline, walkie]
---

# Symptom (user, 2026-11-16)

> 거의 모든세션에서 컴팩트뒤에 턴이 멈추는문제가 발생하고있음 / 특히 kobe 세션

# Measured (Walkie, read-only)

**How each turn after a compaction ended.** Source: the last 40 compactions in Walkie's main transcript ([evidence/walkie-compaction-turn-ends.txt](evidence/walkie-compaction-turn-ends.txt)). The turn did not stop at the compaction itself: after every one, the model went on working (9 to 1,325 assistant messages). The turn then ended in one of three ways:

| How the turn ended | Count |
| --- | --- |
| An API error ended the turn: transient retry limit, gateway stream interrupted, connection refused, 502 | 19 |
| A normal end_turn, and Stop logged "Ciel Work auto continue could not read the Work status, so unfinished items were not checked" | 18 |
| A normal end_turn only | 3 |

**When that notice was logged.** Source: every logged notice ([evidence/walkie-stop-unavailable.txt](evidence/walkie-stop-unavailable.txt)).
- From 2026-11-15 06:07Z, 20 of 41 Stops logged it. That is a floor: the notice is not repeated until the person writes.
- 19 of those 20 came 2.16–2.36 s after the end_turn. That is the 2,000 ms `STOP_READ_MS` deadline plus the other Stop hooks. A read that fails, rather than times out, returns sooner: one came at 0.24 s.

**Where the time goes.** The Stop read, timed against Walkie's router (9505) with the exact calls: `work_status format=json` with the host credential, then each of its 5 `work_host_page` pages ([evidence/stop-read-timing.txt](evidence/stop-read-timing.txt)):
- With nothing else running: 0.43–4.48 s per read. `work_status` itself took 281–3,259 ms; a page took 13–1,064 ms.
- With four other reads at the same moment, as at a turn end (Stop, the band refresh, the pump status and the side status all read then): 1.46–4.43 s.
- The baseline cost of any router call is 12–44 ms, so the time is the board projection itself (578 nodes), not the transport.

**Code.** `register.js` `stopSnapshot` races `readSnapshot` against `setTimeout(STOP_READ_MS)`. When the deadline wins, `workStop` logs the notice and returns without a continuation. The turn ends with runnable Work, and nothing re-prompts main until a person or a side wake does. The deadline was added in 037c55f (2026-10-29), when boards fit one page, because the hook budget pauses during MCP calls.

# Fix

- **`STOP_READ_MS` 2,000 → 10,000** (`ciel-work/hooks/register.js`). The longest read measured was 4.48 s, so 10 s leaves about 2.2× margin. A dead router still ends Stop, at 10 s.
- **Tests:**
  - New harness scenario `slow-board-read-still-continues-the-turn`: a board read that takes 3.5 s must still continue the turn to the runnable item.
  - The engine suite's deadline test now waits out 10 s, with a 20 s budget and a bound under 11 s.
  - The child/precompute test answers its post-pump read at once. Before, it had relied on the 2 s deadline to end that read.

# Verification

- **Fail-before / pass-after** (`tests/test_work_mod_write_gate.py::test_slow_board_read_still_continues_the_turn`):
  - With HEAD `register.js` (2 s): `block ''`, `unreadable True`, 2 s. The test fails.
  - With the fix: the turn continues to `n2` and nothing is logged.
- **`claude plugin test ciel-work`:** 103/103. The deadline test ran 10.2 s.
- **Python mod suites (Windows):** `test_work_mod_write_gate.py`, `test_side_intake_mod.py` and `test_work_mod_real_backend.py` passed, 315 tests (170 subtests).

# Not covered here

- **The 19 turns ended by API errors.** In Walkie's transcript, 57 turns ended with a synthetic "API Error: …" message. None of them was followed by a Stop hook run (no Stop attachment and no `stop_hook_summary` in the next rows), so the Work continuation never ran for them. That is a separate cause.
- **kobe.** Its transcript (329 MB, on 100.64.0.14) was being read when the SSH session was cut: "Device SSH authorization revoked or expired". No kobe result exists yet.
- **Linux (python:3.11-slim):** `tests/test_work_mod_write_gate.py` and `tests/test_work_gate_check.py` passed, 149 tests.

# Release

- **Commit:** `a5193cb` on `beta` and `feature/priority-models`.
- **Publish run 43818134629:** success, every job.
- **npm `beta`:** `0.2.51-beta.20261116-065613.a5193cb`, gitHead `a5193cbbe743…`. Integrity ok, and 622/624 files equal the tree; the 2 that differ carry the version stamp.
- **Installed locally.** The installed `register.js` has `STOP_READ_MS = 10000`. From a copy of the installed package:
  - `slow-board-read-still-continues-the-turn` and the busy-turn pump scenario: 2 passed.
  - The gate and slot-stall tests: 20 passed.
- **Running sessions:** Walkie (and kobe) still run the build they were launched with. The fix takes effect when they restart on this build. No session was restarted.
