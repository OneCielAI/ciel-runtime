---
type: Architecture Decision
title: Event-driven Work fork lifecycle
description: Host-owned per-priority scheduling, exit contracts, handoff continuation and restart recovery without main-context scheduler traffic.
tags: [work, parallel, scheduler, fork, handoff, recovery]
timestamp: 2026-11-10T00:00:00Z
---

# Coordinator boundary

The main model receives Work-board outcomes only. Native agent observations, slot refill, reviewer admission, launch retry, exit recovery and routine lease state remain inside the packaged host.

# Event-driven scheduler

`parallelPump` is serialized with a pending-again latch. It runs after:

- session start;
- a successful worker report or lifecycle event;
- any subagent `turn.complete`;
- a detected board-version change;
- a current `classic.Stop` after stale-lifecycle guards.

There is no periodic refill timer. Only launch-rate admission arms a one-shot 15-second retry. Each priority retains its configured independent slot limit; submitted reviews and handed-off continuation are admitted before fresh work.

# Exit contract

A fork is not stopped merely because a native turn ended. The host requires a persisted completion, review or handoff receipt. A missing receipt causes the host to resume the exact fork through `$.session.send({to:{agentId}})` up to the bounded attempt count. Only a persisted host-salvage receipt permits `unreported` release and TaskStop.

Structured handoffs contain bounded done, remaining, blockers, verified workspace metadata and the next step. The body remains in the host coordination sidecar and is passed directly to a continuation fork, never through main.

# Restart recovery

The host coordination sidecar records item/root, lease/generation, agent identity, workspace, native status, receipt references and recovery attempts. On session start, the host rebinds live rows or resumes the recorded agent directly. Recovery instructions are no longer appended to the main prompt.

# Approval identity

Global board CAS version is not execution approval. Each assignment pins its root's approval generation and semantic scope digest. Unrelated Work updates do not invalidate an in-flight fork; title/note/tree scope changes do. Progress narrative belongs in the phase-preserving `progress` field.

# Related concepts

- [Mixed-build ledger compatibility](/compatibility/mixed-build-ledger.md)
- [Focused verification](/verification/focused-tests.md)
