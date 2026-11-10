---
type: Change
title: Removed the side comparison experiment
description: The read-only full/recent × main/comparison-model shadow runs, their MCP tool, settings and menu rows were removed at the user's request.
timestamp: 2026-11-10T06:30:00Z
tags: [side-actor, cleanup, settings]
---

# Request

The user asked why both a side model and a comparison model exist. After the explanation, they called it an experiment tool and asked for it to be deleted (2026-11-09).

# Why it could go

- **No operational role.** It ran only after the primary side answer, as up to four read-only shadow agents: full/recent context × main/comparison model. Shadows had no tools, wrote no Work and sent no replies.
- **It never measured anything.** The configured comparison model `_API_KEYS-SSE-1` returned 404 "no executable targets" (see [routed context](../../../06/features/side-decision/routed-context.md)). No comparison result was ever recorded.
- **Its only effect was cost.** When enabled, it added billed model calls.

# Removed

- `side_comparison.py` and its tests.
- The `work_side_compare` MCP tool, removed from:
  - the host side tool set;
  - the Work tool lists;
  - the Work-mode launch allow-list.
- Settings `comparison_enabled` and `comparison_model`:
  - menu rows and texts;
  - live-settings projection;
  - CLI `work set` (now rejected as unknown);
  - side model picker branches;
  - subagent model override;
  - router parent-capture trigger.
- Shadow branches in the side context projection: forced `tool_choice: none`, and the processed-receipt fencing.
- Shadow runner, display rows and shadow actor names in the `ciel-work` mod.
- The comparison paragraph in `docs/CLI-Reference.md`.

# Compatibility

- Configs that still hold `comparison_enabled` or `comparison_model` keep the keys. `runtime_settings` ignores them, and they no longer change the subagent model.
- Side context files written before the removal stored `shadow: false` in their profile. Profile comparison drops that field, so an existing actor's context is not rejected.

# Verification

Targeted tests passed with a temporary config directory:
- side context router;
- launch data path, including a retired-key config that now keeps the parent-model force;
- Work settings CLI (retired keys rejected);
- Work MCP tools;
- mod harness;
- side intake, menu and live settings.

The full four-group run is recorded in the [console verification](../../features/work-web-console/verification.md).
