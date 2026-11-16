---
type: fix-record
title: Long compactions failed and stopped the session — give native compaction enough time, and resume after a failed auto-compaction
description: Claude Code abandoned a compaction whose first frame came later than ~12.5 minutes, the router cut a compaction attempt at 300 s and gave up at 540 s, and nothing resumed a turn that a failed auto-compaction ended.
timestamp: 2026-11-16T15:40:00Z
tags: [compaction, router, retry, claude-code, timeout, resend]
---

# Requirement (user, 2026-11-16)

> 컴팩트가 10분이상걸릴때도있는데 영향은 없는거야?
> 그리고 컴팩중에 에러가나서 재시도하는경우도 많고.. 따라서 여기에 충분한 시간을줘야한다는거야
> 진행

# Measured

## Claude Code itself (owned claude.exe 2.1.321, loopback stand-in, isolated config)

The stand-in answered every request at once, except the `/compact` request. That one got HTTP 200, the router's keepalive comment every 15 s, and the summary after the hold. Probe script: [evidence/compact_hold_probe.py](evidence/compact_hold_probe.py).

| Run | Env | Hold | Outcome |
| --- | --- | --- | --- |
| `compact-probe-Drouted900` | routed cielairouter env as launched today (no stream fallback, no CLI retries) | 900 s | At 750.2 s: `Error during compaction: API Error: Stream idle timeout - no chunks received`. No compaction boundary. |
| `compact-probe-D900` | Claude Code defaults | 900 s | Streaming request abandoned at 750 s, then a non-streaming fallback request. That fallback is disabled in the routed env. |
| `compact-probe-R1200` | `API_TIMEOUT_MS`, `CLAUDE_STREAM_FIRST_BYTE_TIMEOUT_MS`, `CLAUDE_STREAM_IDLE_TIMEOUT_MS`, `CLAUDE_BYTE_STREAM_IDLE_TIMEOUT_MS` = 3,600,000 | 1200 s | One request waited 1200 s. The compaction succeeded with a boundary written. |
| `compact-probe-P1800` | exactly the shipped routed env: the four timeouts = 2,100,000, no stream fallback, no CLI retries | 1800 s (the router's new compaction bound) | The stand-in held the one request 1800.1 s. The compaction succeeded with a boundary written. |

So with today's env, any compaction whose summary starts later than about 12.5 minutes fails. The cause can be a slow model, gateway retries or a queue. This matches the 2026-11-13 measurements for ordinary requests (aborts at 600–750 s, journal `2026/11/13/fixes/router-upstream-error-retry`). The variable names come from the claude.exe binary.

## Router (code)

- **`transient_retry`:** every request, including compaction, had a 420 s retry window, a 540 s pre-output bound from arrival and 20 retries.
- **Per-attempt socket read:** 300 s (`requested_timeout`). On 2026-11-06 a session reported "Prompt is too long, automatic compaction failed: API Error: TimeoutError: The read operation timed out". That text is a session's transcription of a Walkie screenshot, not a log line.
- **`runtime_compaction_control`:** released input admission after 1200 s even while a compaction was still running. Walkie router log, 2026-11-16 local time (UTC−6): admission paused 02:15:09, released 02:35:19 (`compact_failed_admission_released`).

## Resume

`claude_typed_turn_resend` resent only person-typed turns, and it cancelled on any compaction. A turn that ends with Claude Code's `Prompt is too long · automatic compaction failed: …` record was resumed by nothing. In Walkie's transcript, no Stop hook ran after any of 57 API-error turn ends (journal `stop-read-deadline`).

# Fix

1. **Router — compaction scope** (`transient_retry`, `router_http`):
   - A request whose newest user text is Claude Code's native compact instruction (`context_summary_policy.native_claude_compact_request`, the same classifier as `compact_request_kind`) gets a 1680 s retry window, a 1800 s pre-output bound from arrival and up to 60 retries.
   - Each attempt may wait up to 900 s for its first byte (`COMPACTION_ATTEMPT_READ_SECONDS`). The bounds above still cap it.
   - This applies on both the routed and the bridge paths. Ordinary requests keep 420/540/20/300.
2. **Launch env (routed cielairouter only):** `API_TIMEOUT_MS`, `CLAUDE_STREAM_FIRST_BYTE_TIMEOUT_MS`, `CLAUDE_STREAM_IDLE_TIMEOUT_MS` and `CLAUDE_BYTE_STREAM_IDLE_TIMEOUT_MS` are set to 2,100,000 ms (35 min), above the router's 30-minute bound. Direct mode and other providers are unchanged.
3. **Input admission** (`RuntimeCompactionPolicy.confirmation_timeout_seconds`): 1200 → 2400 s, past the compaction bound.
4. **Resume after a failed auto-compaction** (`claude_typed_turn_resend`):
   - The `automatic compaction failed` record starts a resend chain whoever opened the turn: a person, a channel, a Work continuation, a peer, or an unseen opener. The schedule is the same: 1 min × 10, then 10 min × 10.
   - A typed opener's text is resent; otherwise a continuation line is sent.
   - Delivering the resend makes Claude Code compact again. That chain's own compaction boundary does not cancel it, and a model answer ends it as recovered.
   - Other API errors from untyped turns are still not resent.

# Verification

- **New tests:**
  - `tests/test_compaction_time_allowance.py` (10): bounds order; a compaction scope gets 1680/1800/60 and an ordinary one 420/540/20; an explicit `gateway_retries` keeps its count policy; the 900 s attempt read stays capped by the window; a recovery child inherits the allowance; the native compact classifier rejects quoted text and ordinary turns; a real `do_POST` opens the compaction scope on the routed and bridge paths for compaction bodies only.
  - `tests/test_claude_environment.py`: the routed env carries the four timeouts; direct mode and other providers do not.
  - `tests/test_claude_typed_turn_resend.py` (+6): any turn resumes after a failed auto-compaction; a typed turn resends its own text; the resend's own boundary does not cancel and an answer recovers; repeated failures reschedule to 20 and stop; a typed chain that hits a failed compaction continues; an untyped turn's other API errors are still not resent. With the previous module, 7 tests failed ([evidence/resend-fail-before.txt](evidence/resend-fail-before.txt)).
- **Linux (python:3.11-slim):** the changed and related modules (compaction allowance, environment, resend, bridge arrival, compaction control and evidence, all `test_transient_retry*`): 325 passed.
- **Suites (Windows):**
  - Router, retry, context, compaction-control, bridge and launch modules (52 files): 708 passed, 1 skipped.
  - Stream recovery, resend and input-retry modules: 199 passed, 1 failed. The failure is a menu test that cannot read a TTY under pytest's stdin capture; it passes with `-s` (8/8). It is unrelated to this change.
