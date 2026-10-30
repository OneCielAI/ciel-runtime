---
type: Diagnostic
title: Keeping 504 diagnostic lines past router log rotation
description: Router 9524 rotates its log about every two hours and keeps one backup, so the redacted SSE shape lines for the next 504 would be lost within hours; a read-only watcher now keeps them.
tags: [retry, sse, diagnostics, logging]
timestamp: 2026-10-28
---
# Initial status of the recurring 504 (n436), observed at 03:14

- No SSE error event has occurred on router 9524 since the redacted shape diagnostics went live (2026-10-27 20:05 local). `router.log.1` (20:05-22:15) and `router.log` (22:15-03:14) contain no `anthropic_sse_error_shape` or `anthropic_sse_upstream_error_event` line.
- 9524 was relaunched at 00:52 and again at 03:07 local, when the session was relaunched. I did not do either relaunch. Both launches started the router from the installed 679245f snapshot, which contains the d2856f8 diagnostics, so capture is still active. Router 9505 also runs 679245f (PID 30608).

# Retention gap

`RouterFileLogger._rotate_if_needed` (`ciel_runtime_support/runtime_logging.py`) replaces `router.log` with `router.log.1` once the log exceeds 1,000,000 bytes. Only one backup is kept. On 9524 that is about every two hours: the 22:15 rotation overwrote the 13:45-20:05 lines. A 504 diagnostic line would therefore survive only two to four hours unless someone reads it in time.

# Watcher

[watch_router_diagnostics.py](evidence/scripts/watch_router_diagnostics.py) polls the instance directory every 5 s.
- It opens `router.log`, reads only the new bytes and closes the file again, so it never holds the log open and does not block rotation. After a rotation it first finishes the old file from `router.log.1`.
- It keeps only lines carrying the retry/SSE diagnostic keys. These lines are already redacted by the router: no message text, request bodies, URLs or credentials.
- It exits 120 s after the first SSE error line, so the matching guard, commit and retry lines are kept, or after 7 days.
- [Self-test](evidence/watcher-selftest.json) on an owned synthetic log: all 8 checks passed. Covered: lines written before start, unrelated lines, a partial line split across polls, lines before and after a rotation, the rotation count, and exit after an error.
- Started on 9524 at 2026-10-28 03:14 local; ended at 04:35:36 after capturing the 04:33:34 recurrence. Output goes to `evidence/live-9524/` (`kept-lines.txt` and `watch-status.json`). The router keeps about 33 KB of these lines per 5 hours.

# Boundary

The watcher only reads. It does not change the router, its rotation rule or any session. The next occurrence was captured at 04:33:34: unrecognized_frame, attempts=0. See [follow-up](../gateway-error-envelope/findings.md). The watcher is stopped, not still monitoring.
