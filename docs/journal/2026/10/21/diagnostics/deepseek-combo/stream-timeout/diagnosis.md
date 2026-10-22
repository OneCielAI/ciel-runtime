---
type: diagnosis
title: Combo stream timeout and reasoning rejection
description: A local post-acceptance socket timeout leak is reproduced independently of DeepSeek reasoning-history validation errors.
tags: [cielairouter, deepseek, timeout, responses]
timestamp: 2026-10-21
status: reproduced-not-fixed
---

# Screenshot facts

HTTP 502, upstream_stream_truncated, underlying TimeoutError: timed out,
model _Walkie_DK41-1017-Sonnet55-Sol61, attempts=1, URL 127.0.0.1:9179/v1/responses.
This establishes neither an internal DeepSeek route nor zero upstream output.
Port 9179 had no local listener during investigation; one health GET was refused.
Exact screenshot host/runtime configuration remains unidentified.

# Reproduced local defect

Responses opener clips its socket timeout to the shared retry budget. On
successful HTTP 200 acceptance the buffered stream reader retains that clipped
timeout instead of the caller-configured normal response timeout.

Owned loopback fixture: immediate response.created, completion 0.25 seconds
later; configured timeout 1 second, injected budget 0.1 seconds. One HTTP open,
no retry: accepted socket timeout 0.1 seconds, stream failure at 0.109 seconds,
UpstreamStreamReadError mapping to 502. Normal budget, retry disabled and generic
provider controls succeed. Unmodified production budget 60 seconds and configured
120 seconds leave accepted socket timeout at 60 seconds. The short-budget probe
reproduces the failure mechanism but does not prove the screenshot incident's cause.

See evidence.json and stdout.txt; source hashes remained unchanged.
Sources: provider_responses_passthrough.py _open_checkpoint_request and
_forward_buffered_stream; transient_retry.py bounded_timeout and preflight.

# Separate reasoning rejection

The exact Celly sandbox logs include 2026-10-21 17:20:42 UTC-05 HTTP 400 naming
deepseek/deepseek-flash-max and missing reasoning_text. This is not the screenshot's
502. DeepSeek official thinking-mode documentation requires reasoning_content
passback for requests carrying tools and states omission produces HTTP 400.
The combo gateway's internal reasoning translation and route are not in this
repository; no live gateway wire body or internal route was inspected.

# Boundaries

Diagnosis only: no production source/config changes, provider replay, live
session restart, queue/cursor/history mutation, or feedback submission. The
pre-existing feedback-menu working-tree changes belong to another task.

# Citations

- https://api-docs.deepseek.com/guides/thinking_mode
- evidence.json
- stdout.txt
