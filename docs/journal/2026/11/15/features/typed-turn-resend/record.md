---
type: implementation-record
title: Automatic resend of a typed turn that ended on a transient API error
description: User decision 2026-11-15, a person-typed Claude Code turn that ends on a transient API error is resent once a minute ×10, then every 10 minutes ×10 (20 in all).
timestamp: 2026-11-15T15:30:00Z
tags: [claude, api-error, retry, resend, terminal]
---

# Why

On F:\omini-router (router 9489), a typed turn ended at 2026-11-15T09:33:47Z with `API Error: 500 transient retry deadline or attempt limit reached`. Nothing resent it: Claude Code did not, and the only Ciel resend, `runtime_input_retry`, covers channel/SSE-injected inputs only.

That router runs build 57dbfb3, whose retry allowance started when the request arrived. The 72 s spent before the first upstream open used the allowance up. The allowance starting at the first open (d69ca83, 2026-11-14) is in current builds; the session has to restart to get it.

# Behavior

Module: `ciel_runtime_support/claude_typed_turn_resend.py`, driven by the launcher's existing `ClaudeStreamRecoveryMonitor`, which supplies the same transcript records, terminal gate and input gateway.

- **What starts a chain:**
  - The turn was opened by input typed by a person (`typed_by_person`).
  - Its native `isApiErrorMessage` record is transient by `runtime_input_retry.classify_claude_api_error`, the classifier channel resends use.
- **Schedule:** 60 s before resends 1–10, 600 s before resends 11–20, at most 20.
- **The resend itself:** one raw injection through the runtime input gateway, with the stream-recovery exact-scope and TTL delivery gate.
  - Its first line is `[ciel-runtime 입력 재전송 n/20 · <error>]` plus a "do not repeat finished work" sentence; the original input follows unchanged.
  - It waits, without dropping, while the terminal is busy, has a draft, compacts, or is unknown.
  - If no echo appears within 210 s, it is admitted again without being counted.
- **Ends:**

  | Trigger | Logged as |
  | --- | --- |
  | Model answer | `recovered` |
  | Newer typed or queued input, interrupt, compaction, session change | `cancelled` |
  | Non-transient error | `stopped` |
  | 20 failed resends | `exhausted` |

  Every step is logged as `claude_typed_resend_*` with ids only.
- **Not resent:**
  - input with an image (text-only would change it);
  - channel/peer input (its own resend applies);
  - a stream cut after output (stream recovery);
  - `error=unknown` with no HTTP status. This is the 09:37:00Z "Gateway response stream was interrupted" after a tool_use. Whether that case is retried is an open user decision.

# Evidence

- `tests/test_claude_typed_turn_resend.py`: 12 passed. Covered:
  - the schedule, and the full 20-resend chain with waits 60×10 then 600×10;
  - the recovered, cancel, non-transient, channel, image, busy-wait, undelivered-readmit, input-id echo and scope cases;
  - the monitor wiring.
- Owned check through the real launcher poll path (`TranscriptDeltaDeliveryService.poll_stream_recovery`):
  - input: the unmodified 09:33:47Z error record from the F:\omini-router transcript, after a typed prompt;
  - at 59 s, nothing was sent;
  - at 60 s, one resend `1/20 · 8b0c247e` carrying the original text;
  - logs: `claude_typed_resend_scheduled ... error=server_error_500`, then `claude_typed_resend_admitted`.
- Stream recovery, terminal, settings and runtime_input_retry suites, plus architecture contracts: 323 passed on Windows.
- Linux (python:3.12): 96 passed.
