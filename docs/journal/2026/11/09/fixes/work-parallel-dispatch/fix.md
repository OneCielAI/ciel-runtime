---
type: Implementation
title: Fork dispatch, auto-fill, terminal release, orphan cleanup, side review notes
description: Code changes that let Work forks start, report and release slots, and stop main from repeating side answers.
timestamp: 2026-11-09T20:10:00Z
tags: [work-parallel, side-actor]
---

# Work parallel

- `work_parallel.py`
  - Leases gain optional `dispatch_name`, `owner {session_pid, identity}`, `report_hash` and `terminal`. Old ledgers stay readable.
  - New terminal states `unreported` and `orphaned` release the slot and keep the row for audit.
  - The duplicate guard now counts only `ACTIVE_STATES`, so a terminal lease no longer blocks a new one for the same item.
  - `terminate_unreported`: a positive `turn.complete` without a report.
  - `orphan_release`:
    - accepts a dead owner (process identity absent);
    - accepts a legacy lease only with explicit operator observations;
    - never releases a live or unknown owner.
- `work_parallel_mcp.py`
  - `reserve` and the new `reserve_next` take priority from the Work root, not from caller input.
  - Both mint a 256-bit worker token, store only its sha256, and return the token once.
  - Both also return the item note, path and worktree.
  - `work_parallel_report` authenticates by token alone, needs no session id, and revokes the token on result.
  - `reconcile_orphans` previews read-only, then applies with the preview digest.
- `work_plan_tree.runnable_leaves`
  - Lists approved, runnable leaves: focus first, then priority, then newest.
- `work_mcp_tools.py`
  - Serves `work_parallel_report` before the session-identity check, scoped to the lease's own board.
- `register.js`
  - The fork spawns with `cwd` set to the worktree and a structured assignment (exact title, note, path, worktree, lease, token).
  - On `turn.complete`:
    - a still-running fork becomes `unreported` and is stopped with TaskStop;
    - after a result and idle, the fork is stopped too;
    - its notification is consumed.
  - `parallelFillSlots` keeps reserving the next runnable item until no candidate or capacity remains. On the rate limit it retries after 15 s.
  - Session start binds a reservation to a live agent of the same name; otherwise it keeps the reservation.
- Menu
  - New row `work_parallel_orphans` in Work 병렬 실행 (en/ko/ja/zh):
    - shows the counts read-only;
    - `apply` releases dead owners;
    - `legacy <lease id>` releases one confirmed legacy lease.

# Side actor

- `side_intake_inbox.source_identity` maps `conversation` + `message_id` (from data, payload or message_context) to one source.
- `side_intake_processing.claim` binds one source to one actor. A later event for the same source:
  - waits while the first is open;
  - becomes `processed` with delivery `duplicate_source` once the first is processed.
  - The outbox never sends it.
- `side_work_decision.apply` records `side_reviews[receipt]` with the reply that was sent.
  - Handoff follow-ups are excluded, because main wrote them.
  - Finished reviews are capped at 50.
- `work_status` lists pending reviews. The prompt hook and one Stop block tell main:
  - do not answer or ACK again;
  - call `work_side_review(verdict=ok)`;
  - only if a reply was wrong, send one correction and record `verdict=corrected` with a note.
- `side_review.py` validates and commits review verdicts.
