---
type: Diagnostic
title: Late SSE pre-output commit reasons
description: Screenshot 7 HTTP 504 skipped replay without retained wire shape; add redacted fixed reasons to identify the exact pre-output branch.
tags: [retry, sse, cielairouter, diagnostics]
timestamp: 2026-10-27
---
# Confirmed evidence

Screenshot #7 transcript error at 22:20:00.827Z correlates to router 9524 at local 17:20:00. The router loaded 7792965; relevant retry modules are unchanged in the source. Degraded discovery sent the request to the central URL at 17:17:54, HTTP 200 arrived at 17:18:21, and an SSE error event without generation was relayed at 17:20:00. Only one upstream request; no `late_sse_retry`, `late_sse_stopped` or transient retry/stop marker.

The original frame bytes and response Content-Type were not retained. Therefore the exact bypass branch is unknown. Possible branches are: guard not armed (non-event-stream content type), multiple/trailing frames, strict JSON shape rejection, terminal marker, byte bound or EOF.

# Change

Diagnostic only. Replay rules are unchanged: an unknown, mixed, partial, terminal or non-event-stream response still relays and does not reopen. The router now records `cielairouter_preoutput_guard state=armed|not_armed` and `cielairouter_preoutput_commit reason=` with fixed reason codes, held byte count and attempt/open counts. It does not log payload, URL, header values or error messages.

# Verification

[Focused late SSE tests](evidence/focused-tests.txt) 18 pass, including integer `code`, extra `status`, a data-only ping, a DONE/trailing frame, a terminal credential-like message, the byte bound, EOF and missing/JSON/unknown content type, with no payload leaks in diagnostics. All `test_transient_retry*` (78) and 5 adjacent CielAiRouter retry modules pass.

`test_native_anthropic_does_not_install_semantic_start` [fails with a timeout both with and without this diff](evidence/baseline-native-liveness.txt), so it is an existing local failure unrelated to this change; not claimed fixed.

This does not fix the 504. The next occurrence after installing this version gives the decisive branch without retaining sensitive response bodies.
