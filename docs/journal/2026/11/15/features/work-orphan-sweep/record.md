---
type: implementation-record
title: Startup sweep of orphaned Work fork leases
description: Why parallel forks stopped in G:\ciel-Walkie, the manual release, and the launch-time sweep that now releases provable orphans automatically.
timestamp: 2026-11-15T07:45:00Z
tags: [work, parallel, orphan, lease, launch]
---

# Measured failure (2026-11-15)

No Work fork started in the G:\ciel-Walkie session (router 9505):

- Device fork slots: 9 in use against a capacity of 8. P1 had 4 in use against its limit of 3.
- Forks confirmed running in the native agent list: 0.
- The read-only orphan preview listed exactly 9 releasable leases. Every one had an owner that was positively dead or had no owner record:
  - 4 from this conversation: 3 P1 "running" leases reserved about 84 h earlier, and 1 P1 "submitted";
  - 5 from two other conversations.

Before this change, orphans were released only when a person previewed and applied the release (web, ctl, menu). Any crash, close or relaunch therefore left slots held indefinitely.

# Manual release (user-approved)

7 leases were released through the operator preview/apply path, one at a time, each pinned to its preview digest:

- the 3 dead-owner P1 "running" leases;
- the 1 dead-owner P5;
- 2 owner-less P2 leases of conversation `417ef30b…`, whose transcript was last written 2026-11-06 and which no running router serves.

The device went from 9/8 to 3/8 slots (P1 4 → 1). 3 leases were kept:

- the "submitted" P1 lease, which the ledger refuses (`unexpected artifact`) because its result waits for review;
- two owner-less reservations of the live F:\omini-router conversation.

# Change

`ciel_runtime_support/work_parallel_orphan_sweep.py` adds a `work_orphan_sweep` launch phase. It runs in `run_claude` after `router_start`, on every Claude start.

The sweep releases exactly the orphans the operator preview would release on its own evidence:

| Owner state | Released when | Proof / observation recorded |
| --- | --- | --- |
| `dead` | Always | The same proof as web/ctl: the owner process identity is absent. |
| `legacy_no_owner` | No running router serves the lease's conversation, read from `router-instances/*/compaction-scope.json` plus `router.pid`. | An unknown pid counts as live. |

The sweep keeps:

- live and unknown owners;
- any lease the ledger refuses.

A sweep failure never blocks the launch. It logs `work_orphan_sweep_failed`, and every release or keep is logged as one bounded line.

# Evidence

- `tests/test_work_parallel_orphan_sweep.py`: 3 passed. Coverage:
  - releases dead owners and unserved legacy leases, including removal of a never-bound reservation;
  - keeps live, unknown and served leases;
  - continues past a refused lease;
  - the launch phase never raises.
- Launch, architecture and parallel suites: 401 passed, 42 skipped.
- Real-process check in an isolated config: a lease owned by an exited process was released (`orphaned`, slot freed), and a lease owned by a live process stayed `running`. The live ledger was verified untouched.
