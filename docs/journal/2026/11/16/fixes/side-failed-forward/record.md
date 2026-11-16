---
type: fix-record
title: Failed SSE events are forwarded to main as their original text, not dropped
description: A side event that reached a terminal failure (quarantine) was previously left in pending/ with no reply and no main notice. Its original text now reaches main once through the existing at-most-once host notice claim.
timestamp: 2026-11-16T22:30:00Z
tags: [side-intake, sse, quarantine, main-notice, celly, walkie]
---

# Request (user, 2026-11-16)

> 그럼 실패한 sse 메시지는 메시지 원문만 메인세션으로 전달해줘야지 그냥 조용히 씹어버림어떻게해

# Measured

- **Celly.** 152 side forks on 2026-11-16 ended with "all targets were skipped by pre-dispatch filters" ([../../diagnostics/celly-side-predispatch/record.md](../../diagnostics/celly-side-predispatch/record.md)). After 6 attempts each event was quarantined.
- **Walkie.** 108 events were quarantined in 3 days ([../side-ack-readback/evidence/walkie-134499-timeline.txt](../side-ack-readback/evidence/walkie-134499-timeline.txt) and the census run that day).
- **Code before this fix.**
  - `side_intake_processing.fail` saved `quarantined: true`.
  - `claim` skips quarantined rows.
  - No code sent such a row's original anywhere: no reply, no main notice, no Work item. The person saw nothing unless they opened the Side pane.

# Fix

1. **Mark the row for forwarding.** When `fail` quarantines a row, it records `main_forward` on that row and adds it to the `forwards-pending.json` index (`side_forward.mark`). The marker is committed before the index entry is published.
2. **Deliver it from three host paths.** All three use the same `side_notice` claim, stored on the event's own record. That claim already guarantees at-most-once delivery of released-wait and relay notices; it now also accepts a forward row and writes to `pending/`.
   - The failing `work_side_fail` call returns `main_forward` in the same host dispatch, and the host appends it to main at once.
   - `work_side_claim` offers `main_forwards` for crash recovery.
   - `work_side_status` offers `main_forwards` too, so delivery continues while every side actor slot is busy.
3. **What main receives.** `side_forward.text` builds the message from the stored original every time it is offered, never from a truncated copy. The header line is `[Ciel side 처리 실패 · 원문 전달 · event <id> · <category> · N회 시도]`, followed by one sentence (main decides any answer and Work; this is not execution approval), followed by the original unchanged.
4. **Delivery failures.**
   - A definite append failure is offered again with the full original.
   - An uncertain append waits until a person releases it, with the existing notice retry pins, and is then offered once more.
5. **Legacy rows.** Rows quarantined by older builds are backfilled gradually: at most 32 pending rows are opened per call, after a durable keyset cursor.
6. **No side-fork retry.** A quarantined event is never claimed by a side fork again.

# Verification

- **Fail-before.** Run against HEAD code ([evidence/fail-before.txt](evidence/fail-before.txt)):
  - `test_side_forward` cannot import (module missing);
  - the two host scenarios (`side-failed-event-original-reaches-main`, `side-status-delivers-failed-original-without-free-actor-slot`) fail.
- **Pass-after on Windows.**
  - Forward, release/relay notice, side MCP, receipt projection, tool schemas, ACK outbox, Work mod harness and side mod harness: 294/294.
  - `claude plugin test ciel-work`: 103/103.
- **Ruff.** All tracked Python files pass.
- **Linux.** Results are in [evidence/linux-tests.txt](evidence/linux-tests.txt).
