---
type: fix-record
title: Side actor — immediate ACK/readback before any processing, P0 readback, no compaction wait for P0/P1
description: Every external SSE event now gets a host-sent ACK with a readback of the message before source prefetch, main compaction or a fork; the processing reply follows separately. A P0 reply states P0, and a P0/P1 event no longer waits for main's compaction.
timestamp: 2026-11-16T21:30:00Z
tags: [side-intake, sse, walkie, ack, readback, p0, priority, compaction]
---

# Requests (user, 2026-11-16)

> 우선순위를 p0로 변경하고 ciel-walkie 에게 전송했는데 10분째 우선순위조절도 안하고 ack도 안해주는데 … 싸이드액터는 최소한 ack, readback을 우선하고.. 처리내용을 또 발송해야하는데

> 항상 싸이드액터는 ack , readback을 우선하고. 그뒤에 처리할내용을 처리하고 처리내용을 다시 보내줘야하는데.. 절차가 안지켜짐

> 또 순위가 바뀌었을때 순위조절실패

> sse가 픽업해서 처리하는데30분이상 걸림

> 우선순위 상관없이 ACK Readback(복기)등은 즉시이루어져야하고..그후 처리사항도 반듯이 응답해야하는데 잘안되고있음

# Measured (Walkie, read-only)

Sources: [evidence/walkie-134499-timeline.txt](evidence/walkie-134499-timeline.txt) and [evidence/side-forks-134499.txt](evidence/side-forks-134499.txt).

**Message 134499.** It was Daniel's P0 message ("우선순위가 바뀌었음으로 큐순서 재조절할것", thread_task priority 1, emergency true).
- Received at 18:33:37Z.
- Its only reply went out at 19:15:31Z, 42 minutes later.
- Nothing reached the person before that reply: `external_ack` is `not_sent` on every receipt.

**Why it took 42 minutes.**
1. **18:33–18:41.** The event was claimed 3 times, and each claim ended in `main_compaction_pending`. The source thread did not fit main's remaining context, so the event waited for main to compact.
2. **18:41–18:56.** The first fork's provider answered 503 ("Request exceeded queue budget maxWaitMs"), so the fork made no output.
3. **19:04–19:08.** The second fork got a 474,071-character prompt. It hit "Output token limit hit" 3 times, then failed with "exceeded the 128000 output token maximum".
4. **19:10–19:15.** The third fork decided `update n1953, priority P0`. The host activated P0 (`p0.effective_priority P0`), but the reply's last line said **"n1953 갱신 · P1 · 현재 진행 중인 항목입니다."**
   - Why: the board stores P0 as canonical P1, and `outcome_text` read the board value.
   - This is what the person saw as "순위조절실패".

**Message 134499 v2.** The edited message (event `message.edited`) became `duplicate_source` of the first event. It received no reply of its own, by design.

**Other events in that window.** Six other events took 37–55 minutes. Their side forks failed with `turn_error` 1–5 times first.

# Cause (confirmed in code)

- **No immediate ACK path existed.** The host sent one reply, after the AI decision: `side_reply_outbox.prepare` runs after `finish`. The side prompt forbids the fork to ACK. So any delay from compaction, a provider error or a fork failure delayed the ACK too.
- **The final line showed the board value.** `side_work_decision.outcome_text` printed the canonical board priority (P1), not the effective P0.
- **P0 waited for compaction like any other event.** `pollSide` made a P0 event wait for main's compaction whenever the full thread did not fit. It did so even when a bounded thread (root + target + newest replies) fit.

# Fix

1. **New `side_ack_outbox` (router tool `work_side_ack`, host-only).** It keeps its own durable state on the receipt (`ack_outbox`, `external_ack`).
   - Its Walkie `client_id` is `sha256(receipt_id + ":ack")`, separate from the final reply's `":final"`.
   - The text is host-composed: `ACK — 요청을 받았습니다.` followed by `복기:` and the event's own message text (bounded to 1,200 characters).
   - It never waits for a model.
   - An uncertain ACK is recorded and never re-sent.
   - An event with no source message is recorded `not_applicable`.
2. **The ACK is sent first.** `side-intake.js pollSide` calls `submitAck` right after the claim is validated, before source prefetch, the compaction wait, the model and the fork.
   - An ACK failure is logged and processing continues.
   - The processing-result reply is unchanged and is still sent once after the decision.
3. **The reply states P0.** `side_intake_mcp._p0_readback` rewrites the committed last line with the effective P0 once the activation succeeded ("n1953 갱신 · P0 · …"). If activation failed, it appends "P0 적용 실패: <reason>".
4. **P0/P1 no longer wait for compaction.** A P0/P1 event (the urgent claim lane) whose full thread does not fit gets the bounded thread at once. It does not wait for main's compaction. Ordinary events keep waiting for it.
5. **Wiring and tests.**
   - Tool lists: `NAMES`, `WORK_TOOL_NAMES`, the Work-off allow-lists and `HOST_ONLY_SIDE_TOOLS`.
   - Tests: the mod suite `HOST_REPLIES`, and the router's own-call allow-path (`work_side_*`).
   - The two-line side pane change from earlier today ships in the same commit; see the side pane section of the journal for 2026-11-16.

# Verification

- **Fail-before.** The new tests run against HEAD code ([../api-error-resume/evidence/fail-before.txt](../api-error-resume/evidence/fail-before.txt)):
  - `test_side_ack_outbox` cannot import (module missing);
  - `test_side_sends_immediate_ack_before_source_prefetch_and_fork` fails;
  - `test_persisted_mcp_result_context_budget_and_spawn_proof` (P0 partial-room case) fails;
  - `host-only-side-tools` (`work_side_ack`) fails.
- **Pass-after on Windows.**
  - Side, outbox, connector, MCP, decision, projection, tool-schema and gate tests: 146/146.
  - Side mod and Work mod harness tests: 201/201.
  - `claude plugin test ciel-work`: 103/103.
- **Linux.** The Docker run (python:3.11-slim) results are in [evidence/linux-tests.txt](evidence/linux-tests.txt).
