---
type: UI Verification
title: Compact Work display and backend exposure
description: Keeps the default Work display one row, separates completed items, preserves expanded browsing, and verifies REST and MCP management over real HTTP.
tags: [work, statusline, compact-view, rest, mcp]
timestamp: 2026-10-29
---
# Changes

- Generated statusline defaults to one bounded Work row: done/total plus current leaf. No root title, all-branch breakdown, queue preview or completed rows in normal mode. Conservative terminal-cell clipping prevents long Korean titles from overflowing the configured COLUMNS (max100 cells).
- Legacy external board fallback also shows only its active unfinished item/current step. Existing diagnostic expanded text remains available through `CIEL_RUNTIME_WORK_VIEW=expanded`.
- AbovePrompt mod defaults to one Work row, e.g. `Work 20/40 [~] OPEN-ITEM-00 [+]`. The native engine/companion tree remains an unsized sibling, not clipped beneath the Work panel.
- `[+]`/w opens the bounded inline expanded view; `[-]`/c collapses it. `/work-mode expand` and `/work-mode compact` provide explicit display-only commands. Narrow screens open the paged Inspector.
- Expanded Progress omits completed rows. Completed remains a separate tab. The exact active Inspector no longer mixes completed descendants into an unfinished subtree; completed nodes remain individually inspectable from Completed with their evidence.
- Expansion is session-local display state. It does not mutate the Work plan or start a model turn; conversation changes reset the view.

# Verified

- Actual Claude Code2.1.312 + production mod + owned loopback router:14/14 fixture checks pass.40 leaves (20 done),6 queued goals, completed history. Default one-current-item display, expand, exact Inspector, Queue/Completed, collapse again, companion visibility, and byte-equal before/after plan snapshots. Six screenshots in [UI evidence](evidence/ui/).
- Statusline tests34 pass. Full native mod tests70:68 pass and2 fail. The same two non-UI tests fail on an untouched HEAD baseline (68 tests:66 pass/2 fail): native plan exemption cleanup and planning-denial toast count. They are not claimed fixed or hidden; exact outputs retained.
- Actual HTTP against a separate owned production router:6/6 checks pass. REST board/add; MCP tools/list+work_status; REST Work off persists, preserves plan and rejects mutation409; MCP Work on persists and restores catalog; REST Work on and help succeed.
- Protected local9505PID86296/9524PID18536 unchanged in actual UI before/after health checks. No live Work/config/queues were changed by fixtures.

# Backend API exposure

REST:
- GET `/ca/work`, `/ca/work/runtime`, `/ca/work/events` (SSE), `/ca/work/help`.
- POST `/ca/work/nodes/add`, `/update`, `/remove`, `/reorder`; require `expected_version`.
- Work settings use POST `/ca/config/llm`: `{ "action":"option", "key":"work_mode", "value":"off" }` or `"on"`. Approval and auto-continue settings are exposed through this same config controller.

MCP JSON-RPC: POST `/ca/mcp`, server `ciel-runtime-router`.
- `work_status`, `work_plan_set`, `work_node_add`, `work_node_update`, `work_node_remove`, `work_select`, `work_execute`, `work_replan`, `work_reorder`, `work_urgent`, `work_focus`, `work_mode_set`.
- When off, only `work_mode_set` remains in the Work tool catalog. Plans remain preserved.

Source references: `work_http_controller.py:49-153`; `llm_config_http.py:53,98-138`; `work_mcp_tools.py:195-241`; `channel_mcp_http_controller.py:69-95`. Shared router guards allow loopback; external administration requires enabled remote access and an admin token or valid web session. This task did not enable remote access or change auth.

REST and MCP are not full parity: `/ca/work` lacks dedicated execute/replan/select/focus/urgent/replace-plan operations; those exist through MCP. REST settings provide approval/auto-continue, while dedicated MCP has only Work on/off. Source audit plus41 focused backend tests independently verified by read-only agent; actual six HTTP checks above close its original mode-switch transport gap.

# Execution notes and limits

The first UI driver attempted unsupported letter-key handling and stopped before expansion; default compact screenshot was captured. Second run's process enumeration hit an encoding failure; only its uniquely identified owned cmd was cleaned up. The final successful run is `work-panel-compact-627bd8cb`; no unrelated processes terminated. Native test baseline isolates existing failures instead of weakening those gate tests.

Only source and journal changed; no commit, publish or local install was performed. Running installed sessions still use their existing release. Real teammate internals were not instrumented: companion sentinel verifies coexistence only. Existing native engine-ref preservation tests pass.
