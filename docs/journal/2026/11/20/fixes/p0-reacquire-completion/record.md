---
type: fix
title: A P0 worker's resume records its delivery, so the next resume gets a slot
description: Neither host resume path recorded the P0 reacquire send's outcome. The open child made every later reacquire return without a slot, and main's resume failed with "no active slot".
timestamp: 2026-11-20T16:20:00Z
tags: [work, parallel, p0, reacquire, resume]
---

# Report

Observed by the 15:40Z monitoring tick ([log](/docs/journal/2026/11/20/operations/live-monitoring/log.md)):
- Celly: main tried to resume n1143 (lease e5b6cdd7, P0 lane) at 15:20:43Z, 15:30:37Z and 15:39:59Z. Each attempt was refused with `Work item n1143 could not return to running: Parallel read failed: no active slot`.
- Lease state at the time: `waiting_input`, slot false. Its P0 reacquire send child 67a7c35c had stayed `slot_acquired` since 14:49:16Z.
- The worker reported a question on that lease at 15:15:23Z. A worker can report only from a running lease, so this proves the 14:49 send was delivered.

Read-only census of open P0 sends (`work-parallel-p0.json`), all `slot_acquired`:
- Celly: 13. 4 on leases still `waiting_input` (n1143, n1078, n1048, n1140). Each has a worker report after the send.
- Andy: 1 (orphaned lease).
- KOBE: 2. n955 is `waiting_input` with no later report; n20 is done.

# Cause

Confirmed in source (95cf25c):
- The design (journal 11-14, `priority-p0-fork-models/revised-encoding.md` section 6a, steps 3–5) requires the host to classify the native send and record `p0_reacquire_complete`: delivered, rolled back, or uncertain.
- Only tests call the router's `p0_reacquire_complete` event. No mod code calls it. Neither path does: main's SendMessage resume (`parallelToolCall`) or the pump's parked resume (`parallelResumeParked`).
- So every child stays open. The router's `reacquire` returns an existing open child as-is (`open_send_for_lease`), without taking a slot. The mod ignored that reply and sent `resume`. The ledger refused it with `no active slot` (`work_parallel.transition`).

# Change

- **Mod (`register.js`)**: both resume paths read the reacquire reply.
  - A child that this call did not acquire is not used for a send (`slot_reacquired` missing, `recovery_required`, or state other than `slot_acquired`). Main is told recovery is required, not `no active slot`.
  - After the native send, the host records `p0_reacquire_complete` with the exact child pins. The outcome follows the design's classification (`sent`, `failed`, `uncertain`).
  - A refused parked resume whose completion rolled the slot back does not send a second `wait`.
- **Router (`work_parallel_mcp.py`)**: before `reacquire` returns an open child, it checks the coordination receipts. If the worker filed a question, completion or handoff receipt on that lease after the child was created, the router records the child as `delivered`. That report is the definite success in step 4 of the design. A fresh child and slot then follow.
  - A child with no later worker report stays open, so its delivery is still unproven. That is Andy n1952 and KOBE n955.
- No model-facing text changed.

# Verification

[Evidence](/docs/journal/2026/11/20/fixes/p0-reacquire-completion/evidence/).

Fail-first: the new tests were copied onto a HEAD (95cf25c) worktree and run there.
- `fail-first-mod.txt`: 4 mod tests, 4 failures. No completion was recorded; with an old child, main still got `no active slot`. Both happened on main's SendMessage path and on the pump path.
- `fail-first-router.txt`: 2 router tests, 1 failure. Reacquire returned the old child (`ledger_after_version` 4, no `slot_reacquired`).

After the change:
- `test_work_parallel_p0_events.py` reacquire tests: 8 pass.
- Resume and P0 tests in `test_work_mod_write_gate.py`: 10 pass.
- `claude plugin validate`: passed. `claude plugin test` on ciel-work: 89 pass, 0 fail.
- ruff clean on the changed Python files. `ciel_runtime.py` is 4980 lines (unchanged).
- Windows, isolated config, affected bundle of 81 test files: 1323 tests, 0 failures, 56 skipped ([evidence/bundle-windows.txt](/docs/journal/2026/11/20/fixes/p0-reacquire-completion/evidence/bundle-windows.txt)).
  - pytest is not installed here, so the nine pytest-only wait suites were left out. `test_wait_boost_pytest_bridge` skipped itself.
- Linux Docker, python:3.11-slim with pytest 9.1.1, archive of 2cb27cc: the same set plus the nine pytest suites (90 files). 1323 tests, 0 failures, 111 skipped. Here the bridge ran the pytest suites ([evidence/bundle-linux.txt](/docs/journal/2026/11/20/fixes/p0-reacquire-completion/evidence/bundle-linux.txt)).
- Linux Docker python:3.10-slim, the changed modules: 228 tests, 0 failures ([evidence/py310.txt](/docs/journal/2026/11/20/fixes/p0-reacquire-completion/evidence/py310.txt)).

# Installed (not yet effective)

The two changed files were overlaid with hash checks and backups (`*.backup-hotfix9`). Before the overlay, every target held 95cf25c's bytes:
- Local: the Andy home, the default home and the global npm package ([evidence/install-local.txt](/docs/journal/2026/11/20/fixes/p0-reacquire-completion/evidence/install-local.txt)).
- Celly: elevated overlay ([evidence/install-celly.txt](/docs/journal/2026/11/20/fixes/p0-reacquire-completion/evidence/install-celly.txt)).
- KOBE: overlay over ssh.

All verified. No process was restarted.
- The mod part (`register.js`) applies when a session hot-reloads or restarts ciel-work.
- The router part applies at each router's next restart.

# Published

- Beta merge 0cb598b has the same tree as b993e7b (e3c613d6…). Publish run 44783113201 succeeded.
- npm `@one-ciel-ai/ciel-runtime@0.2.51-beta.20261120-172521.0cb598b`, gitHead 0cb598bd5cff….
- The two changed files have the same hashes in the published tarball as in the installs (register.js A3A33BE7…, work_parallel_mcp.py 65F0B581…).
