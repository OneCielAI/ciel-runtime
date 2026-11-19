# Log

## 2026-11-19

- The user reported an error in the KOBE session (screenshot not viewable here). I read KOBE's transcript and found three `work_parallel_item_review` refusals: `positive idle workspace observation required before main review` (n906 twice, n922).
- KOBE ledger, read-only: 94 leases, none with a workspace, 46 submitted, and no finalization journal. The session folder has no `.git`.
- Source: `_review_facts`, `_observe_idle_and_publish_review` and `_restart_submitted_terminal` all required a workspace. A fork reserved without `workspace_repo` is allowed, but its result could never be reviewed.
- Tests first: 5 cases in `test_work_parallel_finalization.py` (publish after idle, approve, review with no prior publication, rework without a workspace, a lease that gained a workspace is refused) and 1 in `test_work_parallel_restart_terminal.py` (restart publication). All 6 fail on HEAD 0e0cb7e source; see [evidence/tests/fail-before-HEAD-0e0cb7e.txt](evidence/tests/fail-before-HEAD-0e0cb7e.txt).
- Fix in `work_parallel_mcp.py` and `work_parallel_finalization.py`. A review with no workspace carries `workspace: null`. See [record.md](record.md).
- After the fix the 6 new tests pass. ruff is clean. `ciel_runtime.py` is unchanged (4980 lines).
- Windows, 39 Work test files (work_parallel*, approval workflow, web UI, REST receipts, MCP tools, side queue): 654 tests, 2 subtest failures in `BrokenRetainedWorktreeTests`.
  - The failure was `test_legacy_review_removal_crash_points_converge_without_losing_a_review` (point "after sidecar save, before delete"): the rework decision ended `deciding` instead of `rework_ready`.
  - That test passes alone.
- Repeats of the `BrokenRetainedWorktree` group:

  | build | runs | failed runs | which test failed |
  |---|---|---|---|
  | fix | 8 | 2 | the subtest above, and once `test_recovery_saga_crash_at_every_stage_converges_on_replay` (stage `workspace`) |
  | HEAD 0e0cb7e (git worktree) | 7 | 0 | — |

  - A probe of the failing step (submit, idle, rework decision) ran 40 times on the fix under concurrent load. It reached `rework_ready` 40 of 40 times.
  - Cause of the two failures: not confirmed.
  - Diff reading: for a lease with a workspace every changed branch takes the same path as before. The only differences are where a workspace is `None`, which these tests never create.
- Linux Docker, Python 3.11 and 3.10: `test_work_parallel_finalization.py`, `test_work_parallel_restart_terminal.py`, `test_work_gate_check.py` and `test_side_queue_starvation.py` ran 138 tests each, 0 failures (5 skips need git history).
