---
type: implementation
title: Claude agent-view controls in Work settings
description: Work & SSE Settings can turn off the empty-prompt left arrow that opens Claude Code's agents view, and agent view / background agents as a whole.
tags: [work-mode, observatory, claude-code, agents-view, settings]
timestamp: 2026-11-07T18:45:00-06:00
---

# Request

While moving through Work mode with the keyboard, a left arrow on an empty prompt dropped the user into
Claude Code's agents view (the background-session list). The user asked to block that, to offer an option
that hides that screen or stops background use, and to change both from `/work-mode view`.

# Where Claude Code keeps the switches (verified in the 2.1.310 binary)

| Public row | Claude key | File | Meaning |
|---|---|---|---|
| Left arrow opens agents view | `leftArrowOpensAgents` (default true) | global config `~/.claude.json` | The empty-prompt handler opens the agents view only when it is not `false`. |
| Background agents / agent view | `disableAgentView` (default unset) | user settings `~/.claude/settings.json` | Settings schema: "Disable agent view (`claude agents`, `--bg`, /background, the on-demand daemon)". |

- The global config path follows Claude's own rule: a non-empty legacy `<config home>/.config.json`, else
  `$CLAUDE_CONFIG_DIR` (or home) `/.claude[-custom-oauth].json`.
- Claude Code saves its global config under a proper-lockfile lock: `mkdir <config>.lock`, stale after
  10 s, removed with `rmdir`. A regular file at that path would block its saves, so the first draft that
  used ciel's companion `.lock` file was replaced before it ever touched the real home.

# Code

- [`ciel_runtime_support/claude_user_settings.py`](/ciel_runtime_support/claude_user_settings.py): reads both
  values (`None` when a file cannot be parsed) and writes one of them. The global config is replaced only
  while holding Claude's own `<config>.lock` directory. A busy lock is never taken over: after 5 s nothing is
  written. Unknown keys and Claude's two-space format are kept, the replace is atomic, and an unreadable file
  is never overwritten. Turning background agents back on removes `disableAgentView`.
- `work_live_settings.py` projects both rows right after the basic Work rows (first Settings page),
  labelled in en/ko/ja/zh. Their status is `next_claude_session`. A row whose file is unreadable shows
  `unreadable`. Writes go to Claude's files, not to the Work config.
- `register.js`: an unreadable row shows `= unreadable`. A toggle toast adds "applies to new Claude sessions".

# Applied on this machine

`leftArrowOpensAgents` was set to `false` in `C:\Users\djlov\.claude.json` (105 → 106 top-level keys; nothing
lost or changed). `disableAgentView` was left unset. This session itself runs as a background job under
Claude's daemon (`--bg-pty-host`), so turning agent view off would also disable that kind of session. The
row lets the user choose it.

# Citations

- [Verification](verification.md)
- [Observatory implementation](../work-observatory/implementation.md)
