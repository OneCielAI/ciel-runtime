---
type: fix-record
title: Repeated API errors — the runtime resumes every main turn, not only person-typed ones
description: Main turns that ended on transport errors (terminated, stream failed before message_stop, router pre-output limit, gateway interrupted), on non-text or automatic openers, or after a cut stream with a tool block were never resumed; the person typed "계속". The runtime now resumes them on the 1-minute x10 then 10-minute x10 schedule.
timestamp: 2026-11-16T21:30:00Z
tags: [claude, api-error, resend, stream-recovery, retry, runtime]
---

# Request (user, 2026-11-16)

> 추가 아이템 [Image #17] 지속되는 api 에러에 스스로 재시도를 못함..ciel runtime이 재시도를 해줘야함

The screenshot (Celly session) shows several turns that each ended on one of these errors, with "계속" typed by the person after them:
- `API Error: terminated`;
- `Server error mid-response`;
- `Ciel Runtime stopped waiting for the first upstream output: the router pre-output time limit (540s …)`;
- `Upstream Anthropic stream failed before message_stop (TimeoutError)`.

# Measured (read-only)

**Main-thread API error records, last 8 days, all local Claude projects.** Source: [evidence/main-thread-api-errors.txt](evidence/main-thread-api-errors.txt).
- 32 records.
- In almost every case the next main-thread input was a person's "계속" or a new message, 19 s to hours later.

**What the router logged for them on 2026-11-16.** Source: [evidence/router-resend-log.txt](evidence/router-resend-log.txt).

| Router log line | What it means |
| --- | --- |
| `claude_typed_resend_skipped reason=not_transient … error=unknown` (5 times) | The transport errors above. Claude Code records them as `error: "unknown"` with no HTTP status, and `classify_claude_api_error` counts only server/overloaded/api/timeout errors or 408/5xx statuses as transient. |
| `claude_typed_resend_skipped reason=not_transient … error=stream_cut_after_output`, after `claude_stream_recovery_skipped reason=tool_use_seen` | A stream cut after output with a tool block. Stream recovery declined it (it never continues tool blocks), and the resend left it to stream recovery. Nobody resumed it. |
| `claude_typed_resend_skipped reason=non_text_input` | A turn opened with an image. |
| `claude_typed_resend_admitted attempt=1/20` at 11:43:10, then `claude_typed_resend_cancelled reason=queued_input` 2 s later | The chain's own resend was queued by Claude Code (transcript: `queue-operation enqueue` at 17:43:11.789Z, `dequeue` 11 ms later), and the chain read that as new input. The turn failed again at 17:49:06Z (503) and was not resumed. |
| (none) | Turns opened by a task notification, a Work continuation, a peer/side wake or a resumed session never start a chain: only person-typed openers did. |

# Fix

**`claude_typed_turn_resend.py` and `runtime_input_retry.py`.**

1. **Transport errors are transient.** `classify_claude_api_error` treats as transient a record with `error: "unknown"`, no HTTP status, and one of these exact openings:
   - `API Error: terminated`;
   - `Upstream Anthropic stream failed before message_stop (`;
   - `Ciel Runtime failed to relay the Anthropic stream (`;
   - `Ciel Runtime stopped waiting for the first upstream output`;
   - `Gateway response stream was interrupted`.

   The last four are texts this router writes itself or relays from a gateway error frame. A 400 such as "model hosted outside…" stays non-transient.
2. **Every main turn is resumed.** Typed text is resent as before. Any other opener gets a continuation line, `API_RESUME_TEXT`: images, task notifications, Work continuations, peer/side wakes, and turns of a resumed session.
3. **Channel turns.** A channel message (`[ciel-runtime external channel message]` / `[ciel-wake]`) that failed before any answer stays with its own resend in `runtime_input_retry`. Once that turn has answered, a later failure is resumed here.
4. **The chain's own queue entry no longer cancels it.** An enqueue while the chain's admitted resend is not yet delivered belongs to the chain.
5. **New chain after progress.** After an answer ends a chain, a later failure in the same turn starts a new chain.
6. **Stream recovery hands over what it declines.** It does so with `adopt_cut`, in `claude_stream_recovery_runtime._skip` and on a cut with no matching upstream message.
   - Handed over: a tool block, nothing shown, a non-transient reason, no router failure, or a busy/stale/unknown terminal.
   - Kept as stops: plan mode, a person typing, newer or queued input, a disabled setting, another scope.

The schedule is unchanged: 1 minute ×10, then 10 minutes ×10. It waits while the terminal is busy, never typing over a person.

# Verification

- **Fail-before.** The new tests run against HEAD code ([evidence/fail-before.txt](evidence/fail-before.txt)): 13 errors and 1 failure in `test_claude_typed_turn_resend` / `test_claude_stream_recovery_runtime`. They cover each transport text, the image opener, the untyped openers, the answered channel turn, the own queue entry, the new chain after progress, and the tool-block handover.
- **Pass-after on Windows.** `test_claude_typed_turn_resend`, `test_claude_stream_recovery_runtime`, `test_runtime_input_retry` and `test_channel_transcript`: 102/102.
- **Linux.** See [../side-ack-readback/evidence/linux-tests.txt](../side-ack-readback/evidence/linux-tests.txt).
