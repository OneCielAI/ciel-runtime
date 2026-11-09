---
type: implementation
title: Side simple contract - code and tests
description: Files changed for the status/info/clarify/request side contract, host-composed single reply, Walkie preset prefetch and send, queue reorder, guarded code reads, and the parent-prefix cache mark; with the tests that cover each.
tags: [side-intake, sse, work-mode, implementation]
timestamp: 2026-11-09
---

# Code

| File | Change |
| --- | --- |
| `ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js` | New PROMPT: status / info / clarify / request; the host sends; never claim a queue position. A request's reply is a recap with every stated requirement. An existing item is updated only for its own requested outcome; a different problem in the same area is a new item, and an urgent one is P1. The actor's tools are read MCP tools plus `work_side_code_read` and `work_side_code_search`, with no response verbs and no Work tools. Registration sets `maxTurns` 4, `effort` low and `omitClaudeMd`. `fetchSourceMessage` adds `source_message` to the payload. Resolve passes `work_digest`. A `work_changed` recheck sends nothing. `sideTool` passes the code tools through natively. |
| `ciel_runtime_support/side_work_decision.py` | `kind` field with the kind → action mapping. `queue_position` validated (1-based, waiting goals only). `unfinished_digest` covers goals only, and `WorkChanged` is raised when a create sees a changed digest. update/prioritize can set priority and position in one transaction. The result carries `queue` and `final_text`. |
| `ciel_runtime_support/work_plan_tree.py` | `reorder_queued_root`, `queue_position`. |
| `ciel_runtime_support/side_intake_mcp.py` | Resolve schema adds `kind` and `queue_position`. The claim carries `work_digest` and `source_read`. Resolve releases a changed create for one recheck. |
| `ciel_runtime_support/side_intake_processing.py` | `release_for_recheck`: pops the claim and counts `rechecks`. |
| `ciel_runtime_support/side_reply_connector.py` | Walkie preset `{source, receiver_id, preset:'walkie', server}`. `walkie_target` reads only `data.conversation` (UUID) and `data.message_id` (digits). `source_read` builds the prefetch. The send uses a deterministic `client_id` taken from `sha256(receipt_id + ':final')`. |
| `ciel_runtime_support/side_reply_outbox.py` | Sends `final_text`, passing the original event to the connector. |
| `ciel_runtime_support/side_code_read.py` (new) | `read_code` and `search_code`, bounded to `CIEL_RUNTIME_LAUNCH_CWD`. Rejects a symlinked root, absolute/UNC/`..` paths and secret names. Output is bounded (80 matches, 5000 files, 64 MiB) and redacted. |
| `ciel_runtime_support/side_context_store.py` | `redact_text` helper. |
| `ciel_runtime_support/side_context_router.py` | `_mark_parent_cache_boundary` on the selected projection, used only while fewer than 4 `cache_control` marks exist. |
| `ciel_runtime_support/side_workload.py` | `queue_order` comes from `queued_roots`. |
| `ciel_runtime_support/work_mcp_tools.py`, `work_mode_launch.py` | The code tools are added to the router tool lists. `dispatch_work_tool` serves them before the identity check, with no host token, only while side intake is enabled: the actor's native call carries neither, because the engine skips the plugin hook for agents it spawned. `work_side_drain` never admits; classification stays with the side actor. |
| `ciel_runtime_support/claude_mods/ciel-work/hooks/register.js` | The pane shows host outbox delivery states and the committed queue line. The stale `external ACK: not_sent` detail is removed. |
| `recent_menu_text.py`, `work_settings_menu.py` | "External reply route / Walkie preset (JSON)", with en/ko/ja/zh descriptions. |
| `docs/CLI-Reference.md` | Side inheritance and the reply route. |

# Tests

- `tests/test_work_plan_tree.py`: reorder positions; the active root is fixed; an invalid position is rejected.
- `tests/test_side_work_decision.py`:
  - kind ↔ action mapping;
  - status/info/clarify add no node and end with the no-queue line;
  - create/update/prioritize at given positions, with the committed position in `final_text`;
  - a changed digest raises `WorkChanged`.
- `tests/test_side_reply_connector.py`: Walkie preset validation, target extraction, prefetch spec, deterministic send `client_id`.
- `tests/test_side_reply_outbox.py`: `final_text` sent through the preset.
- `tests/test_side_code_read.py` (new): traversal, UNC, absolute paths, secret files and a symlinked root are denied; output is bounded and redacted.
- `tests/test_side_intake_mcp.py`:
  - a status answer makes no queue entry;
  - a changed create is rechecked once and sends nothing;
  - the claim carries the prefetch spec;
  - a native code read/search with no `session_id` and no `_host` succeeds, stays bounded and is refused when side intake is off.
- `tests/test_work_mod_write_gate.py`: the side pane has no `external ACK` line and shows the host delivery.
- `tests/test_side_intake_mod.py` + `tests/side_intake_harness.mjs`:
  - the actor tool allowlist (no send, no Work tools);
  - `maxTurns` 4 / `effort` low / `omitClaudeMd`;
  - the prompt text;
  - `source_message` in the payload;
  - the menu text.
- `tests/test_side_context_router.py`, `tests/test_side_context_router_boundary.py`: the cache mark and the 4-mark limit.
- `tests/test_work_mcp_tools.py`: the tool schema list; `work_side_drain` keeps status mail pending for classification.
