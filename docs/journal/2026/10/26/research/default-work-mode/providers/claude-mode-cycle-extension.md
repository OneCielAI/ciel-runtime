---
type: Provider Research
title: Whether Work can join Claude Code's Shift+Tab mode cycle
description: The Shift+Tab cycle is Claude Code's closed permission-mode set; mods can relabel the footer, rewrite the plan_mode reminder, gate tools and add commands, but no documented hook adds a cycle entry.
tags: [claude, permission-modes, shift-tab, mods, plan-mode, work-mode]
timestamp: 2026-10-26T06:30:00Z
---

# Question

The user asked how to add Work to the modes shown when cycling with Shift+Tab, and clarified that Work means Plan mode that can build a deeper, hierarchical plan.

# Observed (official docs and published types)

- `chat:cycleMode` (Shift+Tab, Meta+M on Windows without VT mode) is documented as "Cycle permission modes" ([keybindings](https://code.claude.com/docs/en/keybindings), retrieved 2026-10-26).
- The modes are `default`, `acceptEdits`, `plan`, `auto`, `dontAsk` and `bypassPermissions` ([permission modes](https://code.claude.com/docs/en/permission-modes), retrieved 2026-10-26).
- The published mod declarations (`mods/types/claude-code.d.ts` on `anthropics/claude-code` main, 517,451 bytes, downloaded 2026-10-26) define `type PermissionMode = 'default' | 'acceptEdits' | 'bypassPermissions' | 'plan' | 'dontAsk' | 'auto'`. No event or method for cycling modes or adding a mode was found by searching for `cycle`, `keybind`, `setMode` and `permissionMode`.
- Mods need Claude Code 2.1.287 or later in the terminal ([mods overview](https://code.claude.com/docs/en/plugins/mods/overview)); `claude --version` here reports 2.1.310.
- What a mod can do, per [events](https://code.claude.com/docs/en/plugins/mods/events), [API](https://code.claude.com/docs/en/plugins/mods/api), [interface](https://code.claude.com/docs/en/plugins/mods/interface) and [reference](https://code.claude.com/docs/en/plugins/mods/reference):
  - `SessionMode` render site: the dim mode labels at the right of the prompt footer. The declaration says "A hook adds a mode by rewriting `modes`". It is a label, raised on terminal and desktop only.
  - `prompt.attachment`: rewrite or omit engine reminders. Declared kinds include `plan_mode` and `plan_mode_exit`.
  - `tool.call`: deny or answer a tool call before the permission check runs; `tool.check`: decide allow/ask/deny.
  - `$.command.register` (optionally `immediate: true`) plus a keybinding `"<key>": "command:<name>"` in the `Chat` context.
  - `$.tool.register` for model-callable tools (`mcp__<plugin>__<name>`), panes for display, `$.store` (shared JSON store, not atomic across sessions), `session.compact` and `classic.SessionStart` (`source` `compact`/`clear`/`resume`).

# Conclusion (bounded)

- A new entry in the Shift+Tab cycle is not reachable through any documented extension point; it would need a change to Claude Code itself.
- Reachable without that: keep native `plan` in the cycle and change how Plan plans (rewrite the `plan_mode` reminder, validate `ExitPlanMode` through `tool.call`), or a separate Work state toggled by a mod command/keybinding and shown as a footer label.
- Codex has no Claude mod surface; a Codex side needs its own adapter.

# Unverified

- The declarations are from GitHub main, which the docs say can be older or newer than the installed build. The exact 2.1.310 declarations were not obtained.
- No mod was written or loaded. Whether the `SessionMode` label, the `plan_mode` attachment kind and `tool.call` gating behave as declared in 2.1.310, and through the Ciel Runtime routed and direct launches, is untested.
- The existing ciel-code board is still fixed at Board -> WorkItem -> WorkStep ([work board](/architecture/work-board.md)).
