---
type: verification
title: Agent-view controls verification
description: Real-console Claude Code probes, focused and engine tests, lint, and beta release for the agent-view Work settings.
tags: [verification, claude-code, agents-view, beta]
timestamp: 2026-11-07T18:45:00-06:00
---

# Real console (Claude Code 2.1.310, the binary ciel sessions run)

The [probe](evidence/left_arrow_probe.py) launches plain `~/.local/bin/claude.exe` in conhost. It strips
inherited CIEL/CLAUDE/ANTHROPIC/CODEX/OPENAI variables and never submits a prompt. It sets the value through
`claude_user_settings` before each launch.

| Launch | Observation | Evidence |
|---|---|---|
| `leftArrowOpensAgents` on | Footer shows `· ← for agents` | [a1](evidence/a1-on-start.png) |
| on, one ← on empty prompt | Agents view opens at once (the screen in the user's image) | [a2](evidence/a2-on-left-once-agents-view-redacted.png) |
| on, Esc | Back to the prompt | [a4](evidence/a4-after-esc.png) |
| on → off **while running**, 15 s later ← ← | Agents view still opens | [a6](evidence/a6-running-session-after-off-redacted.png), [result](evidence/probe-launch-on.json) |
| `leftArrowOpensAgents` off | No `← for agents` in footer | [b1](evidence/b1-off-start.png) |
| off, ← ← on empty prompt | Prompt stays; no agents view | [b3](evidence/b3-off-left-twice.png), [result](evidence/probe-launch-off.json) |

Conclusion: the setting takes effect in Claude sessions launched after the change. A running session did
not pick it up within 17 s, so the row reports `next_claude_session` (commit 93be24c). Session titles in
the agents-view frames are redacted because the repository is public.

An earlier attempt ran the npm `claude` that comes first on `PATH` (2.1.319). It showed neither the hint
nor the view in either state, so it is not used as evidence.

# Tests

- `tests/test_claude_user_settings.py`, `tests/test_work_live_settings.py`, `tests/test_work_observatory.py`,
  `tests/test_side_work_decision.py`: 31 passed.
- `claude plugin test` on `ciel-work`: the three Observatory tests pass, including the new rows (`= unreadable`
  and a left-arrow toggle write). Totals stay 21 pass / 77 fail, the same pre-existing `clock.after` fixture
  failures as the HEAD baseline. `claude plugin validate` passes.
- `ruff check .` on a clean `git archive HEAD` export: all checks passed. The first beta Publish run for 72e59e2
  failed on two lint findings (an unused import, and a lambda in `test_side_work_decision.py`); 9293d59 fixed them.

# Release and installed-beta console

Beta pushes: 72e59e2 (Publish failed: lint), 9293d59, 93be24c. The 93be24c CI and Publish runs passed.
Registry verification proved:

- beta `0.2.51-beta.20261108-005232.93be24c`, `gitHead=93be24c383fdc420eb30bf0ff8c3e51057f785f2`;
- tarball SHA-512 integrity matched and 508 runtime source files matched Git;
- `latest=0.2.51` and `nightly=0.2.51-nightly.20261106-020333.c00c8b6` did not move.

The verified tarball was extracted to a new immutable snapshot and installed globally. No running process was
restarted. Installed smoke compared all 562 package files, passed six isolated CLI commands, and returned both
settings rows with the intended values and `next_claude_session` status.

Finally, an owned isolated real-console launch of the installed beta opened `/work-mode settings`. The first page
visibly shows `Left arrow opens agents view = false` and `Background agents / agent view enabled = true`:
[screen](evidence/installed-beta-work-settings.png), [result](evidence/installed-beta-real-console.json).
Its scratch config was removed, its owned processes ended, and the live router health results on 9505/9489/9524
remained 200 before and after. Registry and local install details are also recorded in the [log](log.md).
