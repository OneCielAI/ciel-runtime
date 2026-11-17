---
type: fix-record
title: The runtime's goal command (goal_clear) is removed; goals stay with each runtime's own /goal
description: The router MCP tool goal_clear, which typed /goal clear into terminals and sent thread/goal/clear to Codex app-servers, is removed on the user's decision. A goal_clear request left in the session-command slot by an older build is discarded on read and never turns into /compact.
timestamp: 2026-11-17T05:45:00Z
tags: [goal, goal_clear, session-command, mcp-tools, codex, claude-code, muse]
---

# Request (user, 2026-11-16)

> codex에서 goal을 업데이트하거나 삭제하는 기능을 제공하는데. 부작용이많아서 .ciel runtime에서제공하는 goal기능은 삭제해줘

In English: Codex already updates and deletes goals itself. The goal feature in Ciel Runtime has too many side effects, so remove it.

# What the runtime's goal feature was (HEAD `dc5da07`)

- **MCP tool.** `goal_clear` was listed by the `ciel-runtime-router` MCP server (`channel_mcp_tools.py`). It queued `action=goal_clear` in the single-slot session-command file `channel-compact-request.json`.
- **Terminal consumer** (`channel_compact_injection.py`).
  - It typed `/goal clear` into Claude Code, Codex and Muse terminals.
  - It typed it even while a tool call or a turn was running (`MID_TURN_ACTIONS`). No other session command does that.
- **App-server consumer** (`codex_desktop_injection.py` `_goal_clear`). It sent `thread/goal/clear` to `codex-remote`, `codex-app-server` and `codex-desktop`, also without waiting for idle.
- **Remote TUI** (`codex_app_server_session.py`). `REMOTE_TUI_CHANNEL_ACTIONS` carried `goal_clear`.
- **Docs.** `docs/Codex-Remote.md` had a `goal_clear` row and column.
- **No goal setting.** The runtime never set or updated goals; `/goal <condition>` was always the runtime's own command.

# Change

1. **The tool is gone.**
   - The schema and its `SESSION_COMMAND_TOOLS` entry are removed.
   - A call now returns `Unknown ciel-runtime-router tool: goal_clear` (`isError: true`) and queues nothing.
2. **No consumer runs a goal command any more.**
   - Terminal: `/goal clear` mapping and mid-turn exception removed; `active_tool_call` and `active_turn` now defer every command.
   - App-server: `_goal_clear` and its branch removed; `SESSION_ACTIONS = {compact, new_session}`.
   - Remote TUI: `REMOTE_TUI_CHANNEL_ACTIONS = {compact}`.
3. **A leftover request is discarded, never reinterpreted.**
   - **Why this was needed.** An unknown action used to fall back to `compact`. So a `goal_clear` request written by an older router, still within its TTL (default 600 s), would have been run as `/compact` or `thread/compact/start` by the new consumers.
   - **What happens now.** `RETIRED_SESSION_ACTIONS = {"goal_clear"}`.
     - `session_command_action` keeps a retired name, so it matches no consumer.
     - The repository read unlinks the file and logs `channel_compact_request_discarded id=.. action=goal_clear reason=retired`.
     - `payload()` refuses to queue it (`ValueError`).
   - **Shared rule.** `codex_desktop_injection.session_command_action` now uses the same repository rule instead of its own fallback.
4. **Docs.** `docs/Codex-Remote.md` lists only `compact_session` and `new_session`. It states that goals are set, updated and cleared by each runtime's own `/goal`, and that leftovers are discarded. `CHANGELOG.md` has an Unreleased entry.

# Kept, deliberately

**Kept: the startup answer for Codex's "Resume paused goal?" prompt** (`startup_prompt_answers.py`, `codex_paused_goal`). On an unattended restart it answers "Leave paused".
- It does not create, update or clear a goal.
- It stops a restart from resuming a paused goal by itself.
- It stays unless the user asks for it to go.

# Verification

- **Fail-before** ([evidence/fail-before.txt](evidence/fail-before.txt)). The new tests run against HEAD `dc5da07` source: 103 run, 11 failures.
  - `/goal clear` was injected (6 subtests).
  - `goal_clear` was in the catalog (2).
  - `thread/goal/clear` was sent (2).
  - The repository accepted `goal_clear` (1).
- **Pass-after on Windows** ([evidence/pass-after.txt](evidence/pass-after.txt)).
  - Changed modules: 103/103.
  - Adjacent modules (app-server session, terminal dispatch, unattended restart, compact poll, app-server, Muse): 177/177.
  - Modules that read the tool catalog: 584 run, 0 failures, 122 skipped (platform skips).
- **E2E with an isolated router** ([evidence/e2e-router.txt](evidence/e2e-router.txt)). The router ran with its own temp profile and port; live routers were untouched.

  | Check | HEAD | After |
  |---|---|---|
  | `tools/list` count | 52 | 51 |
  | `goal_clear` listed | yes | no |
  | `tools/call goal_clear` | queued | `isError`, nothing queued |
  | Planted `goal_clear` slot, read by the consumer path | returned as-is | `None`, file removed |
  | Queue `goal_clear` | accepted | refused |
  | `compact_session` | works | works |

- **Linux** ([evidence/linux-tests.txt](evidence/linux-tests.txt)). python:3.11-slim Docker, 15 modules: 736 passed, 128 skipped.
- **Windows, all four CI groups** ([evidence/windows-groups.txt](evidence/windows-groups.txt)). Run on a clean `git archive` of `5d65674`:
  - unit 4937 OK;
  - router 1675 OK;
  - channel 626 OK;
  - runtime 460 OK.
- **Deployment** ([evidence/deploy.txt](evidence/deploy.txt)).
  - Commit `5d65674` was pushed to `beta`.
  - Publish workflow 44025947302 succeeded: CI matrix, `quality`, `python-minimum`, `publish`.
  - npm beta is `0.2.51-beta.20261117-055915.5d65674`; its `gitHead` equals the commit.
  - Tarball integrity was verified, and it matches the git tree apart from the two version-stamped files.
  - It was installed globally on this machine.
- **Installed copy** ([evidence/installed-tests.txt](evidence/installed-tests.txt)).
  - The six goal-related modules: 191/191.
  - The isolated-router E2E, run from the installed package, gives the same "After" results as above.

# Not yet in effect for running sessions

A router keeps the code it started with.
- Sessions launched before this install still list `goal_clear` until they restart.
- This Walkie session is one of them: its tool list still shows it.
- No live session or router was restarted.
