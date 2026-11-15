---
type: implementation-record
title: Walkie P0 (emergency) through the side actor opens a fork at once
description: Why a Walkie P0 event was queued as an ordinary P1 with no fork, and the side-path change that registers it as P0 and launches it through the P0 lane.
timestamp: 2026-11-15T09:30:00Z
tags: [work, side-intake, sse, p0, parallel, walkie]
---

# Measured failure (2026-11-15, G:\ciel-Walkie, router 9505)

A person set Walkie thread #131698 to P0. The side actor answered with an ACK for item `n1937` at P1, and no fork opened.

Router receipts for that thread show two arrivals:

| Arrival | `message_context.thread_task` | Indexed as |
| --- | --- | --- |
| First | `{priority: 3, emergency: false}` | P3 |
| After the P0 change | `{priority: 1, emergency: true}` | P1 |

So Walkie encodes P0 as `priority: 1` plus `emergency: true`.

Every stage of the side path knew only P1–P5:

- `side_intake_inbox.source_priority`/`_priority` ignored `emergency` and accepted 1–5.
- The claim ranking looped over P1..P5.
- The `work_side_resolve` schema and `side_work_decision.validate` refused `P0`.
- The side actor prompt said the hint is "P1..P5" and that an urgent problem is "create with priority P1".

A P1 goal waits for the ordinary pump policy (pause, capacity, approval). Only an effective-P0 root takes the pump's P0 lane.

# Change

- **Source priority:** `emergency: true` (on `data` or on `thread_task`) is `P0`. An explicit `P0` or `0` is also P0. P0 has its own inbox index directory and is claimed before P1, in the urgent lane that a reserved side slot takes.
- **Side decision:** `priority: "P0"` is accepted on create, update and prioritize.
  - The board stores canonical `P1`, the same encoding every other P0 entry point uses.
  - The result carries `priority: "P0"` and `p0_root_id`.
  - Main gets no handoff for it, because a fork runs it. A recorded wait or a time limit still reaches main.
- **P0 activation:** right after the board commit, `side_intake_mcp._activate_side_p0` runs `mutate_existing_priority(..., 'P0', set_by='side_decision')`.
  - This is the existing-root activation that REST, web, ctl and the Work tools use.
  - The operation id is `side-p0:<receipt>`, so a replayed resolve rolls the same activation forward and never adds a second one.
  - A failure is returned in `result.p0.error`. It never undoes the committed board.
- **Fork launch:** register.js already pumps after every side turn (`parallelPump($, 'side-turn.complete')`, right after `completeSide`). Its P0 lane reserves the activated root past pause, disable, approval and capacity. No new trigger was needed.
  - A setter that passed `$` into a variable-bound function was tried first. The engine's module check refused it at load (`$ is passed to "p0Pump"…`), so it was removed.
- **Side actor prompt:** a P0 `source_priority` means `priority P0` on the create, update or prioritize, and the hint is `P0..P5`.
- **Person-visible:**
  - The main transcript note reads `Side P0 queued <id> · <title> · fork starting`, or `· P0 not activated: <error>`.
  - The SSE pane shows `P0` for an emergency receipt and keeps P0 under the urgent (P1) filter.

# Evidence

All runs used isolated config: every `CIEL_RUNTIME_*` unset, a temporary `CIEL_RUNTIME_CONFIG_DIR`, and `CIEL_RUNTIME_TEST_ISOLATED=1`.

- `tests/test_side_intake_mcp.py`:
  - An emergency is `P0`, and the urgent-only claim takes it before a P1.
  - A P0 create gives canonical P1 plus effective P0 and no main handoff. A replay leaves one root, and `p0_reserve_next` reserves that root.
  - A P0 prioritize activates an existing goal.
- `tests/test_side_intake_mod.py` with `side_p0_harness.mjs`:
  - The side actor receives `source_priority: P0` and the P0 instruction, then resolves `P0`.
  - The person sees "fork starting", or the activation error.
- `claude plugin test` (ciel-work): 102 passed, 0 failed. This includes the new pane test, where a Walkie emergency shows as P0 under the urgent filter.
- Side, priority, P0 and wait test files: 341 passed, 2 skipped.
- Windows full groups, clean environment:

  | Group | Tests | Result |
  | --- | --- | --- |
  | unit | 4814 | OK (76 skipped) |
  | router | 1669 | OK |
  | channel | 626 | OK (80 skipped) |
  | runtime | 458 | OK (19 skipped) |

- Linux (Docker, node 24 + Python 3.11): the changed side test files, 142 passed.
- Release: beta `0.2.51-beta.20261115-092726.c23087e`.
  - The registry gitHead and integrity were verified.
  - Comparing the package with `git archive`: 619 of 621 files are identical; the two that differ are the version files.
  - It is installed locally as a new snapshot. The owned check below was repeated against the installed snapshot and passed.
- Owned check through the MCP boundary:
  - claim `source_priority` was P0;
  - resolve returned `{priority: P0, p0: {effective_priority: P0}, handoff: not_required}`;
  - `root_priorities` showed `Server down` stored P1 with effective P0;
  - `p0_reserve_next` returned `ready`, with the lease on that root.
