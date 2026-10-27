---
type: Diagnosis
title: Edward native background handoff strands SSE input
description: Read-only evidence connects native continued-in handoff to the parked socket and frozen delivery transcript; repository fix and release are in progress.
tags: [edward, sse, background, session-socket]
timestamp: 2026-10-27T02:00:00Z
---
# Confirmed

- .14:1004 Edward u22: runtime 5054af2, Claude Code 2.1.311; launcher pid464, router5704 port9397, original interactive Claude13456 ([p1](evidence/p1.raw.txt)).
- 17:39:03 PDT: original transcript `354281f4-a695-46c2-a0db-450d681953b8` writes `continued-in` naming `b1e17ad1-6afd-4210-86e4-5e327c749e3b` ([p4](evidence/p4.raw.txt)). The original's published pid record carries `parkedJobId: b1e17ad1`; live bg pid15712 has that exact `jobId`, new session id and its own messaging socket ([p5](evidence/p5.raw.txt)).
- Conversation continues in the bg transcript. After the handoff the original only enqueues socket input without processing it. All post-handoff inputs #16349-#16376 time out as `stale_unconfirmed` ([status](evidence/p2.raw.txt), [transcripts](evidence/p3.raw.txt)). The earlier pre-handoff timeouts are a distinct pattern, not attributed to the fork.
- Production socket client stores a single configured target and keeps writing there. Compaction binding matches only that original socket; transcript discovery returns its bound path without following native handoff. Thus external input goes to the parked original rather than the live continued conversation.
- This is not the Kobe startup-gate pattern: Edward successfully submits bytes and the original native transcript records enqueues, but never handles them after the handoff.

# Limits

Who initiated backgrounding is unknown. No remote installation, restart, queue/cursor/config change or test message was performed. The user was informed that evidence capture was complete and they could restart. Session registry tokens and message bodies were not printed.

# Improvement in progress

Resolve only explicit native `parkedJobId` -> unique `kind:bg`/`jobId` links, validate cwd/domain/process identity/key, and use the same successor for socket transport, transcript confirmation and compaction admission. Incomplete or ambiguous handoffs hold input instead of falling back to the parked session or TTY. Never replay already-failed inputs merely because identity changed.

# Citations

- [Official tracker issue 87984](https://github.com/anthropics/claude-code/issues/87984): same background fork, continued-in and parkedJobId/jobId field shapes. Corroboration only; local remote evidence above establishes this incident.
