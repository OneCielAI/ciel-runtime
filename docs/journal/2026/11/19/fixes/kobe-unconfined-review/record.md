---
type: fix
title: KOBE main review refused every result — forks reserved without a workspace could never be reviewed
description: KOBE's session folder is not a git repository, so every fork is reserved with no workspace (allowed by design). The review path still required a workspace snapshot, so all 46 submitted results were refused with "positive idle workspace observation required before main review" and no review was ever published. A result with no workspace now publishes a review whose workspace is null; approve finalizes it, rework resumes without a workspace, and the restart path publishes it too.
timestamp: 2026-11-19T21:00:00Z
tags: [work, parallel, review, finalization, kobe, hotfix]
---

# Problem (KOBE, measured read-only)

Evidence: [evidence/measure/kobe-ledger-and-transcript.txt](evidence/measure/kobe-ledger-and-transcript.txt).

- The main session called `work_parallel_item_review` three times (n906 at 13:40Z and 15:33Z, n922 at 19:53Z). Every call failed with `positive idle workspace observation required before main review`. ✅
- KOBE's session folder has no `.git`, and none of its parent folders has one either. ✅
- All 94 leases have no workspace. 46 are `submitted`. There is no finalization journal, so no review was ever published. ✅
- The side actor's review notice asked main to review, and main's review was refused. That loop repeated. ✅

# Cause (source)

- Reservation: `work_parallel_mcp.py` `_reserve_assignment` attaches a workspace only when the host sends `workspace_repo`. register.js sends it only when `$.session.repo()` is true. Otherwise the lease is unconfined (`_host_workspace_path` documents this as allowed).
- Review: three places required a workspace. `git log -S` shows all three messages came in with 34966b6 (2026-11-12). The `not lease.get("workspace")` condition first appeared in aa06216 (2026-11-10).
  1. `_observe_idle_and_publish_review` (`lease.get("workspace") is not None`). The idle reconcile answered `review_publication_error: submitted idle review observation required`, so nothing was published.
  2. `_review_facts` (`not lease.get("workspace")`). This raised the error KOBE showed.
  3. `_restart_submitted_terminal` (`submitted restart lease required`). After a restart the result was not published either.
- The finalization journal validator `_workspace` accepted only a full snapshot.

# Fix

- `_observe_idle_and_publish_review`: for a lease with no workspace, the ledger's positive idle proof is the observation. No worktree write. The lease must stay unconfined while the proof converges.
- `_review_facts`: with no workspace, the review carries `workspace: null`. A receipt that reports a workspace for an unconfined lease is refused as stale.
- `_revalidate_review`: a review with no workspace stays valid only while the lease still has no workspace. If either side changes, it is refused as `stale workspace`.
- Approve: the cleanup proof is `no workspace: the fork ran without a git worktree; nothing to retain or remove`.
- Rework: there is no `handoff_rebind`. The replacement resumes with no workspace, its coordination assignment has `workspace: null`, and the dispatch carries `workspace: null`.
- `_restart_submitted_terminal`: no longer requires a workspace.
- `work_parallel_finalization._workspace` accepts `null`. `_same_snapshot` treats two nulls as the same and null against a snapshot as different.
- The status projection, web REST (`_public_reviews` skips a non-dict workspace) and register.js renderers (`r.workspace?.…`) already tolerated a missing workspace. No change was needed there.

## Compatibility

- The journal schema stays 5. Only rows from unconfined leases carry `workspace: null`.
- A build older than this fix would refuse a journal that holds such a row, because its validator requires a dict. Before this fix no such row could exist, and KOBE has no journal at all.

# Verification

- Fail-before: the 6 new tests run against HEAD 0e0cb7e source. All 6 fail. Two fail with KOBE's exact message, and the restart test fails with `submitted restart lease required`. See [evidence/tests/fail-before-HEAD-0e0cb7e.txt](evidence/tests/fail-before-HEAD-0e0cb7e.txt).
- Pass-after and the regression suites are recorded in [log.md](log.md).

# Not covered here (observed, not changed)

- `host_salvage` still refuses a lease with no workspace (`unknown salvage workspace`, `work_parallel_mcp.py`). Whether this leaves KOBE's unreported forks unresolved has not been measured.
