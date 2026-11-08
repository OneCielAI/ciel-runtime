---
type: verification
title: MAX_PATH side-intake fix and Observatory keyboard verification
description: Regression tests, a real legacy-path run on Betty .12 (old fails, fixed passes), and an owned real-console run driving the Observatory by keyboard alone while a P1 SSE fork is processed and answered.
tags: [verification, sse, side-intake, max-path, observatory, keyboard]
timestamp: 2026-11-08
---

# Regression tests

The new module `tests/test_legacy_max_path.py` simulates the legacy limit: any `Path.open` above 259 characters raises FileNotFoundError. It rebuilds the AAP depth, a 166-character marker directory where the old temp path is exactly 262 characters, and runs three tests:

| Test | What it checks |
| --- | --- |
| `test_marker_shape_saves_with_temp_under_the_limit` | The old temp path is 262 characters and fails. The new temp is shorter than the target, in the same directory, and no `.tmp` is left behind. |
| `test_p3_event_is_received_claimed_and_finished_at_aap_depth` | A P3 event goes through receive, then a duplicate re-index, then claim (P3), then finish (processed). Pending and marker are cleared and no `.tmp` is left. |
| `test_p1_reserved_claim_works_at_aap_depth` | The reserved P1 claim picks the P1 event. |

Results:
- [Fixed code: pass](evidence/focused-tests.txt). The run also covers 73 durable, side-intake, side-decision, mailbox, MCP, mod and observatory tests.
- [HEAD 2df49b7: all three fail](evidence/old-code-regression-tests.txt), each with the production `FileNotFoundError` at a `.<64-hex>.<pid>.<time_ns>.tmp` path.
- ruff 0.15.22 (the CI pin) passes on the changed files.

`claude plugin validate` passes. In `claude plugin test`:
- `observatory.test.ts` passes 5/5, including the new strip test. That test checks the Buttons, their hotkeys and `autoFocus`, and presses Next/Prev/SSE/Work/P1 without handing any key to the Client.
- The 77 failures are all in `work.test.ts` and are the pre-existing baseline.

# Real legacy path limit on Betty .12

[Probe](evidence/maxpath_real_probe.py), [runner](evidence/betty_maxpath_run.ps1), [output](evidence/betty-real-maxpath.txt).

Setup:
- Betty reports `LongPathsEnabled=0`.
- Everything ran under the owned `C:\Windows\Temp\ciel-obs-probe\maxpath`.
- The state directory was padded to 73 characters, so the marker directory is 166 characters, the same as the live u41/u42 paths.

| Run | Result |
| --- | --- |
| Old: 0a881e0 snapshot, the same `durable_json_file` as the live builds | `receive` raised `FileNotFoundError [Errno 2]` on the `priority\P3\d8\.<64>.22948.<ns>.tmp` temp, length **262**. The pending record had already been written. |
| Fixed | receive OK, claim OK (P3), finish OK (`processed`). Marker length 236. Marker removed after finish. No `.tmp` left. |

No live process, router, install or user profile on Betty was touched.

# Real console: keyboard only, then a P1 SSE fork

[Probe](evidence/owned-keyboard-sse-probe.py), [result](evidence/keyboard/result.json).

Setup:
- Owned conhost and an isolated copied config.
- Router on a free port.
- The installed 0a881e0 snapshot with the three changed files overlaid. Its code is the same as HEAD 2df49b7.

After `/work-mode view` the probe never clicked.

| Key | Frame | Observed |
| --- | --- | --- |
| (open) | [02](evidence/keyboard/02-dashboard-before-sse.png) | The strip shows `w: [Work] s: SSE g: Settings k: ▲Prev j: ▼Next / i: Inspect p: P1 f: Focus r: Refresh q: Close`. The ring is on Work. The inspector shows `WORK n1`. |
| `j` | [01](evidence/keyboard/02k-01-j.png) | Inspector `WORK n2` |
| `j` | [02](evidence/keyboard/02k-02-j.png) | Inspector `WORK n3`. The ▸ cursor is on "Render Work hierarchy". |
| `k` | [03](evidence/keyboard/02k-03-k.png) | Inspector `WORK n2` |
| `s` | [04](evidence/keyboard/02k-04-s.png) | Inspector `SSE none` |
| `w` | [05](evidence/keyboard/02k-05-w.png) | Inspector `WORK n2` (the cursor kept its place) |
| Tab, Tab | [07](evidence/keyboard/02k-07-tab.png) | The ring moved to `g: Settings` |
| Enter | [08](evidence/keyboard/02k-08-enter.png) | The Settings tab opened |

The composer stayed empty throughout, so no key leaked into the prompt.

The probe then pressed `w` and served one P1 CloudEvent. The SSE column went through these states:

| Frame | SSE column |
| --- | --- |
| [01](evidence/keyboard/03-frame-01.png) | `SSE FLOW 0` · No active side fork |
| [02](evidence/keyboard/03-frame-02.png) | `ACTIVE SSE FORKS 1` · ▶ fork · `P1 processing` (the Claude Code task list shows `fork Side SSE fd5f7411488b`) |
| [20](evidence/keyboard/03-frame-20.png) | No active side fork · `P1 pending` |
| [21](evidence/keyboard/03-frame-21.png) | `ANS 1` · `P1 answered` |

The inbox files persisted: pending, then receipts. No `.tmp` was left. The router log, at level ERROR, has nothing after its startup lines.

Cleanup:
- The owned tree and router ended and the copied config was removed.
- Live routers 9505/9489/9524 returned 200 before and after the run.
