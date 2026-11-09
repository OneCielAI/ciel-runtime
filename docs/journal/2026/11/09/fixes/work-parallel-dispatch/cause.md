---
type: Incident Analysis
title: Work forks never started, dead leases held slots, side answers repeated
description: Why Prody's P1 Work did not run in parallel and why one Walkie question got two replies.
timestamp: 2026-11-09T20:10:00Z
tags: [work-parallel, side-actor, prody, walkie]
---

# Work parallel (Prody, read-only, 18:27–18:31Z)

| Defect | Evidence |
|---|---|
| Host spawn reserved the item twice | 04:31:05Z `work_parallel_run n231 P1` → `spawn_denied … no eligible candidate`. `register.js` reserved first, then `$.agent.spawn` ran ciel-work's own `agent.spawn` hook, which reserved the same item again. The fix in `ac88bfd` was never released; 503be09's `register.js` is byte-identical to 13c6f4a. |
| Dead leases hold slots | Ledger v19: P1 `n527`, `n528`, `n534`, scope `4d24be21` (2026-11-04), `running`, slot held. That session has no process and no board file. Under `p1_reserved 5`, non-P1 work could start only while fewer than 1 slot was active (05:36:51Z P2 `n245` refused). |
| Worker reports had no identity | Owned run `owned-fork-lifecycle-20261108-221039`: a plugin-spawned fork's tool call skipped ciel-work's own hook, so `work_parallel_event` arrived without a session and failed with `conversation identity is unavailable`. |
| Finished forks kept their slot | Same owned run: the finished fork stayed `running · slot held`; a lease not marked `done` also blocked re-dispatch of its item. |
| No automatic dispatch | Only an explicit `work_parallel_run` started a fork; none after 05:36:51Z. |

# Side actor answered twice (Prody, Walkie 119649)

| Time (Z) | Event | Side result |
|---|---|---|
| 18:40:17 | `task.auto_assigned` for 119649 | status answer, submitted (#119651) |
| 18:40:23 | `message.created` for 119649 | status answer, submitted (#119652) |
| 18:39:55 | `task.status_changed` for 119485 | status answer, submitted |
| 18:42:24 | `agent.pending_reminder` (no message id) | status answer, `route_unavailable` |

The inbox keyed receipts by event id, so every event about the same source message started its own actor. Main received no record of what the side had already sent, so it could not tell a handled request from an open one.
