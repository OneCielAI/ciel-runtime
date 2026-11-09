---
type: fix
title: Side wake prompts settle in the background; handoff errors reach main
description: pollSide no longer awaits $.prompt.submit. At most one wake per conversation is in flight, and claims continue while it waits for main. The work_side_handoff intercept now answers with the router's error instead of throwing.
tags: [side-intake, sse, handoff, prompt-submit, work-mod]
timestamp: 2026-11-08
---

# Changes

## `ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js`

- The `main_wakes` loop still records `prepare` with the router. It then starts `submitWake(...)` without awaiting it and moves on to the claim.
- `submitWake` awaits `$.prompt.submit`, records the result and clears the in-flight mark:
  - `queued` when the prompt entered;
  - `failed` on `drop`;
  - `uncertain` when submit threw.
- `wakesInFlight` (identity → event_id) allows **one** wake prompt per conversation until it enters. Further wakes stay `pending` in the durable ledger, and a later claim offers them again. Only one plugin prompt is ever queued behind main's turn.
- The ledger contract (`side_wake.py`) is unchanged:
  - `prepare` → `submitting` with an attempt id;
  - `record` accepts only that attempt;
  - `main_wakes` offers only `pending`/`failed`.

## `ciel_runtime_support/claude_mods/ciel-work/hooks/register.js`

- The engine-origin `work_side_handoff` intercept now catches the router error and returns `{deny}` with its text, for example `work_side_handoff: the plan changed (stored version 1688, expected 1685); call work_status and retry`.
- Before, the throw skipped the hook, and the engine's own call lacked `session_id`.

# Tests

| Test | Proves |
| --- | --- |
| `tests/side_wake_harness.mjs` + `test_side_intake_mod.test_pending_main_wake_never_holds_side_claims` | With `prompt.submit` pending, both polls return. A new event is claimed and spawned. Only one wake prompt is submitted. After it enters it is recorded `queued`, and the next wake is prepared. The old code fails: the first poll stays blocked and the event is never claimed. |
| `work_mod_harness.mjs` `side-handoff-conflict` + `test_work_mod_write_gate.test_side_handoff_conflict_reaches_main_instead_of_identity_error` | A router conflict returns `{deny: 'work_side_handoff: the plan changed …'}`, with `session_id` sent. The old `register.js` throws `Parallel read failed: …`. |
