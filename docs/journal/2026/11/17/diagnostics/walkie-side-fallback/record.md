---
type: diagnosis
title: Walkie side events marked "failed" were never model failures; oversized threads skip the bounded fallback and stall the claim lane
description: In the G:\ciel-walkie session (install 71db761, 2026-11-17), every "P3 failed" row is a Walkie event whose thread (mostly thread 137008) exceeds the context main has left. Such an event never gets the bounded-thread fallback unless the mod compacted main in the last 10 minutes, so it runs 12 compaction waits and 6 counted attempts, is quarantined, and only then is forwarded to main (median 17.5 h after it arrived). Real model failures (turn_error) were retried on main's model today as designed. The retrying rows hold the single claim lane, and 194 newer events have never been claimed.
timestamp: 2026-11-18T01:40:00Z
tags: [side-intake, walkie, fallback, main-model, source-context, compaction, quarantine, claim-starvation]
---

# Question (user, 2026-11-17)

> 로컬 g:\ciel-walkie 세션에서 사이드액터가 모델 이유로 실패했는데 그럼 메인모델로 자동 시도되든가.혹은 메인으로 메시지 전송이되어야하는데 안되는거같은데.. 별도의 포크세션으로 조사해줘

In English: "In the local g:\ciel-walkie session the side actor failed for a model reason. It should then retry on the main model automatically, or the message should be sent to main. That does not seem to happen. Investigate in a separate fork session."

The screenshot (Observatory, SSE FLOW 16) shows most rows as `P3 failed [com.ciel.walkie.support…]`, with `FORKS 0 running · SIDE 0` and `No active side fork`.

# Scope and method

- Read-only investigation. No process was stopped and no state file was written.
- Session identified:
  - Workspace registry entry `aae7db361c7f` maps to `g:\ciel-walkie` (port 9505).
  - Launcher pid 31888 started 2026-11-17 17:28:51 local; claude.exe pid 55720 started 17:29:19.
  - The mods are loaded from install `ciel-runtime-71db7613…` (version `0.2.51-beta.20261117-225206.71db761`).
  - Main model: `_walkie_opus55-sol61-fast`.
- Side inbox read: `workspaces/aae7db361c7f/side-inboxes/4bfc8743…/` (`pending/`, `receipts/`, `sources/`).
- Main transcript: `G--ciel-walkie/feeac3ad-….jsonl` (1.24 GB, scanned line by line). Fork transcripts are under its `subagents/`.
- Local time is UTC−6. The transcript uses UTC; inbox times are printed in local time.
- Scripts and outputs are in [evidence/](evidence/). Local paths are redacted to `<home>`.

# Findings

## F1 ✅ The "failed" rows are not model failures

- `observatory.client.js:24-30` (`stage()`) prints `failed` for any row that carries a `side_failure` and was not answered. That includes rows still waiting to be retried.
- Pending rows with a failure in the last 24 h ([failure-waits.txt](evidence/failure-waits.txt)):

