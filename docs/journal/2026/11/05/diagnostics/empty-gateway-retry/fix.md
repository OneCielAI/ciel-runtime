---
type: Fix
title: Empty gateway response retry gap
description: Reproduce and repair bounded pre-output empty HTTP gateway retries.
tags: [cielairouter, retry, http502]
timestamp: 2026-11-05
---

# Evidence

User reports repeated API Error502 no body on this session9524. Router log has upstream_direct_http_error code502 at13:53:46 plus typed timeout retries at13:41/46/51. These observations do not prove every user-visible502 had the same cause. Source transient_retry next_wait required strict structured body through _retryable_verdict; empty body always failed. Empty BytesIO error was also classified incomplete by _read_error. New exact empty502 test failed before fix.

# Fix

Allow complete empty real HTTP502/503/504 before output under existing request budget/backoff/cancellation. No empty429/auth/400/500 expansion, no partial framing or post-output replay, no generic SSE error widening. Empty in-memory stream is known complete. DNS/timeouts and structured errors retain existing behavior.

# Verification

82 existing focused retry tests pass plus two new loopback/policy tests. Actual loopback HTTP server returns Content-Length0 status502 then200: hits2, retry1, bodyok. Native provider traffic not used. a187afa CI42099924738/Publish42099924787 success. npm201239.a187afa exact head verified and installed; actual installed loopback test again502empty->200 hits2retry1 passes. New immutable snapshot copied, smoke passed, persistent User launcher pin updated; exact wrapper version matched. Active live router not restarted or overwritten.
