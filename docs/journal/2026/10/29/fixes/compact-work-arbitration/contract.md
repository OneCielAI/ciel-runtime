---
type: Fix Contract
title: Compact and Work liveness arbitration
description: Restore bounded automatic compact resumption and session-command admission without replaying unknown inputs or crossing approvals.
tags: [compact, work, lifecycle, recovery]
timestamp: 2026-10-29
---
# Scope

- Valid main auto shrinking compact checkpoint closes generation busy; later native user/assistant activity opens it normally. Tool ownership is separate and never cleared by a checkpoint alone.
- Safeguard command echo observed before submission receipt is buffered until confirmed or failed/uncertain, bounded to16records and1200seconds. No unconfirmed submission grants continuation authority.
- Protected unresolved wake remains owned after admission release; session-command polling can still reach its safe tool/turn/input checks. The old wake is not replayed or declared complete.
- Work main compact entry/settlement invalidate old Stop decisions; child/precompute events are excluded. Work no-progress counters do not consume compact waits. Stop status read has2second wall deadline; MCP Work status uses nonlocking read_snapshot.
- No runnable Work: existing Stop result passes unchanged, explanation only. Runnable Work: next approved step proposed only outside main compact. User focus/waits/approval remain authoritative.

# Evidence

Python152uniquefocusedtests pass (9new liveness regressions, repeated imported fixture classes removed). Native Work88pass/2samepreexistingbaselinefailures;19newcompact regressions pass. Actual isolated CLI withWork enabled passes: bounded terminal compact failure/retry, queued SSE admitted after failure release, new compact verified,3screenshots and zero owned foreground activation. Installed proof pending. No live or managed-host restart authorized or performed.

# Source acceptance

- [Python tests](evidence/python-tests.json):152pass.
- Native Work90cases:88pass/2preexisting failures, all19new lifecycle tests pass; no tests weakened.
- [Actual CLI](evidence/source-cli/verification.json): retry_failed_sse_retrigger_verified; one queued-input model request,4compact HTTP requests observed (includes native follow-up), live9505/9524unchanged. Only loopback fixture generation, owned egress proxy blocks external traffic; zero foreground window activation.
- User requested no-runnable Work release: already-native result is preserved; Work does not clear approval/waits/focus or another guard. Main compact suppresses ordinary Work stopped notices and no-progress accounting; native restart cleans lifecycle state.
- Screenshot15 is an additional Agent launch attempt's WorktreeCreate failure. That attempted child did not start. Existing authorized Work mod agent completed two files; no worktree isolation bypass or product-runtime claim made.
