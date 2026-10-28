---
type: Investigation
title: Kobe stream ended before message_stop was not continued
description: On .14 Kobe (u24) the router reported an incomplete upstream stream after a tool result with nothing shown; the existing stream recovery does not handle that shape and Claude Code did not retry it.
tags: [stream-recovery, cielairouter, kobe, diagnostics]
timestamp: 2026-10-26T21:08:00Z
---
# Target and scope

User screenshot: `API Error: Upstream Anthropic stream ended before message_stop.`, then `Baked for 13m 6s · done 1:59 PM`, on .14 (port 1004) Kobe. Read-only: no session, queue, config, process or file on .14 was changed. Probes ran Python over SSH from stdin (`evidence/scripts/`).

- Sandbox `C:\ProgramData\aap-win\sandboxes\u24\home\kobe-ai`, account `aapw-a24u24` ([p1](evidence/p1.txt), [p2](evidence/p2.txt)).
- Launcher pid 17844 (`cli --continue`), router pid 4572 on port 9780, instance `9780-651d879f6c68`, Claude Code 2.1.310 pid 12532 with model `ciel-runtime-cielairouter-_walkie_sol61-opus55-fast`.
- Runtime `0.2.51-nightly.20261026-162537.2faae14`. Router log level ERROR.
- Transcript `5ac1ee55-d0d1-4ef7-b4df-3d68e62bbd1b.jsonl`. Remote local time is UTC-7.

# Confirmed

- The text comes from this router: `streaming_anthropic.py:1455-1462` sends an `api_error` event with it when the upstream (cielairouter) SSE ends without `message_stop`. Failure reason `anthropic_stream_upstream_incomplete`.
- Error record `c877795f-5c1f-42ee-98d2-91b3dcc45b23` at 20:59:15.194Z (13:59 local), `isApiErrorMessage=true`, `truncatedAfterOutput` absent. Its parent is a user `tool_result` record (the result of `work_execute`), not an assistant record. The request started 20:59:02 ([p3](evidence/p3.txt)).
- No retry by Claude Code: the next conversation record is user input at 20:59:48, 33 s later.
- The stream recovery ledger has no failure entry for this time. Its newest failure is 2026-10-25T21:48:17Z; `handled` is empty and `chains` is `{}` ([p4](evidence/p4.txt)).
- The launcher monitor only considers an error with `truncatedAfterOutput=true` whose parent is an assistant record (`claude_stream_recovery_runtime.py:534`). This record has neither, so it never becomes a candidate. Even with a ledger entry, `ineligible_reason` excludes a failure with no visible text (`nothing_shown`, line 73-74). The 2026-10-20 design lists "nothing shown" as excluded on purpose (`docs/journal/2026/10/20/diagnostics/claude-stream-recovery/implementation.okf`).

So nothing in the installed runtime re-sends a request whose stream broke before any output was shown, and Claude Code did not re-send it either.

# Not determined

- Why the router recorded no failure. The recorder's skip reasons (`no_message_id`, `scope_changed`, `not_main_session`) are INFO lines, and this router logs ERROR only. If the upstream ended before `message_start`, there was no message id to record; that is not confirmed.
- What the upstream (cielairouter) sent before it ended, and why it ended.
- Earlier errors in the same transcript (HTTP 504 timeouts and `Request timed out`) are a different shape and were not analyzed here.

# Citations

[1] [Processes](evidence/p1.txt), [versions and transcripts](evidence/p2.txt), [transcript records](evidence/p3.txt), [ledger and log](evidence/p4.txt)
