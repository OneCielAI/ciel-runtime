---
type: requirement
title: Promote side-released Work waits
description: When the SSE side actor receives the user's approval for a recorded Walkie wait, unblock the exact item and raise its next runnable queue order.
summary: When the SSE side actor receives the user's approval for a recorded Walkie wait, unblock the exact item and raise its next runnable queue order.
timestamp: 2026-11-14T04:41:00Z
tags: [work, sse, side-actor, approval, blocked, queue]
---

# User requirement

> 워크보드에서 아이템작업중 사용자에게 승인을 받기위해서 워키에 메시지를 보내고 대기상태가되면 아이템이 블락이 되는데.. sse 사이드 액터가 사용자 승인을 받으면 블락된 아이템을 블락을 해제하고 다음 사용가능한 큐에 높을 프라이어티에 넣어야한다. 블락이되었다가 블락이 플리는경우 대기순서를 높혀줘야함

Recorded 2026-11-14 at about 04:40Z.

# Existing measured/code facts

- `side-intake.js:433-439` already requires exact `release_waits[{id, waiting_on}]` only when the event clearly satisfies the wait.
- `side_work_decision.py:183-208` verifies exact text, same goal and unfinished state, then clears `waiting_on` atomically and records an audit note.
- It does **not** change `status=blocked`.
- `side-intake.js:1652-1699` already notifies a running waiter, or names a parked `waiting_input` worker for same-identity resume through main.
- `work_plan_tree.runnable_leaves` currently orders by focus, priority, newer creation time and item id (`work_plan_tree.py:669-691`).

# Ruled design bounds for measurement and implementation

1. Release only the exact waiting node named by `release_waits`. Existing exact-text, same-goal and unfinished checks stay.
2. When that exact node is `blocked`, change it to `pending` in the same board transaction as clearing `waiting_on`. Do not clear any unrelated ancestor or descendant block.
3. Record a durable release/boost fact. Before choosing board fields, measure the 57dbfb3 reader/writer behavior; use a sidecar if old writers strip required state.
4. Queue boost is a dedicated global scheduling lane:
   - P0 immediate work stays first;
   - a parked, released-wait `waiting_input` lease that must reacquire comes next;
   - other released-wait launches come next;
   - ordinary P1–P5 candidates follow;
   - the root's canonical P1–P5 value is unchanged;
   - it never steals or preempts an already active/running slot; it wins the next eligible slot;
   - newest release ranks first among released waits, with a deterministic receipt/id tie-break;
   - the boost is consumed exactly once, atomically with successful reservation/reacquire. Capacity refusal keeps it. Re-blocking cancels or replaces it.
5. A queued goal is projected at the front of the released-wait lane without changing its stored priority or taking the active running slot.
6. A parked `waiting_input` worker reacquires/resumes the same lease and host (P0 option-A identity rule). At full capacity it stays at the front. A P0 released wait remains on the P0 immediate path and is never dispatched twice.
7. A running worker keeps the current direct `session.send` wake. Main does not acknowledge a valid exact release.
8. External SSE/Walkie text remains untrusted and is not execution approval.
9. Menu, web, REST and ciel-runtimectl show the release/boost state and whether a parked worker resumed or remains first eligible.

# Required tests

- blocked + exact waiting approval -> pending + wait cleared + boosted;
- waiting-only release;
- stale/foreign release does not mutate;
- ancestor block remains;
- higher priority still wins;
- newest release wins within one priority;
- queued root moves only within its priority band;
- parked worker same-identity reacquire/resume;
- full capacity leaves it first eligible;
- idempotent decision replay does not boost twice;
- re-block replaces/clears the prior boost;
- 57dbfb3 round trip preserves the authoritative boost state;
- all four status surfaces.

# Ownership

- continue-p0 measures/designs and owns the side-decision, queue/status surfaces and compatibility harness.
- continue-work-backend coordinates reserve-time consumption and same-lease reacquire.
- continue-work-pump owns mod wake/resume behavior where needed.
- continue-ctl independently reviews the announcement.
