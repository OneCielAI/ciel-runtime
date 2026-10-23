---
type: Fix
title: CielAiRouter usable timeout retry budget
description: The previous typed-timeout fix still exhausted its aggregate deadline on its first production open; default budget duration now derives once from the actual opener timeout.
tags: [cielairouter, retry, deadline, timeout, responses]
timestamp: 2026-10-23
---

# Correction to previous fix

Actual local9489 running dfcf41a produced HTTP504 at13:18:02. Its log explicitly
records `kind=transport_timeout attempts=0 opens=1 elapsed=60.36s`.
The prior patch made typed timeout eligible, but left the fixed60-second
aggregate allowance. `bounded_timeout` clipped the configured300-second caller
timeout to60, so no retry could start. Earlier short-timeout loopback tests
passed but did not represent this default-duration interaction. The patch was
therefore insufficient for this observed failure.

[Actual incident log](evidence/incident-9489.txt).

# Change

In `transient_retry.py`, scopes created by `request_scope` mark their budget as
awaiting first resolved timeout. At the first `bounded_timeout` call only:

- For finite positive timeout T, reserve max(existing60,
  (retries+1)*(T+preflight0.1)+retries*maximum_wait30).
- Keep the original start time. No resets on failure/reselection or later calls.
- Explicit supplied budgets retain exact deadlines. Invalid/unbounded input
  keeps the original finite fallback and cannot be expanded by a later call.
- Already expired/stopped/exposed budgets do not expand. Derived nonfinite
  arithmetic is rejected in favor of the finite fallback.
- Preserve caller T (subject to genuine remaining aggregate capacity), cap10,
  explicit0, cancellation, error/exposure guards and accepted read-timeout
  restoration. A later gateway timeout cannot enlarge the established budget.
- Log resolved requested timeout and aggregate deadline with retry diagnostics.

With timeout300 and retries3 the allowance is1290.4seconds, rather than60.
This is finite but can keep an unanswered operation pending substantially
longer; cancellation is checked between synchronous opens, not a forced
interruption of a blocked DNS/socket call. Existing timeout/retry controls
remain the only settings. Possible duplicate upstream work/billing remains.

# Verification

- [Before](evidence/before.txt): default300 test raises on first timeout; direct
  timeout assertion observes60 rather than300.
- [Regression](evidence/regression.txt):106 exact-file tests pass, including
  default-scope300-second fake-clock timeouts, all configured attempts1/3/10,
  immutable first-timeout policy, invalid fallback, explicit short deadline,
  cancellation, exposure, accepted read timeout, shared HTTP/timeout allowance.
- A real socket fixture with scaled old threshold0.05 and actual0.15 open
  timeout passes through the default production scope and retries successfully;
  no injected budget is used.
- [Long probe](evidence/long-timeout.txt): real300-second socket timeout through
  production Responses passthrough completed after300.016seconds:2 opens,
  1 retry, aggregate deadline630.2seconds, HTTP200 and exactly1 status block.
- Exact typed503 skipped-target JSON/SSE and initial200/SSE are retryable.
- Internal semantic summary calls now recognize structured pre-output CielAiRouter
  502/503/504/529 provider failures as bounded failed-chunk retry candidates,
  while output-started/terminal/plaintext failures remain terminal. 131
  compaction/control tests pass.
  Untyped/plaintext503 remains terminal. Existing integration covers Messages
  opener and native Responses loopback. Screenshot text alone does not prove
  the actual raw error format or exhaustion count.

# Emily .14 boundary

User identified one screenshot as Emily sandbox. Historical log path8966 under
user aapw-a11u4 was identified, but both plain and encoded read-only SSH commands
returned `exec request failed on channel0`. Its current version and incident
logs are unavailable. No claim this change fixes Emily's503 is made; no remote
settings, sessions, files, history, queues or cursors were modified.

# Delivery

This followup is uncommitted source plus isolated verification. No new publish,
local install or live restart was performed.
