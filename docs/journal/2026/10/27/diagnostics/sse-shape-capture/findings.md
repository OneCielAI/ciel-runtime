---
type: Diagnostic
title: Redacted structure capture for recurring 504
description: The router now records enough safe shape to explain why an SSE 504 was or was not retried.
tags: [retry, sse, diagnostics]
timestamp: 2026-10-27
---
# Purpose

The live recurring504 error showed only `text_len=0` and `message_start=False`, so the retry-skip branch could not be determined from older logs.

# Capture

Without logging message text, request bodies, URLs, credentials, raw header values or unknown upstream strings, the router records:
- pre-output guard state and fixed commit reason;
- held prefix complete-frame count and the first/last two frames;
- recognised event names, allowlisted JSON field names and value kinds;
- recognised error.type tokens only, plus message character length;
- relay response media class, wrapper class and the guard reason/operation id;
- explicit fixed log when retries are disabled for CielAiRouter.

# Verification

- [Tests](evidence/shape-tests.txt), [retry tests](evidence/transient-tests.txt), [relay tests](evidence/relay-tests.txt): all pass.
- [Actual isolated Claude Code](evidence/result.json): integer `error.code` 504 remains non-retryable by policy; screen shows the API error, one main request, no retry. [Screenshot](evidence/01-outcome.png). [Logs](evidence/router-retry-lines.txt) identify `reason=unrecognized_frame`, `code:number`, `type_token:api_error` and correlate guard/relay by operation id. Live routers unchanged.

# Boundary

This captures the next live occurrence once a router runs this code. It does not prove the cause of earlier failures or change retry acceptance.
