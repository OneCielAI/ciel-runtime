---
type: fix-record
title: Side model failure → retry on main's model was never wired; now the next attempt runs on main's model
description: The router had a parent-model retry flag, but it was set only for model_not_found/invalid_model categories the host never sends, and nothing read it — the gate always gave side forks the side slot model. An API error that ends a side fork's turn now makes the next attempt run at once on main's model.
timestamp: 2026-11-16T23:00:00Z
tags: [side-intake, side-model, fork-model, ciel-work-gate, retry, celly, walkie]
---

# Question (user, 2026-11-16)

> 아 그전에 중요한 문제... sse 사이드 액터가 모델문제로 실패하면...메인과 동일한 모델로 재처리하게 되어있는 로직이 동작을 안하는거아냐?

# Measured

**Walkie, last 3 days** ([evidence/walkie-parent-model-census.txt](evidence/walkie-parent-model-census.txt)).
- 154 events had a side failure, 39 of them `turn_error`.
- None of them has `use_parent_model: true`.
- Celly's 152 pre-dispatch 503 failures (same day) were retried on the same side model each time; see [../../diagnostics/celly-side-predispatch/record.md](../../diagnostics/celly-side-predispatch/record.md).

**Code at `aceb3aa`.**
1. **The flag was set by categories the host never sends.** `side_intake_processing.fail` set `use_parent_model` only for `model_not_found` / `invalid_model`, or a router outcome with `no_executable_targets`. The host sends `turn_error` for every side fork whose turn ended on an API error (`side-intake.js completeSide`).
2. **No router outcome was ever recorded.** `side_request_outcome.record` has no caller; only `take` is called ([evidence/outcome-record-callers.txt](evidence/outcome-record-callers.txt)).
3. **The flag was read by nothing.**
   - `claim` returns `use_parent_model`, but nothing reads `event.use_parent_model`.
   - The ciel-work-gate `turn.step` gives every side fork the side slot model (`slotNames(state, 'side')`).
4. **When the logic was written.** It dates from the separate side-model build (11-07), whose side model was retired on 11-11 (34966b6). The side model came back on 11-14 through the gate, and this retry was not reconnected.

# Fix

1. **When the next attempt switches** (`side_intake_processing.fail`). It switches to main's model at once, with no backoff, in two cases:
   - the side fork's turn ended on an API error (`turn_error`) while a side model is configured;
   - the model is unavailable.

   The router tool passes `side_model` from the work_fork_models projection. An unreadable setting counts as configured.
2. **Retries stay on main's model.** `use_parent_model` is sticky: later retries of that event stay on main's model, with normal backoff.
   - If main's model is unavailable too, the event is quarantined, and its original then goes to main ([../side-failed-forward/record.md](../side-failed-forward/record.md)).
   - With no side model configured, the side fork already runs on main's model, so nothing switches.
3. **The host marks the retry.** For a claim with `use_parent_model`, it spawns the fork described `Side SSE <id> main-model`.
4. **The gate applies main's model.** The gate's `SIDE_DESCRIPTION` accepts that suffix (still a side fork for every rule). `forkModel` returns main's model for it: the main model setting when one is chosen, otherwise null, which keeps the inherited engine model, main's own.
5. **The Side pane** shows "side model failed → retrying on main model" and "quarantined; original forwarded to main".

# Verification

- **Fail-before.** Run against HEAD (`aceb3aa`) ([evidence/fail-before.txt](evidence/fail-before.txt)). The three router tests error because the setting probe they patch does not exist there. The gate scenario and the host scenario fail.
- **Pass-after on Windows.** Side MCP, fork gate, Work mod harness, side forward and side mod tests: 274/274.
- **Remaining suites.** The side suites and `claude plugin test ciel-work` are listed in [evidence/regression.txt](evidence/regression.txt).
