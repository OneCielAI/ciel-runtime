---
type: Integration Research
title: Runtime default work-mode integration seams
description: Existing Ciel Runtime menus and transports can carry an optional work-mode preference, but no such preference is currently observed.
tags: [ciel-runtime, work, menu, launch, integration]
timestamp: 2026-10-26T00:00:00Z
---

# Observed seams

- `ciel-runtime-menu.py:595-601` loads config; `:604-619` persists `last_launch_action` by temporary-file replacement. `prelaunch_launch_preference.py:9-18,26-75` defines the key and fallback. No work-mode key was found.
- `prelaunch_launch_panel.py:23-38` already distinguishes Claude, plain Codex TUI, Codex TUI + app-server, and standalone app-server.
- `runtime_launch.py:410-452` selects direct/native versus routed Claude; `:606-614` adds Claude messaging-socket arguments when configured.
- `claude_session_socket.py:146-184` authenticates to a published peer key and sends newline-delimited auth and user frames.
- `chat_http_controller.py:140-166,474-496` accepts `input_transport=session_socket|tty|router` and preserves it in metadata.

# Proposed design (not implemented)

The user clarified after the initial research that Work must operate as an independent mode, analogous to Plan mode, not merely a startup invocation of the `/work` skill. The initial `off|ask|start-plan` suggestion is therefore superseded.

1. Model **workflow** (`normal|work`) separately from **permission** (`bypassPermissions`). Work is not the native Plan permission mode and must not become read-only while planning its tasks. Expose `Default workflow: Normal | Work` in the runtime menu plus explicit session enter/exit/status operations.
2. Persist mode, board reference, active item, work phase and revision outside the conversation. The mode controller owns enter/exit/switch/resume transitions; a prompt or a flat TodoWrite list is not authoritative mode state. Never convert pausing/exiting Work into automatic permission lowering.
3. Reuse one board owner/service (initially the ciel-code board implementation behind a stable CLI/MCP boundary) instead of having the runtime and ciel-code write divergent boards. Before multi-client use, add verified schema validation, locking/revision conflict handling and tested session/item resume binding. Any shared board mutation design is still a proposal.
4. For Claude native/direct and routed paths, use the launch/settings permission control and client-side session/prompt/compaction lifecycle integrations. Restore authoritative Work state and publish a bounded active-item snapshot after start/resume/compaction, without repeatedly executing an interactive `/work` selector. A router-only system prompt would miss native/direct requests.
5. Keep each mode operation explicit: create/switch item, decompose/update/complete step, pause/resume item, inspect tree, exit mode. Restore context without executing a task merely because it exists. Track a next action, blockers and completion evidence; do not generate unlimited automatic continuation from unfinished work alone.
6. For Codex TUI and app-server, reuse the same board/mode service with a different adapter. Its plan display is a projection, not a second source of truth. Apply approval/sandbox controls through launch or JSON-RPC rather than prose; test resumed threads separately.
7. Native Claude's exact Plan-mode UI/Shift+Tab integration is not established by these inspected runtime seams. An independent runtime workflow can be researched without claiming it is a newly added built-in Claude permission mode. A true new native mode/UI entry would require a documented extension point or changes to the owning CLI.

# Unknowns

No inspected Ciel Runtime source invokes `/work` or exposes `default_work_mode`. The peer handoff remained queued without a response, so peer-side recommendations and live `/work` behavior are unconfirmed. The work design note itself lists cross-process locking and remote-channel `/work` as verification items (`C:/Users/djlov/ciel-code/docs/okf/ciel-code/architecture/concepts/work-items.md:56-59`).
