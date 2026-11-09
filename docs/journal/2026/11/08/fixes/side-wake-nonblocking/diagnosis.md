---
type: diagnosis
title: One pending main wake stopped every Prody side claim for 63 minutes
description: Beta 1581aa5 awaited $.prompt.submit for a side-to-main wake inside the side claim loop. A plugin prompt enters only when main is idle, so during one 70-minute main turn no SSE event was claimed, acknowledged or answered. A second defect hid main's handoff version conflict behind an identity error.
tags: [side-intake, sse, handoff, prody, prompt-submit]
timestamp: 2026-11-08
---

# Report

The user reported that Walkie message 118635 reached Prody, but the side actor neither read nor answered it, and it was not queued.

# Evidence (Prody, read-only, 2026-11-09 UTC)

Sources:
- the side inbox (`side-inboxes/<session>/pending`, `receipts`);
- the canonical session board (`work-sessions/<sha256(claude:543686ba-…)>/work-plan.json`, reached via `work-lineage.json`);
- the main transcript `ef61c539-….jsonl`.

All were read with backup semantics as SYSTEM. The workspace directory grants only its owner. Nothing on Prody was written.

| Time (UTC) | Observed |
| --- | --- |
| 00:47:46 | Main turn starts (user `계속`). It runs until `end_turn` at 01:57:30 (70 min). |
| 00:52:44 → 00:54:08 | Side claims and processes 118606 (GitHub): `update n221`, `main_handoff.kind = p1`. |
| 00:54:03 | Handoff `19e9b528…` requested. |
| 00:54:10 | Its wake goes to `submitting` (`wake.started_at`). |
| 01:04:56 – 01:57:04 | 10 events arrive and stay `stage: received` with no `processing` lease. They include 118622, a pending reminder, 118631, **118635 (JUN KIM, 01:31:29)**, 118641, two Walkie restart notices, 118651, 118655 and a renotify. |
| 00:54 – 01:57 | No receipt is written. The newest receipt stays 00:54:08 and `decision-cursor.json` stays 00:54:04. |
| 01:52:32 | Main UI notice: `Cannot open agents — 1 queued command would be lost.` |
| 01:57:32.953 | The wake prompt `The ciel-work plugin sent a message: Ciel side event requires main action …` enters main, 2.9 s after `end_turn`. |
| 01:57:34 | Wake recorded `queued` (`wake.updated_at`), 63 min 24 s after `submitting`. |
| 01:57:49 | Claims resume. 118655 is claimed first (P1). The side's decision reply says it answered in Walkie 118658. |
| 02:01:52 → 02:01:55 | The next P1 wake (n219) enters 3 s after main's next `end_turn` at 02:01:51.9. |
| 02:02:20 | A third wake is `submitting` while main runs again. At the 02:02:49 read, 118635 was still `received`. |

# Cause 1: the claim loop awaited the wake prompt (established)

`side-intake.js` `pollSide` (beta `1581aa5`, `83b4427`):
- sets `busy = true`, calls `work_side_claim`, then for each `main_wakes` entry awaits `$.prompt.submit({text})`;
- clears `busy` only in `finally`.

Every other poll returns at `if (busy …) return`, so no `work_side_claim` runs.

The Claude Code plugin API (`claude-code.d.ts`, 2.1.310):
- `$.prompt.submit` is "a turn of its own, once the session is idle";
- `PromptSubmitArgs.wait` "is false, as a plugin's prompt runs once idle";
- the result resolves "once the prompt entered the session".

Prody's timestamps match this: each wake entered within 3 s after a main `end_turn`.

Owned reproduction: `tests/side_wake_harness.mjs`. Its `prompt.submit` stays pending as if main were busy.
- Old `side-intake.js`: the first poll is still blocked after 2 s, and a following event is never claimed (`claims 1`, `spawns []`).
- New code: both polls return and the event is claimed.

# Cause 2: main's handoff conflict surfaced as an identity error (established)

At 01:59:02 main called `work_side_handoff` with `expected_version 1685`. The board was at 1688.
- The mod's `tool.call` intercept (`register.js`) calls the router itself. On the router error it threw `Parallel read failed: the plan changed (stored version 1688, expected 1685); call work_status and retry`.
- The engine skipped the thrown hook (main UI notice: `tool.call hook skipped`). It then ran the call itself, without `session_id`.
- Main received `conversation identity is unavailable; no shared Work fallback is allowed`. Handoff `19e9b528…` remained `requested`.

# Not established

- The 01:57:32 notice `Ciel Work auto continue could not read the Work status` was not investigated.
- Side events used about 690k input tokens each (`context_mode full`, 100 %). This was measured but not changed here.
