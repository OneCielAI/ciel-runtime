---
type: Diagnostic
title: REGINA compaction and notification delays
description: Queue receipt continued while compaction admission held delivery; a relaunch grace expiry released five events before another compaction started.
tags: [regina, sse, compaction, admission]
timestamp: 2026-10-23
---

# Evidence and time scope

Host100.64.0.25, regina-ai sandbox, read over sjglobal SSH with sudo -u regina-ai.
Observed installed dfcf41a at19:01UTC. No configuration, history, queue, cursor,
process or remote file was modified. The delegated collector failed after
writing evidence due to an unrelated local gateway reset; the parent inspected
its saved evidence and independently rechecked current state.

# Verified timeline (UTC)

-18:21:11: old8801 admission paused, phase=compacting; later context_recovery
 deferrals logged through18:53:29. Compact attempts failed upstream (504 and
 later502/rate-limit combinations), some at completed_chunks=0.
-18:25:03,18:39:35,18:42:11: inputs312-314 persisted despite no delivery.
-18:54:25 through18:56:54: inputs315-319 persisted locally.
-18:56:04: old child exits after terminal control inputs;18:56:36 new8802 launch.
 Launch fast-forward moves311->314 (distinct behavior, not successful delivery).
-18:56:41: resumed session identity bound; first-request identity hold active.
-18:58:41: request_identity_expired opens delivery after120seconds; five inputs
 315-319 submitted together via session_socket (23591bytes).
-18:58:43: native compaction starts again; admission paused phase=compacting.
-18:59:34 and19:01:13: compact fails0/19 chunks with upstream failure.
-19:06:22: new native checkpoint plus smaller request observed; gate becomes
 verified, evidence=next_request_shrink. Parent read at19:06:51 confirms state.

# Conclusion

This observed delay is downstream admission/delivery, not complete absence of
SSE receipt: messages were already persisted before delivery. Earlier delay is
associated with the logged active compaction recovery gate. The five-message
burst specifically occurs when the new launch identity120-second grace expires,
not when compaction finishes. Another compaction starts two seconds afterwards.
Thus the reported burst-then-compact sequence has two separate gates/episodes;
it must not be described as a single successful compaction releasing all input.
Submission to a socket is not proof every input was answered. Inputs312-314
were fast-forwarded on relaunch, not confirmed delivered.

# Current boundary

The current8802 gate was verified at19:06:22, but that does not prove all later
notifications or the historical backlog were consumed. Repeated upstream
compaction failure prolonged the earlier hold; its rate-limit/timeout causes
must not be conflated with the separate local9489 fixed60-second retry budget.
