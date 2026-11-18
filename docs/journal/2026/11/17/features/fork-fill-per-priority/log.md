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

## 2026-11-18 deployment

- Committed as bdcff80. The Windows groups then found 11 failures in `test_work_mod_real_backend`, which the earlier targeted runs had not included.
  - Product defect: the new 1-minute pump and gate round retried a refused review wake once per timer. Those two triggers now leave a failed wake to the events and idle ticks that always paced it (3fb838e).
  - Test expectation: the gate round's dispatch notice and its `gate-check.json` record were counted as review delivery and pump writes. The harness now counts them apart and asserts at most one dispatch notice.
- Verification:
  - bdcff80: Linux Python 3.10, all three unit shards, 4,960 tests, 0 failures; Windows router, channel, runtime and unit shards 2 and 3 pass.
  - 3fb838e, the mod-dependent tests only (Python code did not change): real backend 116 run, 17 mod test files 549 run, `claude plugin test` 104 pass, 0 failures.
- Deployed 3fb838e: publish run 44290675912 succeeded; npm beta `0.2.51-beta.20261118-111905.3fb838e`.
- Installed locally: 631 of 633 package files equal the tree (`package.json` and `runtime_constants.py` carry the release stamp). Against the installed copy: 450 tests, 0 failures, 10 skipped; `claude plugin test`: 104 pass.
- Live sessions were not restarted. Celly and KOBE were not changed.
