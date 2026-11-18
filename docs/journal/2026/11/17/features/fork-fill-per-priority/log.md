# Log

## 2026-11-17

- Stage 3 started: parked leases leave the bound, per-priority diagnosis, `advise` wording fixed.
- Probe: `$.session.send` resumes a parked fork under the same agent id with its history.

## 2026-11-18

- Periodic pump, gate check while forks run and with side intake off, automatic resume, and the diagnosis record correction.
- **Rule broken:** the first E2E driver copied the real provider and API key. Run fill-1 stopped at the launcher and fill-2 died in a reboot. The coordinator deleted the scratch copy, and the driver was rewritten around a local stub. See [record.md](record.md).
- A real-load error found by `claude plugin test`: `$` passed to a variable-held function in `side-intake.js`. Fixed with a closure bound at `session.start`.
- Stub runs: the mod loaded and 8 forks ran. The resume failed first because the parked fork was no longer listed, then because the lease stayed `waiting_input`. Both fixed.
- Added by the coordinator: truthful P0 notices and per-goal P0 diagnosis. The P0 lane defect was reproduced and fixed.
- Final: 378 unit tests pass, plugin test 104 pass, ruff clean, fill E2E 22/22, P0 blocked E2E 9/9, no owned process left.
