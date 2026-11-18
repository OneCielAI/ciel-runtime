---
type: fix
title: Andy side-actor failures — no thread prefetch, batched gate tick, claim order, note field, answer shape
description: On Andy's workspace 335 side events failed in four days and 408 new events waited unclaimed. The side claim no longer reads the source thread (the fork decides from the event and main's inherited transcript; a cut or textless event reads its one target message). The gate re-check is one batch per tick reading only each gate thread's messages after a cursor. Retrying rows take their own claim lane alternated with new events after P0 and P1. A side note on an executing goal goes to progress so the approval digest stays the same. Over-long text is cut with a marker, and a shape refusal gets one corrective turn. The 13 persisted-file refusals were measured as the pre-ab6edbe name rule.
timestamp: 2026-11-19T01:30:00Z
tags: [side-intake, walkie, sse, gate-check, claim, quarantine, andy, work, hotfix]
---

# Problem (lead brief, Andy, last 4 days)

335 failed side events, 408 events waiting unclaimed since 04:29, and no new event processed after the 16:27 restart (the only claim was a 2-day-old page-limit failure).

Re-measured read-only on this PC (Andy's workspace side inbox; counts only, no event text) ✅ — [evidence/measure/andy-side-inbox-tally.txt](evidence/measure/andy-side-inbox-tally.txt):
- pending at the time of the tally: 402 never tried, 9 retrying, 517 quarantined (the live session keeps moving these);
- `Source thread exceeds page limit`: 141, all on 11-18;
- `side update exceeds note capacity`: 59, none after 11-16;
- `side decision needs bounded summary and reply`: 62 (40 on 11-18);
- `side answer carries no decision JSON`: 76;
- `Source persisted result is not this session's exact file`: 14, all 11-17 13:45–14:25Z.

# Design history of item 1 (long threads)

1. First build: read every page and keep a ring of the newest 1600 replies. Rejected by the user.
2. A `direction: desc` read was considered. The recorded Walkie schema with `"direction":{"enum":["asc","desc"]}` is the `order_by` option of a collection `list`/`query` action, not of `threads.read` or `walkie_read_messages` (read from Andy's transcript, not a live call).
3. Then: read only the messages newer than a per-thread cursor. Superseded.
4. **Final (user):** no mandatory thread prefetch at all.
   - The side actor is a native fork of main. Every earlier event of the thread reached main, so it is in the fork's inherited context.

# Fixes

## 1. No thread prefetch on the side claim path ✅

`side-intake.js` `eventSource` replaces `fetchSourceMessage`:
- An event with its own message text: no Walkie read. The prompt gets `source_ref` (conversation, root, `target_message_id`) and `source_context: event_only`.
- An event cut by the source (`event_truncated`, `[잘림]`) or without message text: exactly one `walkie_read_messages` with `after = target-1`. The first row must be the target, in the event's conversation and thread, with the claimed lineage; it is passed as `target_message` (`verified_target`).
- The identifier checks that need no read still refuse malformed or conflicting identities before any spawn. A forged target outside the thread is refused.
- Removed from this path:
  - the thread pages, the ring and the page limit;
  - the main-context budget and its compaction waits (`compactMain` and its counters, which no other path used);
  - the bounded-thread note.
  The persisted-result file checks stay because the one target read still uses them.
- The prompt says the event plus the inherited context are the basis, never to assume unseen content, and that the fork's read-only Walkie tools may read more only when needed, never the whole thread. The Python `DEFAULT_SIDE_INSTRUCTION` is byte-equal.

| event | HEAD 1b42a73 | after |
|---|---|---|
| on a 2,400-reply thread, with text | 8 thread pages, then fails "Source thread exceeds page limit" | **0 Walkie reads**, fork spawned |
| cut (`[잘림]`) or textless | full thread read | **1 read** (the target) |
| forged target outside the thread | refused | refused ("Source target is not in the event thread") |

## 2. Gate re-check: one batch per tick, latest state only ✅

- `work_gate_threads.py` (sidecar per session, outside the board, so no approval digest change):
  - a thread row per Walkie thread with `last_seen`;
  - a node → thread binding.
  `work_side_resolve` records both after every committed event, from the host's own classification of the event (`side_reply_connector.source_read`), never from the model.
- `work_side_gate_check` with `thread_reads: true` (the new mod):
  - Gates are collected P0 → P1 → rest, and the router answers `read_threads`, one entry per thread with its cursor.
  - The host reads each thread once with `threads.read` after the cursor (at most 2 pages), checks every row (conversation, thread, ascending past the cursor), and hands the reads back in the same tick.
  - The router then queues **one** side event:
    - gates of threads with new messages, with those messages (newest 30, clipped) under the first gate of the thread;
    - gates without a thread, or whose read failed, as today, told to read only what is needed;
    - a thread with nothing new drops its gates; no gate left means no event and no fork.
  - Cursors move to the newest message read.
- An older mod without `thread_reads` gets one event for all gates (the 24-gate split is gone).

| case | measured |
|---|---|
| 20 gates on 3 threads (+2 non-thread) | 3 `threads.read`, 1 event (1 fork); the thread with nothing new left out |
| a gate thread with 2,000 replies, cursor at #2035 | 1 read, 5 messages (#2036–#2040) |
| cursor far behind | 2 pages (400 messages), `more: true`, never further |
| nothing new | no event, no fork; the tick is recorded |
| two gates on one thread | one read; the second gate points to the first |

## 3. Claim order ✅

`side_intake_processing.claim`: P0, then P1, first. Then a retry lane (rows with `side_failure`, oldest first) alternates one for one with new events (`claim-lane.json`). One actor per event (lease) as before.

| fixture: 40 old retries, 6 new, a P1, a P0 | order |
|---|---|
| HEAD | P0, P1, then all 40 retries before any new event |
| after | P0, P1, then new, retry, new, retry … |

## 4. Note capacity and the approval digest ✅

- The refusal stopped on 11-16 (`side_note` compaction, d5560bf).
- Remaining defect fixed: the side note went into `note`, which is part of the root's approval `scope_digest`. On an executing goal it now goes to `progress` (outside the digest) with the same compaction. 30 updates leave the digest identical.

## 5. Model output shape ✅

- Over-long `summary`/`reply`/`note`/`title`/`understanding` are cut with ` … [host cut N characters]`. Every other check stays.
- One corrective turn: when the answer has no decision JSON, or the router refuses its shape before saving the proposal (nothing was sent yet), the same fork gets the exact error once. A second refusal counts.
- Measured on the real engine: inside the fork's own turn end, the engine declines the send ("a plugin declined the resume: Side SSE native fork authorization did not match its pending host record"). The host retries 1.5 s after the hook returns, and that send is delivered.

## 6. Persisted-result file refusals: cause measured, no change ✅

- The 14 refusals fall between 13:45 and 14:25Z on 11-17. All 88 saved Walkie results Claude Code wrote in that window carry the 2.1.326 suffix.
- The current rule accepts 88/88 and the pre-ab6edbe rule 0/88 — [evidence/measure/persisted-window-rule-check.json](evidence/measure/persisted-window-rule-check.json).
- 🟡 The time the fix reached this workspace was not measured. Quarantined rows stay quarantined until an operator retries them.

# Verification

| check | result |
|---|---|
| fail-before (HEAD 1b42a73 code, new tests) | Python 20 fail (13 F, 7 E), mod suite 3 fail — [fail-before-python-final.txt](evidence/tests/fail-before-python-final.txt), [fail-before-plugin-final.txt](evidence/tests/fail-before-plugin-final.txt) |
| `claude plugin test` ciel-work | 108/108 |
| ruff | all checks passed |
| mod-related and touched test files | 619/619 |
| real-backend `test_work_mod_real_backend.py` | see [log.md](log.md) |
| Windows groups, Linux py3.10 shards | see [log.md](log.md) |
| real claude.exe 2.1.327 load | PASS — [evidence/real-load/result.json](evidence/real-load/result.json) |

Real load (owned scratch under D:/Temp, stub Messages, owned router MCP with the real Work and side tools, stub Walkie MCP serving a 2,400-reply thread):
- **0** `walkie_read_messages`, **0** `threads.read`. The fork's prompt carries `source_context: event_only` and `source_ref` (root 40, target 45), and no thread.
- The first answer had no JSON. The correction was declined inside the turn end, then delivered 1.5 s later. The fork answered validly, and the receipt is `processed` / `answer` with no failure counted.
- The run had no reply route configured, so no Walkie send happened. The ACK, understanding and final targets are covered by the unchanged router outbox and `test_side_understanding_flow` (passing).
- No unload. Live `~/.claude/settings.json` and bin unchanged; `~/.claude.json` does not mention the run. No owned process left; the scratch folder was removed after the driver.

# Changed contracts (tests updated to the requested behavior)

- Executing-goal side note goes to `progress` (`test_side_work_decision.test_approval_for_waiting_item_requests_main_without_demoting_goal`).
- Gate events are one batch per tick (`test_work_gate_check`: 60 gates → 1 event, previously 3).
- The thread-prefetch, bounded-thread and compaction-wait tests were replaced by no-prefetch tests (`test_side_intake_mod`, `side_prefetch_harness.mjs`, `side_persisted_harness.mjs`, `side_persisted_unicode_harness.mjs`).
- `work_side_renew` `source_bounded` (KOBE hotfix) has no producer any more; receipts that already carry it still display.

# Not done / open

- `turn_error` failures (160 in the tally) were not in the brief and were not investigated.
- Already-quarantined rows are not re-queued by this change.
- Gate tick on the real engine: covered by the mod suite and router tests, not by a real claude.exe run.
