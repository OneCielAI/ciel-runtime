---
type: Diagnostic
title: Subagents had no PowerShell tool while the main session did
description: Claude Code offered the PowerShell tool to the main session but not to general-purpose subagents; launches now set CLAUDE_CODE_USE_POWERSHELL_TOOL=1 on Windows unless the user chose a value.
tags: [claude, subagent, powershell, launch, windows]
timestamp: 2026-10-27
---
# Observed

- Live probe in this session (2026-10-27, Claude Code 2.1.310 native, launched by ciel-runtime): a general-purpose subagent reported `present=no, shell tools=[Bash]` for `PowerShell`, while the main session has and uses the PowerShell tool.
- Local transcripts, last 4 days, all projects: 499 subagent transcripts contain 0 PowerShell tool calls; main sessions in the same period used it (Walkie 354, Sutter 256, view-team-analysis 188, ciel-runtime 19 and 33 calls). Subagent attempts went through Bash as `powershell -Command ...`; their 11 failures in this project were bash quoting (`$_` expanded by bash inside double quotes) and long commands cut off (`unexpected EOF while looking for matching`), not permission denials.
- Official [tools reference](https://code.claude.com/docs/en/tools-reference.md) (PowerShell tool): on Windows with Git Bash installed the tool "is on by default for claude.ai and Console accounts"; `CLAUDE_CODE_USE_POWERSHELL_TOOL=1` enables it, `0` turns it off. Neither user settings nor the session environment set the variable here. Why the account default reached the main session but not the subagent is not known.

# Isolated reproduction (real Claude Code, owned router and Work mod, fake gateway)

The fake model has the main agent call `PowerShell`, then start a general-purpose subagent that calls `PowerShell` ([e2e_subagent_ps.py](evidence/scripts/e2e_subagent_ps.py)).

| Run | Main offered | Subagent offered | Subagent result |
|---|---|---|---|
| [-p, 2.1.311, no env](evidence/scripts/run-source-bypass-121415/result.json) | no | no | `No such tool available: PowerShell` |
| [-p, 2.1.310 native, no env](evidence/scripts/run-source-bypass-121816/result.json) | no | no | same |
| [-p, live cached feature flags copied](evidence/scripts/run-source-bypass-livegates-121739/result.json) | no | no | same |
| [-p, `CLAUDE_CODE_USE_POWERSHELL_TOOL=1`, bypass](evidence/scripts/run-source-bypass-ps1-121509/result.json) | yes | yes | `PS-CHILD-OK-42` |
| [-p, env=1, default permission mode](evidence/scripts/run-source-default-ps1-121542/result.json) | yes | yes | native "Permission to use PowerShell has been denied" (no prompt in -p) |
| [interactive, 2.1.310, no env](evidence/scripts/ui-source-122009/result.json) | no | no | `No such tool available` |

Without an account the isolated main session is not offered the tool either, so these runs show the effect of the variable, not the live main/subagent split itself; that split is the live probe above.

# Change

`claude_environment.default_powershell_tool` sets `CLAUDE_CODE_USE_POWERSHELL_TOOL=1` on Windows when the inherited value is unset or blank; any explicit value, including `0`, is kept. `runtime_launch.run_claude` applies it to the child environment after removing projected Claude keys. Permission decisions stay native; Work does not gate shells.

# Verification

- Launcher proof: the real `ciel_runtime.py cli --ca-runtime claude` in an owned minimized Windows Terminal, isolated config/HOME/ports, egress sentinel, Work mode on ([e2e_subagent_ps_launch.py](evidence/scripts/e2e_subagent_ps_launch.py)):
  - installed 8ebacfa: [screenshot](evidence/scripts/launch-installed-130542/01-outcome.png) shows `MAIN-DONE main-ps=[... No such tool available: PowerShell ...] child=CHILD-DONE [... No such tool available ...]`; neither request offered the tool.
  - worktree: [screenshot](evidence/scripts/launch-source-130515/01-outcome.png) shows `MAIN-DONE main-ps=[PS-MAIN-OK-42] child=CHILD-DONE [PS-CHILD-OK-42]`; both requests offered the tool ([result](evidence/scripts/launch-source-130515/result.json)).
  - Live routers 9505 (pid 79472) and 9524 (pid 65156) unchanged before and after.
- [Focused tests](evidence/focused-tests.txt): new `test_claude_powershell_tool_env` (4), architecture_contracts 261 (42 skipped), claude_environment 24, launch_data_path 49, runtime_session_restart 63, compaction_launch_contracts 6 and five more launch files: OK. ruff clean.

# Separate native observation (not changed here)

Claude Code's Bash tool on this machine received long commands cut off: a heredoc command of 2,092 characters arrived intact, 4,064 to 8,892 characters ended early (`here-document ... delimited by end-of-file`), 11,816 and 19,568 characters failed with `ENAMETOOLONG: name too long, uv_spawn` ([probe](evidence/scripts/e2e_bash_length.py), [result](evidence/scripts/bashlen-source-bypass-122122/result.json)). The probe lines contained many single quotes, so the cut-off point depends on escaping; the exact limit was not determined. The native transcript of this session held the full text of two commands that bash received truncated, so the cut happens after the model output.

# Limits

Running sessions keep their environment; the change applies to Claude sessions launched after the update. A user who set `CLAUDE_CODE_USE_POWERSHELL_TOOL=0` keeps that choice.
