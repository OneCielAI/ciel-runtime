---
type: Investigation
title: 'Window showing "Error: stdout is not a terminal"'
description: The message comes from the Codex CLI started through the npm codex.cmd shim with its output redirected; the process that started it is not recorded.
tags: [codex, windows-terminal, tty, diagnostics]
timestamp: 2026-10-26T20:05:00Z
---
# Report

The user sent a screenshot of a Windows Terminal window with one tab titled `C:\WINDOWS\system32\cm...` that shows only `Error: stdout is not a terminal`.

Legend: **Confirmed** means a command result in `evidence/`. **Not determined** means I found no record.

# Confirmed

- Among the binaries and trees searched, only the three installed `codex.exe` copies contain the text `stdout is not a terminal` (`evidence/01-string-search.txt`). It sits next to `stdin is not a terminal` in the Codex CLI and TUI code. `claude.exe` 2.1.308, 2.1.309 and 2.1.310 (native and npm), the installed Ciel Runtime snapshot 81f6a4b, this worktree, Python 3.14, the npm packages, `gh`, `git`, `cmd.exe` and the Ciel router logs do not contain it.
- Codex prints exactly `Error: stdout is not a terminal` and exits with code 1 when its interactive mode starts with a console as stdin and stdout redirected. Both installed versions do this: 0.158.0 (npm) and 0.164.0 (desktop app) (`evidence/02-codex-tty-probe.txt`, owned probe in an isolated `CODEX_HOME`). With stdin redirected as well it prints `Error: stdin is not a terminal` instead.
- `codex` on PATH resolves first to the npm shim `AppData\Roaming\npm\codex.cmd`, which runs `title %COMSPEC%` before starting Codex. That sets the console title to `C:\WINDOWS\system32\cmd.exe`, the title in the screenshot.
- The screenshot window has a single tab. No such window is open now. The only terminal window is the 3-tab window that holds the Walkie launcher, this session and one other session (`evidence/03-windows-and-walkie-tab.txt`).
- Process-creation auditing (Security event 4688) and Sysmon are not enabled, so no record names the process that opened that window.

# Separate observation in the Walkie tab (confirmed, cause not determined)

After the Walkie CLI exited normally at 14:38:02 local, three relaunches from its tab exited with code 1 about one second after start (14:39:03, 14:42:17, 14:44:47). The launches at 14:46:22 and 14:49:59 started normally. No log records what those three exits printed. They are Claude Code exits, and Claude Code does not contain this message, so the evidence does not tie them to the screenshot.

# Not determined

- Which program started `codex` with redirected output, and when. Candidates that start Codex on this machine include the Codex desktop app, the VS Code Codex extension, `cielview.exe`, and any script that calls `codex`. I found no Ciel Runtime code path that runs interactive Codex with redirected output: its Codex launches inherit the console, and its `--version` probes do not need a terminal.
- What the three code-1 Walkie relaunches printed.

# Needed to continue

When the window appeared, what was done just before (a menu choice, a script, an app), and whether it opened by itself. If it happens again, a screenshot of the whole window or the result of `wmic process where "name='codex.exe' or name='node.exe'" get ProcessId,ParentProcessId,CommandLine` while it is open would identify the starter.