| category | reason | waits | attempts | state | rows |
|---|---|---|---|---|---|
| `source_context` | "Source thread exceeds main context budget" | 12 | 6 | quarantined | 22 (8 of them had already moved to main's model) |
| `source_context` | "Source persisted result is not this session's exact file" | 0 | 6 | quarantined | 13 (07:45–08:25 local, before the 17:29 restart) |
| `main_compaction_pending` | context budget | 1–9 | 0–2 | waiting | 8 |
| `invalid_answer` | work_side_resolve kind/action mismatch | – | 1 | quarantined | 1 |

- None of these is a model API error. A context-budget failure does not depend on the model. Row `058829ebfe18` was already on main's model (`parent_model_since` set) and still failed on the budget, then was quarantined.
- The receipt's `model` field always reads `inherit`. `side-intake.js` records `claimedActor.model='inherit'` for every native fork, so the pane's "model inherit" does not tell which model ran.
- The fork transcripts show the side forks actually ran `deepseek-v4.1-flash` and other models in the side slot (`work_parallel_models.cielairouter.side`).

## F2 ✅ Real model failures were retried on main's model today

- Source: [turn-errors.txt](evidence/turn-errors.txt), last 72 h.
- On 11-17, 46 `turn_error` receipts carry `use_parent_model=True`, and their `retry_not_before` equals `failed_at`, meaning they were retried at once. All of them were then processed (`delivery=submitted`, `not_applicable` or `failed` from the source reply).
- The code path:
  - `side_intake_mcp.py:295-303` passes `side_model=_side_model_configured(config)`.
  - `side_intake_processing.py:222` then sets `parent_retry`.
  - `ciel-work-gate` maps a ` main-model` description to main's model.
- 11-15 and 11-16 `turn_error` rows were not switched. Eight of those (11-15) are still quarantined in `pending/`. They predate the main-model wiring and are old-build residue.
- The fork errors behind today's turn errors ([fork-api-errors.txt](evidence/fork-api-errors.txt)):

| Error | Count |
|---|---|
| `max_output_tokens` (128000 exceeded) | 38 |
| "Prompt is too long" | 13 |
| 503 "all targets were skipped" | 3 |
| deepseek "Input exceeds context window" | 3 |
| `billing_error` | 2 |

## F3 ✅ An oversized thread never reaches the bounded fallback unless the mod itself compacted main within 10 minutes

The installed code (71db761) and the work tree at 71f5243 have the same logic:

- `side-intake.js:1679-1681` (work tree `:1708-1710`): for a non-urgent event, an over-budget thread is rethrown instead of being cut to `bound()` while `!compactionRecent(identity)`.
- `side-intake.js:566-569`: `compactionRecent` reads only the in-memory `compactions` map. Only the mod's own `compactMain` writes that map. Claude Code's auto-compactions are not counted.
- `side-intake.js:570-586`: `compactMain` does nothing while main is busy (`wanted:true`). It runs only at a main turn end (`sidePromptTurnComplete`, `:825-826`). A skipped compaction leaves `at` unchanged.
- `side-intake.js:1729-1734` records `main_compaction_pending`.
- `side_intake_processing.py:175,208` counts up to 12 such waits. After that the category becomes `source_context` and each failure counts.
- `side_intake_processing.py:232` quarantines at 6 counted failures. `:252` (`side_forward.mark`) then forwards the original to main.

The comment at `side-intake.js:240-244` says: "The caller compacts main first when that can make room; past that, it uses bound() instead of failing (2026-11-16: Walkie thread 63773 was quarantined after 6 tries)." The behavior does not match. Once the 12 waits are used up, nothing switches the event to `bound()`. It fails 6 more times and is quarantined.

Evidence:
- 22 rows today carry `compaction_waits=12`, `attempt_count=6` and `quarantined=True`, all with the main-context-budget reason. Most belong to Walkie thread 137008 ([threads.txt](evidence/threads.txt)).
- Main-transcript compactions on 11-17 ([main-compactions.txt](evidence/main-compactions.txt)):
  - Manual (the mod's `$.session.compact()`): 09:34, 10:35 and 17:31 local.
  - Auto (Claude Code): 07:47, 08:52, 12:17, 13:06, 14:25, 15:24 and 16:58.
  - Between 10:35 and 17:31 there was no manual compaction, so `compactionRecent` was false the whole time the 137008 events were being retried.
- The budget is `window − used − 20% reserve`. Right after an auto-compaction it was 305–323 KB, and the 137008 thread still did not fit, so compaction alone could not have admitted it.

## F4 ✅ Main does get the original, but only after the quarantine

- Every quarantined budget row has `main_forward=True` and `release_notice.state=appended`.
- For the 7 events sampled (quarantined between 16:33 and 18:48 local), the forward text "[Ciel side 처리 실패 · 원문 전달 · event … · source_context · 6회 시도]" is in the main transcript as a `plugin_note` attachment. See [forward-in-main.txt](evidence/forward-in-main.txt). There are 315 distinct forwarded events in the transcript.
- Time from arrival to forward for the 49 budget-quarantined rows still in `pending/` ([latency-backlog.txt](evidence/latency-backlog.txt)):

| Statistic | Minutes |
|---|---|
| Min | 27 |
| Median | 1050 (about 17.5 h) |
| Max | 5562 |

- Not verified: whether main acted on each forwarded note (replied to the source or queued Work). The forward is a `plugin_note`, delivered with main's next request.

## F5 ✅ / 🟡 The claim lane is held by retrying rows; newer events are not claimed

✅ Measured at 18:54 local ([latency-backlog.txt](evidence/latency-backlog.txt)):
- 194 pending rows have neither `side_failure` nor `processing`. They arrived between 15:27:16 and 18:53:40; 181 of them belong to thread 137008.
- In the last hour 13 claims were recorded, all on old rows (received 15:17–16:56), roughly one every 22 s. Each ended in `main_compaction_pending` or a quarantine.
- No side fork was spawned, which matches `SIDE 0`.
- The last processed receipt was at 18:31.
- No unclaimed row is held by a source-holder marker ([source-holders.txt](evidence/source-holders.txt), using the installed `source_identity`).

🟡 Inference, not verified from a claim log (the router log does not name MCP tools):
- `side_intake_processing.py:84,89` orders the candidates as `urgent → aged (by received_at) → ranked_rest → rows`.
- So an old row whose retry delay (30–60 s) has passed is picked before a never-claimed row, and each such claim spends its time on a full Walkie thread read before failing.
- The unclaimed 137008 events would take the same 12+6 path once claimed, because their thread is the same.

# Answers to the question

- **"Side failed for a model reason, so retry on main's model":** that part works.
  - Today 46 side model failures (`turn_error`) were retried immediately on main's model and then processed (F2).
  - The rows shown as `failed` are a different failure, the context budget. Main's model cannot fix it (F1).
- **"Or the message goes to main":** it does, but only after the quarantine (F4).
  - Each oversized-thread event first goes through 12 compaction waits and 6 counted failures, because the bounded-thread fallback is gated on a mod compaction in the last 10 minutes (F3).
  - Meanwhile those retries hold the claim lane and 194 newer events have not been claimed at all (F5).

# Defects (code against stated intent; not fixed here)

1. **Bounded fallback gate** (`side-intake.js:1679-1681`, `:566-569`). Fixed in the work tree; see "Fix" below.
   - Problem: once `main_compaction_pending` waits are used up, or Claude Code has auto-compacted, a non-urgent oversized thread still gets no `bound()`.
   - Intended behavior: the comment at `:240-244` says "past that, it uses bound() instead of failing".
   - Reproduction: main busy in back-to-back turns, no mod compaction for 10 minutes, a P2–P5 Walkie event whose thread is larger than `window − used − 20%`. Result: 12 waits, 6 `source_context` failures, quarantine.
2. **Pane label** (`observatory.client.js:28`). `failed` also covers rows that are still retrying (`main_compaction_pending`, attempts below 6). Fixed in the work tree at the coordinator's request; see "Fix" below.
3. **Receipt model field** (`side-intake.js` sets `claimedActor.model='inherit'`). The failure record cannot show which model failed. This is recorded as an observation.

# Fix (coordinator request, 2026-11-17; work tree, not committed)

> 조사에서 확인한 대형 스레드 축약 fallback 결함을 이제 수정해 주세요. … 화면 stage가 retryable failure를 곧바로 'failed'로 부르는 문제도 … 수정하고 테스트하세요.

In English: fix the bounded-thread fallback defect found above. Also make the screen stage stop calling a retryable failure "failed", and test it.

## Change 1: the bounded thread opens once the event's compaction waits are used up

`ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js`:

- New `MAIN_COMPACTION_WAITS = 12` in the mod. It mirrors `side_intake_processing.MAIN_COMPACTION_WAITS`, and a test compares the two.
- New `compactionWaitCounts` map, filled by `noteCompactionWaits`.
  - It keeps the durable `side_failure.compaction_waits` that the router returns in every `work_side_fail` receipt.
  - `recordFailure` notes it before displaying the receipt.
  - The map is bounded to 1000 entries.
- The prefetch catch now rethrows an over-budget error only when all of the following are true:
  - the event is not urgent;
  - this module has not compacted main within the cooldown;
  - **and** `!compactionWaitsSpent(identity, receipt_id)`.
- Once the router has reported 12 spent waits, the next claim gets `bound()`, which contains the root, the target and the newest replies that fit.

Behavior that did not change:
- A new event still waits for main's compaction first.
- P0/P1 still gets the bounded thread at once.
- A thread that fits is still read whole.
- The router's wait count and its quarantine rule (`side_intake_processing.py`) are untouched.
- No condition depends on Claude Code's own auto-compaction.

Known limit (measured, `waits_spent_reloaded`): the count lives in module memory. A module that was reloaded mid-wait has not seen it yet. Its first claim of such an event is therefore one counted `source_context` failure; that receipt reports `compaction_waits=12`, and the next claim opens the bounded thread.

## Change 2: the terminal Client shows the real state of a failed row

- `register.js` `sideFailureState`: the graphical receipt row now carries `failure`, which is one of:

| `failure` | When |
|---|---|
| `quarantined` | the router's quarantine; terminal |
| `waiting` | `main_compaction_pending` |
| `retrying` | any other recorded failure the router will retry |

  This replaces the old boolean `failed`.
- `observatory.client.js` `stage()`:
  - only `quarantined` reads `failed`;
  - `waiting` and `retrying` are shown as such (waiting in blue, retrying in yellow);
  - answered, work and processing rows keep their precedence.

## Tests

The fail-before tree is `git archive HEAD` (71f5243) with the new tests copied in.

| Test | Before | After |
|---|---|---|
| `test_side_intake_mod.test_spent_compaction_waits_start_the_fork_on_the_bounded_thread` | FAIL: `recorded` = mcp, source_context; 0 spawns | pass: 1 bounded spawn, 0 counted failures |
| `test_side_intake_mod.test_the_mod_wait_limit_matches_the_router` | FAIL: no constant | pass |
| `test_side_intake_mcp.test_the_failure_receipt_reports_the_spent_compaction_waits` (real `fail()`) | pass: router unchanged | pass |
| `test_observatory_stage` (2 tests, widths 100 and 40) | FAIL: retry row shown as `received` | pass |
| `observatory.test.ts` "calls only a quarantined row failed…" (real engine) | FAIL: "▸P3 failed Retry row" | pass |

Conditions fixed in `tests/side_persisted_harness.mjs` (sequences):
- a long thread (60 replies);
- main busy on every claim;
- one mod compaction, then the clock moved 11 minutes;
- P3;
- partial room, so a bounded thread exists;
- `waits` starting at 11.

Paths preserved, each with its own case: immediate compaction (`context_budget`), urgent (`context_partial_p0`, `urgent_no_wait`), normal size (`context_room`, `fits_after_waits`), and a fresh event that waits (`fresh_event`).

Evidence files:
- [bound-fallback-sequences.txt](evidence/bound-fallback-sequences.txt)
- [tests-fail-before.txt](evidence/tests-fail-before.txt)
- [tests-pass-after.txt](evidence/tests-pass-after.txt)

Runs:
- `claude plugin test ciel_runtime_support/claude_mods/ciel-work`: 104 pass, 0 fail. `work.test.ts` keeps main's 2.1.327 engine-ref expectation.
- Python, isolated: `test_observatory_stage`, `test_side_intake_mod`, `test_side_intake_mcp` and `test_work_observatory` together ran 112 tests with 0 failures.
- Python, all 18 test files that load the Work mod: 610 run, 0 failures.

# Remaining uncertainty

- Not verified from a log: whether the claim cadence (~22 s) comes from the poll timer or from the thread read time.
- Not measured: whether main replied to each forwarded original.
- Not proven: why the 18:08–18:09 `main_compaction_pending` rows were processed with no compaction after 17:31. Possible explanations are that they were P1 (urgent → bounded) or that their threads fit at that moment.
- The "persisted result is not this session's exact file" quarantines (13 rows, 07:45–08:25) are a separate source check. The journal `fixes/side-persisted-result-name` covers that area; they were not re-examined here.
- The Walkie session still runs 71db761. The machine also has 71f5243 installed (Stage 2), but F3's logic is unchanged there and in the work tree.
