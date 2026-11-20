# Change log

## 2026-11-19

- Compared the new KOBE report against read-only lease, coordination and finalization state.
- Reproduced an exact completed result missing from authenticated host status behind the model's 24-run cap.
- Added host-only full run projection. Model-facing limits and all decision checks remain unchanged.
- Windows restart-terminal and coordination receipt modules: 27 tests, no failures or errors (`.local/tmp/kobe-host-projection-after.txt`, local execution log).
- Linux Python 3.11 and 3.10: seven Work modules, 216 tests each, zero failures/errors, 8 skips each (historical beta source unavailable in the copied Linux tree).
- The mod now preserves the router's approval_changed boolean in the bounded review message. Node mod/writing-gate plus approval-workflow modules: 152 tests, zero failures/errors, 33 existing retired-behaviour skips. Real engine `claude plugin test`: 89 pass, zero fail.
- Installation and live recovery not yet verified.
