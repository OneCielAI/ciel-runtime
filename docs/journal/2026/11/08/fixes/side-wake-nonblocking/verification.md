---
type: verification
title: Owned real console, main busy 420 s - fixed build answers at once, 83b4427 waits for main
description: Same probe on the fixed source snapshot and the installed 83b4427 snapshot. Main ran one 420-second foreground turn; an approval created a pending wake; a status question sent while the wake waited was answered 23 s after send on the fixed build, and only after main's turn ended on 83b4427.
tags: [side-intake, sse, handoff, verification, real-console]
timestamp: 2026-11-09
---

# Probe

The probe is `.local/owned_busy_wake_probe.py` (not committed). It is an owned, isolated launch:
- a new workspace and a copied config with the receiver and remote sections removed;
- a free router port, with inherited `CIEL_RUNTIME_*`, `CLAUDE*`, `ANTHROPIC*`, `CODEX*` and `OPENAI*` variables stripped;
- a loopback SSE source feeding the production receiver → side inbox → side actor path;
- side model `_Walkie_Haiku_Terra`, `context_mode isolated`.

Steps:
1. Main is asked to run `py -3 -c "import time; time.sleep(420)"` in the foreground, which keeps it in one turn.
2. While main is busy, an approval for the seeded item waiting on `Daniel approval 118269` is sent. The side creates a handoff whose wake prompt can enter only after main's turn ends.
3. A status question is sent while that wake is still `submitting`.
4. Main's turn ends, the wake enters, and main acknowledges the handoff.

The first attempt asked main to run `sleep 300`. Claude Code moved it to the background and ended the turn, so main was not busy. That run is not used as evidence.

# Results

| | Fixed snapshot (`probe-wake-nonblocking`) | Installed `83b4427` |
| --- | --- | --- |
| Main turn | Bash 02:21:27 → `SLEEP DONE` 02:28:40 | Bash 02:24:37 → `SLEEP DONE` 02:31:46 |
| Approval → handoff | processed; wake `submitting` | processed; wake `submitting` |
| Status question processed | **yes, 23.1 s after send** (02:22:05), while main was still in its sleep turn | **not within 200 s**. Its receipt is at 02:32:01, 15 s after main's turn ended |
| Wake when the status was answered | `submitting`; not yet entered main | `queued` only after main ended |
| Wake prompts in main | 1, entered 02:28:41 | 1, entered 02:31:46 |
| Main `work_side_handoff` | succeeded; handoff `answered`, wake `acknowledged` | refused twice with `conversation identity is unavailable` after a hidden version conflict (5 vs 4, debug log: `tool.call hook skipped`); handoff stayed `requested` |

Screenshots (title bar cropped):

- Fixed build, `evidence/fixed/`:
  - `02-main-busy.png`: main in the sleep turn.
  - `04-wake-pending.png`: the wake prompt queued under the running turn.
  - `06-status-answered-main-busy.png`: the side pane shows the status receipt (1/2, processed, answer) while main shows `Waiting 420 seconds … 39s`.
  - `07-main-after-wake.png`: main after it acted on the wake.
- `83b4427`, `evidence/old-83b4427/`:
  - `06-status-answered-main-busy.png`: at the same point (3 m 43 s into the sleep) the pane lists only the approval receipt (1/2). The status question is not there.

Each folder's `result.json` holds the probe result. Local paths are redacted.

# Automated checks (working tree)

- `tests.test_side_intake_mod`: 8 OK, including `test_pending_main_wake_never_holds_side_claims`.
- `tests.test_work_mod_write_gate -k side`: 7 OK, including `test_side_handoff_conflict_reaches_main_instead_of_identity_error`.
- **Each regression test also run against HEAD's file:**
  - old `side-intake.js`: the first poll is still blocked after 2 s and the event is never claimed;
  - old `register.js`: the harness throws `Parallel read failed: the plan changed …`.
- CI groups, local, with `CIEL_RUNTIME_*` unset:
  - unit: 3449 OK (50 skipped);
  - router: 1544 OK;
  - channel: 607 OK (80 skipped);
  - runtime: 458 OK (19 skipped).
- `ruff 0.15.22` over the repository, excluding `.local` and `docs`: passed.
- Live routers before the probes: 9505=41824, 9524=41224, 9489 not running. The probes stopped only their own process trees (`owned_router_pids` empty after kill).
