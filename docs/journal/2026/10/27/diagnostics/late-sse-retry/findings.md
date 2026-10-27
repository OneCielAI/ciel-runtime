---
type: Diagnostic
title: CielAiRouter delayed pre-output SSE error was never retried
description: HTTP 200 streams whose first meaningful event is a late aggregate-timeout error escaped the router retry window while the CLI's own retries are disabled; a read-time guard now retries them under the same request budget.
tags: [cielairouter, retry, sse, streaming, claude]
timestamp: 2026-10-27
---
# Observed (local router 9524, current session)

Read-only router log `router-instances/9524-bd5f642b3583/router.log` (local time, UTC-05):

| Request | HTTP 200 to client | SSE error | Router lines |
|---|---|---|---|
| 08:46:55 | 08:49:41 | 08:52:22 | 10530, 10569, 10607-10609 |
| 10:01:43 | 10:02:37 | 10:05:13 | 12435, 12449, 12488-12490 |
| 10:05:19 | 10:07:54 | 10:11:06 | 12497, 12533, 12579-12582 |

Each failure logs `anthropic_sse_upstream_error_event ... text_len=0 emitted_tool_use=False message_start=False`, then `claude_stream_recovery_failure_skipped reason=no_message_id` and `agent_turn_ended ... reason=error`. No further `/v1/messages` request follows any of them. The upstream log line reads `attempt=1/1`; the config has no `gateway_retries`, so the shared transient budget is the default 3 and `request_timeout_ms` is 300000.

The HTTP 504 text is only inside the SSE error message; at the router/client boundary the response is HTTP 200 followed by an `event: error` frame. The exact model list in the user's screenshot was not matched to these log lines; only the shape is.

# Why nothing retried

1. `claude_environment.py:625-631`: for routed `cielairouter` the launch sets `CLAUDE_CODE_MAX_RETRIES=0`, `CLAUDE_CODE_RETRY_WATCHDOG=0`, `CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES=0`, `CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK=1` (comment: "One retry owner: the router in routed mode"). Claude Code 2.1.311 contains both `Error streaming, falling back to non-streaming mode` and `Error streaming (non-streaming fallback disabled)`.
2. The router's only owner, `transient_retry._preflight`, inspects at most 64 KiB for 100 ms after open. An error that arrives later is read by the relay after the executor has returned, so it is relayed unchanged.
3. Post-stream Claude recovery requires a message id or visible output; a pre-output failure has neither (`no_message_id`).

An earlier owned run that omitted this env (not kept) succeeded only because the CLI logged `Error streaming, falling back to non-streaming mode` and re-sent the request itself; with the routed env that path is off, which matches the live logs (no follow-up request).

# Reproduction (isolated, real Claude Code 2.1.311)

Fake gateway: HTTP 200 `text/event-stream`, a comment, pings every 10 s, then `event: error` with the aggregate `api_error` "... timed out (HTTP 504)". Owned router, routed env above, fresh HOME/config/ports, egress sentinel ([e2e_late_sse.py](evidence/scripts/e2e_late_sse.py), [e2e_late_sse_ui.py](evidence/scripts/e2e_late_sse_ui.py)).

| Run | Code | Upstream conversation requests | Outcome |
|---|---|---|---|
| [-p, 0.35 s](evidence/scripts/run-installed-115731/result.json) | installed 8ebacfa | 1 | exit 1, error text returned |
| [-p, 70 s + pings](evidence/scripts/run-installed-delay70-115929/result.json) | installed 8ebacfa | 1 | exit 1 |
| [interactive, 20 s](evidence/scripts/ui-installed-120558/01-outcome.png) | installed 8ebacfa | 1 | TUI shows `API Error: ... timed out (HTTP 504)` |
| [-p, 0.35 s](evidence/scripts/run-source-115737/result.json) | worktree | 2, identical body SHA-256 | exit 0, `LATE-SSE-RETRY-SUCCEEDED`, `cielairouter_late_sse_retry attempt=1/3` |
| [-p, 70 s + pings](evidence/scripts/run-source-delay70-115931/result.json) | worktree | 2, identical | exit 0 |
| [-p, every attempt fails](evidence/scripts/run-source-failall-115744/result.json) | worktree | 4, identical | retries 1/3, 2/3, 3/3, `cielairouter_late_sse_stopped ... reason=budget_exhausted`, original error relayed, exit 1 |
| [interactive, 20 s](evidence/scripts/ui-source-120632/01-outcome.png) | worktree | 2 | TUI shows `LATE-SSE-RETRY-SUCCEEDED` |

