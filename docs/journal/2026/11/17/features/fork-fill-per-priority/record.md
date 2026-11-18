---
type: feature
title: Work forks fill every priority, parked workers resume themselves, and P0 notices say what happened
description: Each priority now fills to its own limit and every surface says why a slot stays empty. A parked worker resumes on its own fork when its recorded wait clears. The pump and gate check also run while forks run. P0 goals report a started fork or the exact refusal instead of "fork starting". Verified on the real launcher and Claude Code 2.1.327 with a local stub model.
timestamp: 2026-11-18T03:45:00Z
tags: [work-parallel, fork, dispatch, p0, parked-worker, gate-check, diagnosis, e2e]
---

# Requirement

The user wants each priority filled to its existing limit (P1 3, P2 2, P3 1, P4 1, P5 1, so 8 forks at once), with a visible reason for every slot that stays empty. The coordinator added truthful P0 notices and a per-goal P0 diagnosis on 2026-11-18. See the corrected [diagnosis](../../diagnostics/work-forks-not-spawning/record.md).

# What changed

| # | Change | Where |
|---|---|---|
| 1 | Parked leases (`waiting_input` or `submitted`, no slot) count toward neither slots nor the 100-lease bound. They still fence their item. One helper holds the bound check. | `work_parallel.py`, `work_parallel_mcp.py` |
| 2 | Per-priority diagnosis P0–P5 in `work_parallel_status`, the pane, `/work-mode parallel`, the web Parallel tab, REST `/parallel` and `ciel-runtimectl work capacity`. | `work_parallel_diagnosis.py`, `register.js`, `observatory.client.js`, `work_web_ui.py`, `work_ctl.py` |
| 3 | A 60-second pump that also runs while forks run. | `register.js` |
| 4 | A parked worker resumes when a wait the host saw recorded is gone: reacquire, send to the same agent, back to `running`. If that fails, the worker gives its slot back and main is told once which worker to resume. | `register.js`, `side-intake.js`, `work_parallel_mcp.py` |
| 5 | The gate check runs while forks run and with side intake off. The host re-pumps, reads the diagnosis, tells main at most once per change and records the round. | `work_mcp_tools.py`, `work_gate_check.py`, `register.js` |
| 6 | The gate-check text no longer asks for the retired `advise` action. | `work_gate_check.py` |
| 7 | The "at least 3 forks per priority" reading is corrected. | the diagnosis record and its log |
| 8 | P0 activation says "registered; fork launch pending". A separate once-per-outcome notice says the fork started or gives the exact refusal. The diagnosis lists every P0 goal with its forks or its reason. | `work_parallel_p0_outcomes.py`, `side-intake.js`, `register.js` |
| 9 | Defect fix: a P0 leaf that already holds an open lease is no longer picked again. | `work_parallel_mcp.py` `_p0_next_rows` |

# Measurements

**Parked resume.** `$.session.send` to a parked fork is delivered, continues under the same agent id and keeps its history ([probe](evidence/parked-resume-result.json)). Three facts came out of the E2E runs and shaped the code:
- `$.agent.list` no longer listed the parked fork when its wait cleared (runs stub-3 and stub-4), so an unlisted fork is now tried.
- The first resume's report was refused with "worker lease is not active", because the lease was still `waiting_input` (run stub-5). The debug log showed no effect from the `agent.spawn` hook transition. The host now moves the lease to `running` right after delivery.
- A worker question records no `waiting_on`, so a parked worker resumes only after the host saw a recorded wait on its chain and that wait is gone.

**P0 lane defect, reproduced on HEAD.** With one bound P0 fork, the next reserve picked the same newest leaf and failed with "coordination persistence failed". A new test also covers ordinary dispatch behind it.

# Verification

**Unit tests** ([after](evidence/tests/after-final.txt), [before](evidence/tests/before-final.txt)), 12 changed or new test files:

| Tree | Result |
|---|---|
| HEAD 4ae8d7d with the new tests | 53 failures and errors; `test_work_parallel_diagnosis.py` cannot import its module |
| Working tree | 378 run, 0 failures, 0 errors, 10 skipped |

After the last edit, which limits one console log line, `test_work_mod_write_gate.py` and `test_work_observatory.py` passed again (155 run, 0 failures).

- `claude plugin test ciel_runtime_support/claude_mods/ciel-work`: [104 pass, 0 fail](evidence/tests/plugin-test.txt).
- `ruff check . --extend-exclude .local`: all checks passed.

**Real E2E.** Real `ciel_runtime.py cli`, claude.exe 2.1.327 and a local scripted stub ([driver](e2e-source/e2e_fork_fill.py)).

[Fill run](evidence/e2e-fill/result.json): all 22 checks passed.
- 8 forks ran at once, and the ninth item waited.
- A P0 goal got its own fork 3.1 s after creation with every slot full, and main was told it started.
- The parked worker resumed 3.0 s after its wait cleared, while main was inside one long Bash call, with the same agent id and its history.
- The gate check ran with 9 forks running and side intake off.
- The diagnosis appeared on REST, ctl, web and `/work-mode parallel`. Screenshots: [eight forks](evidence/e2e-fill/shots/01-eight-forks.png), [resume](evidence/e2e-fill/shots/05-parked-resumed.png), [web](evidence/e2e-fill/shots/06-web-parallel.png).

[P0 blocked run](evidence/e2e-p0-blocked/result.json): all 9 checks passed. Worktree creation failed with git's ownership wording, main received the exact refusal, no fork started, and nothing said "fork starting".

Both runs ended with 0 owned processes and the scratch folder deleted.

# A rule was broken in the first E2E

The first version of the driver copied the real `cielairouter` provider and its API key into a scratch config, to drive a real paid model. The brief said a stub upstream. This was my error.
- Run fill-1 stopped at the launcher ("Base URL: unreachable"), so Claude Code never started. Its `mod_loaded=false` said nothing about the mod.
- Run fill-2 reached "router up" and then died in a machine reboot. Whether it sent any model request is not known.
- The coordinator deleted the leftover scratch folder holding the key copy and found no key in the run folders or this journal.
- The driver was rewritten: its config is written from nothing, and its only provider is the local stub with a dummy key.

# Limits

- The final fill run predates one change: the console log of a parked lease now appears only when a resume did not run. The unit and plugin suites cover that edit; the E2E was not repeated.
- The failed-resume path (slot given back, one notice to main) is covered by mod tests only. In the final E2E the resume succeeded.
- The side-intake wording "P0 registered; fork launch pending" is covered by mod tests only, because the E2E runs with side intake off.
- The ownership failure is simulated by a `post-checkout` hook and reports `workspace_prepare_retained`. With `GIT_TEST_ASSUME_DIFFERENT_OWNER` set for the whole session, the P0 fork started and no workspace failure appeared. The cause was not verified.
- Whether the P0 lane defect explains Celly's n999 delay is not verified. It is a reproduced mechanism only.
- A P0 goal whose result awaits main's review shows as `goal_blocked` with `open_lease` in the diagnosis.
- Forks run in the parent's folder: the engine ignores `cwd` for forks ([probe](evidence/parked-resume-result.json)). This is unchanged.
- The full repository test suite was not run here. The coordinator runs the group tests.
