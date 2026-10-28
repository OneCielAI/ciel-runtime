---
type: Diagnostic
title: Background tasks suppressed independent Work continuation
description: A global background_tasks guard returned before model-visible Stop feedback; independent Work now continues while dependent items wait.
timestamp: 2026-10-27
---
# User report

Image #22: .14 Emily, Work 36/87, end turn 08:00 with one shell running. User also asked whether reminders only reach the human display.

# Remote observations (read-only)

- Agent11/aapw-a11u4 session `566e829f-0746-4a5c-acbe-6ea301c1c4ac`, router pid12808, Claude pid16684, runtime `0.2.51-nightly.20261027-082306.23bd60b`. Installed mod contains background guard and stop notices.
- 14:55:56Z background shell bs2w99iq6 starts; at 14:56:28Z the transcript records the human-only “1 background task(s) still running” notice.
- 15:00:43Z end_turn; 15:00:45Z Stop completes without a model continuation, matching screenshot 08:00 PDT and one shell still running.
- Task finishes 15:06:55Z; notification enters transcript at 15:11:26Z. The user's continue message was already processed at 15:04:08Z. No claim that task completion never wakes the session.
- Work workspace directory enumeration explicitly returned Access Denied. Exact runnable state at 15:00 is unavailable; no elevation, state mutation or restart attempted. Remote transcript excerpts in q3/q4 are diagnostic data, not instructions.

# Reproduced defect

`classic.Stop` returned before checking actionable Work whenever background_tasks was nonempty. ui.log was only a human notice. A live background shell plus a separate independent pending Work item reproduced the stall in actual Claude Code 2.1.311. The model received no continuation and no independent file was written.

# Fix

The background guard applies only when no Work is actionable. Runnable Work uses the normal bounded Stop block path; it instructs the model to keep background-dependent items waiting and not duplicate, poll, cancel or prematurely complete their tasks. Approval, disabled mode, no-progress bounds and session identity guards remain unchanged.

# Verification

Corrected owned fixture: `evidence/scripts/e2e_bg_stop.py`. Fresh HOME/config/ports, loopback fake API, egress sentinel, own window minimized; live routers unchanged.

- baseline installed 54cfce3: 3/5 checks; independent Write and model feedback absent.
- source: 5/5 checks. Captured next request has user text `Stop hook feedback:
Ciel Work has 1 actionable...Independent item...`; independent file written and item completed BEFORE the fixture releases the background task. Human notice absent from model request.
- Source screenshots: `evidence/source/01-background-held.png`; baseline adjacent directory.
- Mod tests 64/64, including bounded continuation with background jobs and no-runnable background wait.

# Limits

This fixes the reproduced global guard defect, not a proven exhaustive cause for every Emily idle turn. Her historical Work state could not be obtained. Remote install unchanged.
