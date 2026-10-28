---
type: Diagnostic Investigation
title: Compact idle-timeout retry
description: Evidence-backed investigation of a compaction stream timeout and subsequent retry behavior.
tags: [compaction, retry, idle-timeout]
timestamp: 2026-10-27T03:30:00Z
---
# Scope

The user screenshot shows `/compact` failing with `API Error: Stream idle timeout — no chunks received`, followed by a Work band with unfinished items. The fix must identify the exact failure state and prove a subsequent retry succeeds without replaying an uncertain request.

# Status

Exact native idle-timeout recovery is implemented and source actual-CLI verification passes. Integration/nightly installation remains pending. Other unobserved error forms are not claimed supported.

# Evidence

The captured screenshot is the user's supplied image in this conversation. Source and isolated runtime evidence will be added here after the failure and retry are reproduced.

# Confirmed source behavior

Existing installed baseline `evidence/repro/run-baseline-224217/result.json` stays compacting after the native idle error and makes one compact request. Fixed `run-fixed-224123/result.json` makes two total compact requests, one bounded retry after 30 seconds, then reaches verified only after a real native checkpoint and measured shrink. Both include PNGs; neither changed protected live router PIDs.

A further actual production-launcher/SSE proof [result](evidence/repro/run-shared-source-232256/result.json) queued a CloudEvent while compaction was in flight, observed the native idle error, automatically retried compact once, verified checkpoint/shrink, then delivered the original queued event exactly once with a durable replied status. [SSE reply screenshot](evidence/repro/run-shared-source-232256/04-queued-sse-replied.png). One queued-model request, two compact requests, phase verified, live_unchanged=true.

The timeout duration differs between CLI runs (earlier 600 seconds; shared run approximately 300 seconds). The harness waits for the native failure, not a guessed timer. This is Claude Code 2.1.310 through a loopback CielAiRouter fixture, not live provider reliability proof. The screenshot #13 exact remote session remains unidentified; the reproduced same native error is proof of the local lifecycle defect, not identification of that session.

# Limits

Only exact captured manual/automatic native idle-timeout verdicts authorize this retry. Unknown/uncertain submissions never replay on age alone. Native error, handoff and corrupt state reasons are separately observable in the integrated source. Final package regression is still required.
