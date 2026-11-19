---
type: evidence
title: Approval workflow test results
description: Test counts for the approval workflow change after rebasing onto the KOBE hotfix 1b42a73 (Linux Python 3.10 shards, ciel-work mod suite, mod-referencing Python files, real-backend suite, new tests on the pre-change base).
timestamp: 2026-11-18T16:40:00Z
tags: [work, approval-workflow, tests, evidence]
---

# Test results (worktree feature/priority-models, base 1b42a73 plus this change)

## Linux Python 3.10, git archive 1b42a73 (before)
py310 unit 1/3 exit=0 Ran 1498 tests in 135.883s OK (skipped=55)
py310 unit 2/3 exit=0 Ran 1671 tests in 185.090s OK (skipped=30)
py310 unit 3/3 exit=0 Ran 1805 tests in 186.101s OK (skipped=66)

## Linux Python 3.10, 1b42a73 + this change (after)
py310 unit 1/3 exit=0 Ran 1425 tests in 126.441s OK (skipped=21)
py310 unit 2/3 exit=0 Ran 1613 tests in 141.687s OK (skipped=56)
py310 unit 3/3 exit=0 Ran 1978 tests in 241.699s OK (skipped=74)

## claude plugin test ciel-work (after)
111 pass, 0 fail (hotfix suite plus tests/workflow.test.ts, 6 tests)

## Python files that reference register.js, side-intake.js or a .mjs harness (Windows, after)
RAN 494 failures=0 errors=0 skipped=0 (real-backend file run separately below)

## tests/test_work_mod_real_backend.py (Windows, after)
RAN 117 failures=0 errors=0 skipped=0

## New tests on the pre-change base 720eeb6 (must fail)
- tests/test_work_approval_workflow.py, _review.py, _surfaces.py: ImportError (work_workflow does not exist)
- tests/test_work_approval_workflow_mod.py: 3 of 3 failed (sideWorkflowApproved/workflowThread missing)
- ciel-work tests/workflow.test.ts: 6 of 6 failed

## Approval re-check reads only the request message (after)
- Linux Python 3.10: py310 unit 2/3 exit=0 Ran 1515 tests in 135.899s OK (skipped=22)  py310 unit 1/3 exit=0 Ran 1590 tests in 198.625s OK (skipped=29)  py310 unit 3/3 exit=0 Ran 1918 tests in 212.707s OK (skipped=100)  
- claude plugin test ciel-work: 111 pass, 0 fail
- Mod-referencing Python files: RAN 501 failures=0 errors=0 skipped=0
- tests/test_work_mod_real_backend.py: RAN 117 failures=0 errors=0 skipped=0
- tests/test_work_approval_workflow_recheck.py on 50f7d11: RAN 7 failures=1 errors=6 skipped=0

## Rebase onto hotfix 9cba321 + 3caf256, approval re-check in the one batched gate tick (after)
- claude plugin test ciel-work: 115 pass, 0 fail (new: "20 waiting items on 3 threads and 2 awaiting approvals").
- Mod-referencing Python files plus workflow, gate-check and side-decision files: RAN 625 failures=0 errors=0 skipped=0.
- tests/test_work_mod_real_backend.py: RAN 117 failures=0 errors=0 skipped=0.
- ruff: all changed Python files pass.
- New and changed tests on 2b11374 (before this change): test_work_approval_workflow_recheck.py RAN 9 errors=5 (the reader section and the batched tick are missing); test_work_approval_workflow_mod.py RAN 3 failures=1 (SSE approval read the thread, not the request message); ciel-work plugin test 111 pass, 1 fail (the new 20-item tick test).
- Not run after this rebase (lead order, deploy without CI): Linux Python 3.10 shards, Windows groups, real E2E. The E2E evidence in evidence/e2e/run is from before the rebase.
