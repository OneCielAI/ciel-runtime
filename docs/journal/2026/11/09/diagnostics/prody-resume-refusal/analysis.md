---
type: Incident Analysis
title: Prody deferred worker resume was rejected
description: Read-only evidence for why SendMessage could not resume three interrupted Work workers.
timestamp: 2026-11-09T23:01:42Z
tags: [work-parallel, prody, resume, claude-code]
---

# Observation

Prody's running session was inspected read-only. The persisted parallel ledger and the Claude transcript agree on these affected leases:

| Work item | Lease | Worker id | Exact persisted agent name | State |
|---|---|---|---|---|
| n335 | `6be14eb5af…` | `ab10db85a703d815e` | `work-auto-mw9u0z1s-nop40mpe8gp` | running, slot held |
| n343 | `28ee0ed251…` | `abf1e8f87d7740a61` | `work-auto-mw9uoj2k-49hzf5kwo6n` | running, slot held |
| n344 | `3c4ce0c92b…` | `a75fa2a2339e18ed3` | `work-auto-mw9ubi7a-9a5r47dwj1b` | running, slot held |

At 23:01:12Z, 23:01:30Z, and 23:01:42Z, the main session called `SendMessage` with those exact worker IDs. Each deferred restart spawn was rejected with:

> no pending work_parallel_run dispatch matches Agent name …

The same three failures recurred at 02:01Z. The persisted names in the ledger exactly match the names printed in the rejection. This rules out a stale or mismatched agent name.

# Proven cause

`register.js` authorized recovery by adding the durable lease id to `parallelResuming` immediately before `await next(e)` in the `SendMessage` hook. A `finally` deleted it as soon as `SendMessage` returned.

Claude Code emits the restarted worker's `agent.spawn` after that `SendMessage` call returns. Therefore `parallelAgentSpawn` observed neither an entry in `parallelPending` nor the already-deleted recovery authorization, and routed the spawn through `parallelDeny`.

This is a temporal lifetime defect in the in-memory authorization. It is not a capacity error and not a ledger corruption.

# Separate completion gate

At 00:13:47Z, marking n335 done was rejected with `Work completion waits for parallel validation, review and cleanup.` That result is consistent with the three falsely-running leases above. It is a consequence of the resume failure, not a second observed root cause.

# Evidence

- [SendMessage results and recovery contexts](evidence/sendmessage-results.txt)
- [Affected persisted lease rows](evidence/affected-leases.json)
