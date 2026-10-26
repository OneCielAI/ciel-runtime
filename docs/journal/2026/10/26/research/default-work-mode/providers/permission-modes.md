---
type: Provider Research
title: Claude and Codex work-mode permissions
description: Claude and Codex expose different permission controls, so a shared work-mode toggle must separate startup prompts from approval and sandbox configuration.
tags: [claude, codex, permissions, bypass, app-server]
timestamp: 2026-10-26T00:00:00Z
---

# Claude

Observed Ciel Runtime evidence:

- Routed launch computes whether to add a permission-mode option (`ciel_runtime_support/runtime_launch.py:622-652`).
- Ciel-code native launch creates its session with `permissionMode: "bypassPermissions"` (`C:/Users/djlov/ciel-code/src/cli/native-run.ts:38-42`). Its comments say interactive TUI starts in plan and uses `settings.local.json` to return to bypass (`native-run.ts:55-65`), but comments are not runtime proof.
- Ciel Runtime sets `CIEL_RUNTIME_BYPASS_PERMISSIONS=1` in its Claude environment (`ciel_runtime_support/claude_environment.py:72-82,611`).

Official Claude docs state that `defaultMode` controls the starting mode and distinguish `plan`, `auto`, and `dontAsk`; they also state permissions are enforced by Claude Code, not model instructions. Source: [Configure permissions](https://code.claude.com/docs/en/permissions), retrieved 2026-10-25.

Official hook docs list `SessionStart`, `UserPromptSubmit`, `PreToolUse`, and `PermissionRequest`, and support user/project/local settings. Source: [Hooks reference](https://code.claude.com/docs/en/hooks), retrieved 2026-10-25. This research did not install or change a hook.

# Independent Work mode implication

Work must be its own workflow state, not an alias for Claude's `plan` permission mode and not just a prompt that asks the model to run `/work`. Keep the two axes explicit:

- `workflow_mode=work`: the runtime owns active item, hierarchy, phase, resume and mode enter/exit.
- `permission_mode=bypassPermissions`: Claude launch/settings enforce bypass; Codex launch/app-server fields enforce full access where selected.

A `/work` message can bootstrap or repair a client projection, but it cannot be the source of truth and cannot grant permissions. Any implementation claiming Plan-like independent lifecycle, compaction restore, or cross-client resume needs runtime tests at session start, mid-turn socket handoff, compaction, restart and Codex thread resume.

# Codex

Observed Ciel Runtime evidence:

- `codex_cli.py:293-312` maps `--permission-mode bypassPermissions` to `--dangerously-bypass-approvals-and-sandbox` and records that the Claude flag is otherwise ignored for Codex.
- `codex_desktop_injection.py:68-78` defines full access as `approval_policy="never"`, `sandbox="danger-full-access"`, and turn sandbox policy `{type: "dangerFullAccess"}`.
- `codex_app_server_session.py:88-105` inserts approval and sandbox config overrides. The injector sends matching values on `thread/resume` and `turn/start` (`codex_desktop_injection.py:311-315,738-743`).
- Runtime notes state that resumed threads retain saved approval/sandbox state and that the first resume or a later `turn/start` is the override point (`codex_desktop_injection.py:19-25`).

Official OpenAI docs describe app-server as bidirectional JSON-RPC with `thread/start`, `turn/start`, streamed events, and approval handling; they document local stdio/WebSocket/Unix transports and `codex --remote`. Source: [Codex app-server](https://developers.openai.com/codex/app-server), retrieved 2026-10-25.

# Integration implication

Treat `start-plan` and `bypass` as distinct controls:

- **Work startup**: send or launch `/work` and attach the selected board item.
- **Permission policy**: set Claude permission mode/settings or Codex approval/sandbox fields through provider-specific launch/protocol adapters.

“Always bypassPermissions on Claude, ideally Codex” is a proposed default policy, not an observed existing runtime menu toggle.