No run used the CLI non-streaming fallback (`no_cli_nonstreaming_fallback` true). Live routers 9505 (pid 79472) and 9524 (pid 65156) were identical before and after every run.

# Change (`ciel_runtime_support/transient_retry.py`)

An accepted `text/event-stream` success response is wrapped in `_PreOutputSSE`. On the consumer's first read it holds comments and typed `ping` frames (at most 64 KiB, under the existing socket idle timeout) until the first meaningful frame:

- exactly one complete frame that `_retryable_verdict(503, …)` accepts (strict JSON error, not auth/quota/billing/usage) and no trailing bytes: close, wait the scheduled backoff, reopen through `_execute` under the same `TransientRetryBudget` (same attempt ceiling and sized deadline);
- anything else (generation event, unknown, mixed, partial, over the byte cap): relay unchanged; never reopened afterwards;
- budget exhausted: log `cielairouter_late_sse_stopped` and relay the original error;
- read exceptions are not replayed; a typed HTTP error from the reopen becomes one SSE error event (headers are already downstream), an untyped one is raised to the relay's existing failure path;
- if the relay closes the response during the wait or the reopen, no reopen starts and a reopened response is closed (`UpstreamClientDisconnected`).

Downstream liveness during the hold comes from the relay's own `: ciel-runtime-keepalive` comments (`streaming_anthropic.py` `upstream_lines_with_downstream_keepalive`, 15 s), which read the wrapper on a separate thread.

The sized deadline already covers the live timing: default scopes size `(limit+1)·(timeout+0.1)+limit·30` = 1290.4 s for a 300 s opener (`bounded_timeout`), so an error 330 s after the request still admits a retry; a test pins this and the past-deadline relay.

# Verification

- [Transient suite](evidence/focused-tests-transient.txt): `test_transient_retry*.py`, 72 tests OK (12 in `test_transient_retry_late_sse.py`). Before the change, 2 of the first 4 late-SSE tests failed ([baseline](evidence/baseline-tests.txt)). The two close-guard tests fail with the guard removed (`ValueError: I/O operation on closed file`, a reopen occurred), checked in a temporary copy.
- [Related files](evidence/focused-tests-related.txt): api_key_rotation 46, architecture_contracts 261 (42 skipped), handled_error_guards 9, timeout_route, transient_integration 5, transient_context_recovery, meta_provider 49, router_transient_retry_scope 7, upstream_retry 4: OK. `test_claude_compaction_liveness` 1 error: `test_native_anthropic_does_not_install_semantic_start` times out identically on HEAD eedd8ee in the same stripped env ([evidence](evidence/preexisting-native-anthropic-timeout.txt)); not caused by this change, cause not investigated.
- ruff clean on both changed files. Live health before/after the test runs unchanged ([before](evidence/live-before-tests.txt), [after](evidence/live-after-tests.txt)).

# Limits

- Live sessions keep the code they were launched with; this session (9524) is not fixed until it is restarted on a release containing the change.
- Each retry re-sends the whole prompt; an upstream that did work before failing may bill twice (same caveat as the existing open-time retry).
- With live timings (about 5.5 min per failed attempt) the 1290 s deadline admits roughly two or three retries, not necessarily all three.
- Whether CielAiRouter itself could avoid the aggregate timeout is outside this repository.
