---
type: fix
title: A worktree created before its manifest's final save completes on retry
description: When workspace creation was cut between creating the origin worktree and the manifest's final save, every later prepare refused the manifest and the whole P1 goal kept "workspace preparation failed and its folder was kept". prepare now completes that manifest.
timestamp: 2026-11-20T19:20:00Z
tags: [work, parallel, workspace, worktree, manifest, andy]
---

# Report

Observed on Andy (monitoring ticks 18:00Z–18:37Z, [log](/docs/journal/2026/11/20/operations/live-monitoring/log.md)):
- Goal n1445 is stuck at "workspace preparation failed and its folder was kept". Its items n2452, n2453 and n2456 are blocked.
- The workspace manifest (manifest version 120) says `status: "preparing"`, `git_dir: null`, `host_id: null`.
- The worktree folder exists.
- What cut the creation is not recorded (11-19 17:42Z; the log segment before it rotated to NUL). The cause of the cut is unknown; the stuck state below is.

# Cause

Confirmed in source (0be8eec):
- `prepare` (`work_parallel_workspace.py`) verifies an existing manifest and refuses a `preparing` one, with no path to accept its own worktree.
- When the interruption falls after `git worktree add` and before the final manifest save, the manifest stays `preparing` with no git dir and no host while the folder is complete. Every later prepare fails the same way.

# Change (f932da4)

- `_complete_interrupted` accepts a manifest only if: status `preparing`, no `git_dir`, no `host_id`, not idle, and none of `ownership_lease_id`, `generation`, `prior_lease_id`, `handoff`, `removal` present. Its folder must exist (as a plain directory), and the folder's git dir must verify with the current head equal to `initial_head` and the stored head.
- `prepare` completes such a manifest in place (`git_dir` filled, status `ready`) and returns it. Anything else verifies as before.
- No model-facing text changed.

# Verification

[fail-first.txt](/docs/journal/2026/11/20/fixes/workspace-interrupted/evidence/fail-first.txt).

- Fail-first: `interrupted_after_add` fixture on 0be8eec, the same git worktree add leaving the same manifest: prepare refused it with "workspace preparation/removal incomplete; retained".
- After the change: `test_creation_interrupted_after_worktree_add_completes_on_retry` and `test_interrupted_worktree_that_moved_is_still_retained` pass in the board-refs bundle runs on Windows and Linux (351fa9a): 1616 tests each, 0 failures and no workspace error in either
  ([evidence/bundle-windows.txt](/docs/journal/2026/11/20/features/board-refs/evidence/bundle-windows.txt),
   [evidence/bundle-linux.txt](/docs/journal/2026/11/20/features/board-refs/evidence/bundle-linux.txt)).
  A worktree whose head moved is still retained, not completed.

# Installed

`work_parallel_workspace.py` is in the hotfix11 overlay installed with the board refs change on Andy, Celly, KOBE, Prody and Betty (hash 304BBD80EC7C1D66,
[install-local.txt](/docs/journal/2026/11/20/features/board-refs/evidence/install-local.txt),
[install-kobe.txt](/docs/journal/2026/11/20/features/board-refs/evidence/install-kobe.txt),
[install-aap12.txt](/docs/journal/2026/11/20/features/board-refs/evidence/install-aap12.txt)).
It applies at each router's next restart. Andy's n1445 manifest has not been touched; whether prepare completes it live is still to be observed after a router restart.
