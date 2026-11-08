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

# Beta 6933b8d release and local install

- CI 42454579415 succeeded in every group.
- Publish 42454579337 succeeded.
- npm `beta` = `0.2.51-beta.20261108-063433.6933b8d`; `latest` 0.2.51 and `nightly` are unchanged.

Registry check ([report](evidence/installed-beta-registry-6933b8d.json)): integrity, gitHead `6933b8d…f4f1` and all 509 source files verified.

Local install:
- The verified tarball was extracted to a new immutable snapshot (563 files) and installed with `npm install -g`.
- User `CIEL_RUNTIME_HOME` now points to that snapshot.
- Local live sessions run from older snapshots and were not restarted. Routers 9505/9489/9524 returned 200.

[Installed smoke](evidence/installed-beta-smoke-6933b8d.json):
- 563 package files match.
- 5 isolated commands pass.
- `claude plugin validate` passes.

The keyboard + SSE console probe was rerun on the installed snapshot ([result](evidence/installed-keyboard/result.json)):
- Keys behaved the same: `j`→n2, `j`→n3, `k`→n2, `s`→SSE, `w`→n2, Tab Tab Enter→Settings.
- The strip now draws on two rows with no blank row between them ([frame](evidence/installed-keyboard/02-dashboard-before-sse.png)).
- The P1 fork was shown ([frame](evidence/installed-keyboard/03-frame-05.png)) and answered ([frame](evidence/installed-keyboard/03-frame-21.png)).
- No `.tmp` was left, and the scratch config was removed.

# Betty u42 / Prody u41

## How they launch

A read-only check covered process command lines and user environment:
- Both run `npm-bin/ciel-runtime.js` from their sandbox npm prefix (`C:\ProgramData\aap-win\sandboxes\u4X\home\<agent>\.npm-global`). That launcher runs the package it sits in.
- Neither user has `CIEL_RUNTIME_HOME` set.
- So only that package reaches their sessions. No remote install or restart was done from here.

## Betty u42 after its own relaunch

All times are Betty's clock, 2026-11-07.

| Item | Value |
| --- | --- |
| Package in Betty's prefix | `0.2.51-beta.20261108-063433.6933b8d` |
| Package written | 22:39:45, about five minutes after Publish |
| Router 9564 started | 22:43:10 |

How the package got there was not determined. This job ran no install or restart on .12.

Betty's router log, from that start to 22:46:50:

| Event | Count |
| --- | --- |
| `FileNotFoundError` | 0 |
| `channel_side_intake_deferred` | 0 |
| `channel_side_intake_accepted` | 1 |
| `anthropic_sse_upstream_error_event` | 0 |

Upstream requests in the same window:
- `_Walkie_Haiku_Terra`: 7 requests of about 364k tokens. That size matches the side fork's full context.
- `_Walkie_Sol61-Opus55-Fast`: 2 requests, the main session.

## "Combo has no executable targets" (user screenshot)

Before the relaunch, Betty's router sent six side requests to `model=_API_KEYS-SSE-1` (22:40:40–22:42:03, about 364k tokens each). Each one ended in `anthropic_sse_upstream_error_event` with no output.

This matches the gateway answer recorded on 2026-11-07: the `API KEYS - SSE` combo returns `404 Combo has no executable targets` (`poolSize 0`). See [side-main-connection](../../../07/fixes/side-main-connection/fix.md).

After the relaunch, no request names that combo. Why the side model differs before and after has not been determined: no ciel-runtime config file on u42 names either model.

## Prody u41

- Still runs `2df49b7` (package written 21:44:11).
- Its router log holds 5,665 `FileNotFoundError` and 2,522 deferred events, and has not been written since 21:46:21.
- Router 9592 returns 200.
- Prody gets the fix only when its prefix holds 6933b8d or later and it relaunches.

## Cleanup

The owned probe folder `C:\Windows\Temp\ciel-obs-probe` was removed (722 MB, 2,102 files). No probe process or 9611 listener was left.
