---
type: Fix
title: CielAiRouter preacceptance timeout retries
description: Typed timeout exceptions during upstream open now use the existing shared bounded retry owner instead of failing immediately.
tags: [cielairouter, retry, responses, timeout, transport]
timestamp: 2026-10-23
---

# Report and evidence boundary

User reported local `/v1/responses` returning HTTP504 with SSE `event: error`,
`api_error`, message `TimeoutError: The read operation timed out`.
The affected port 9179 is not listening on this machine and no matching router
log was available. This signature alone does not establish that the upstream
returned HTTP504: `UpstreamFailure.from_transport_error` also produces precisely
that local HTTP504 from a bare TimeoutError or URLError containing TimeoutError.

Source baseline: origin/nightly c741650, isolated worktree
`C:/Users/djlov/ciel-runtime-wt-timeout`.

# Confirmed gap

Native Responses uses `provider_responses_passthrough._open_checkpoint_request`
-> `transient_retry.open_with_transient_retry` directly. The executor formerly
caught HTTPError for structured classification, but stopped/marked all other
exceptions immediately. A typed open timeout therefore never reached retry
scheduling. Changing only legacy upstream_retry outer loops would not fix this
native route.

Complete structured HTTP504 JSON/SSE errors already retry. No SSE framing rule
was relaxed; display text omitting trailing blank lines does not establish the
actual wire framing.

# Change

Only `ciel_runtime_support/transient_retry.py` production code changes:

- Recognize bare TimeoutError (socket.timeout) and URLError whose direct reason
  is TimeoutError, raised by open_once before response acceptance.
- Use one shared backoff/allowance scheduler for HTTP rejections and typed
  timeouts. No fabricated HTTPError is introduced; the final original timeout
  exception is retained for existing local504 error mapping.
- Same request owner/operation ID, default3 retries, cap10, explicit0, 60-second
  monotonic admission deadline, cancellation, gateway reselection and terminal
  handled markers. HTTP and timeout attempts share the allowance.
- Log `kind=transport_timeout` without claiming an observed upstream HTTP504;
  structured HTTP errors log `kind=http_rejection` with the actual status.
- Other providers, TLS EOF/reset/broken pipe, generic timeout-like text,
  malformed/mixed SSE, and accepted-stream reads remain unreplayed.

This deliberately changes timeout policy: an upstream may already have done
work even when no downstream output exists. Replays can duplicate upstream
work or billing; no exactly-once upstream execution guarantee is claimed.

# Deadline boundary

No deadline extension/reset or shorter production per-open timeout is added.
If a first timeout consumes the 60-second allowance, there is still only one
open. The existing stalled-loopback test proves that case with a test-only
0.15-second deadline: one connection, zero retries, terminal handled marker.

# Verification

[Before](evidence/before.txt): 10 tests, 1 failure and 5 errors against unchanged
production code. Typed timeout->success failed; mixed HTTP/timeout stopped at
the second attempt instead of the shared exhaustion boundary.

[Regression](evidence/regression.txt): 99 exact-file tests pass. New tests use
real loopback sockets (no real provider/model) through production native
Responses passthrough and `_handle_provider_responses_route`/SSE error writer:

| Failure | Setting | Actual upstream hits | Downstream |
| --- | --- | --- | --- |
| typed header timeout then success | retries2 | 2 | HTTP200, one header block, completed body |
| typed header timeout always | retries2 | 3 | HTTP504, one error event |
| typed header timeout always | retries0 | 1 | HTTP504, one error event |
| exact reported HTTP504/SSE then success | retries2 | 2 | HTTP200, one header block |
| exact reported HTTP504/SSE always | retries2 | 3 | HTTP504 |
| exact reported HTTP504/SSE always | retries0 | 1 | HTTP504 |

Bare and wrapped timeout, JSON/SSE504, default/cap/0, mixed allowance,
cancellation before/during backoff, reselection with the same budget, deadline
exhaustion, handled/exposed guards, accepted-body timeout and other-provider
behavior are covered. Existing HTTP error presentation is unchanged (upstream
SSE text may remain inside the final message); no new envelope conversion was
added. Retry waits are injected in tests, but socket timeouts and HTTP status
capture are real. TLS handshakes and the user's remote incident were not run.

The runner strips all inherited CIEL_RUNTIME_* and client variables, sets a
private HOME/config plus free router port, and enables CIEL_RUNTIME_TEST_ISOLATED.
Full test groups were not run while live routers are present. Ruff and diff
whitespace checks pass.

# Delivery

Source and local loopback validation only. No commit/push, npm installation,
remote changes or live router/CLI restart were performed for this request.
