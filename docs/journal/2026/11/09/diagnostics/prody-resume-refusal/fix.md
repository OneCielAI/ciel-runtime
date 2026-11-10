---
type: Implementation
title: Keep exact recovery authorization through deferred spawn
description: One-shot authorization now survives SendMessage return and is consumed by the exact deferred worker spawn.
timestamp: 2026-11-10T04:20:00Z
tags: [work-parallel, resume, authorization]
---

# Change

`parallelResuming` is now a map from the durable lease id to the exact persisted agent name.

1. The main `SendMessage` hook finds the lease by the exact persisted worker id and records `(lease id, agent name)` before forwarding the call.
2. A definite synchronous denial clears the authorization.
3. A later `agent.spawn` is admitted only when all existing recovery guards still hold:
   - the durable row is running, slot-holding and bound;
   - its persisted `agent_name` equals the requested spawn name;
   - the one-shot map contains that same lease/name pair;
   - parallel execution is not paused;
   - it is a top-level `fork` spawn from the engine.
4. The matching spawn consumes the map entry before forwarding. A duplicate spawn is denied.

No new reservation is created, so the same Work item cannot run twice under this path.
