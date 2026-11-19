---
type: fix
title: Gate re-check every 10 minutes, and none without a blocked or waiting item
description: The user asked for the side actor's blocked-item check to run every 10 minutes instead of 5, and not at all when no item is blocked or waiting. The default interval is now 10. The live Andy, Celly and KOBE routers were set to 10 through Work REST. With no gates the periodic check already queued nothing; a test now pins that at the new interval.
timestamp: 2026-11-19T21:30:00Z
tags: [work, gate-check, side-intake, settings, andy, celly, kobe]
---

# Request (user, 2026-11-19)

"The side actor that checks blocked items every 5 minutes doesn't need to run when there are no blocked items. Change it from 5 minutes to 10."

# Measured before the change ✅

From [evidence/andy-gate-cadence.txt](evidence/andy-gate-cadence.txt), Andy's side inbox on 2026-11-19 (UTC):
- 119 periodic gate-check events. The median gap was 5.7 minutes: the 5-minute interval, plus the wait while the previous round was still open.
- 9 turn-end events (main's turn ended). The median gap was 93 minutes.
- Andy's transcript has no "Gate re-check queued for 0" line. Every round today carried gates.

REST before the change: Andy, Celly and KOBE all reported `work_gate_check_minutes = 5`. That is the default; none of them stored a value.

# Change

- `work_gate_check.DEFAULT_MINUTES` changed from 5 to 10.
  - The host round (re-pump, diagnosis, notice) uses the same interval.
  - The discovery claim and the approval re-read use it too.
- No gates, no side event:
  - `tick_rows` already returns nothing when the board has no blocked or waiting item, unless main's turn just ended.
  - The router answers `no_gates` (existing test `test_conditions_gate_the_check`).
  - The new test `test_default_interval_is_ten_minutes_and_no_gates_queue_nothing` checks that no event comes at 10 minutes, at 100 minutes or after a day once the gates are cleared.
- Main's turn-end check (2026-11-18 requirement, prompt `turn_end`) is a separate trigger. It still runs at a turn end with no gates, at most once a minute. It was not changed.
- Live routers, through Work REST `settings-update` (compare-and-set on the settings fingerprint):

  | router | before | after |
  |---|---|---|
  | Andy | 5 | 10 ✅ |
  | Celly | 5 | 10 ✅ |
  | KOBE | 5 | 10 ✅ |

  Each router now stores `minutes: 10`, which equals the new default.

# Tests

- `test_work_gate_check.py`: the default and bounds now expect 10. The interval tests derive the wait from `DEFAULT_MINUTES`. The settings row shows 10.
- The 7 gate-check related files passed: 150 tests, 0 failures. See [log.md](log.md).
