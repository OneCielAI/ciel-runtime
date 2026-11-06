---
type: journal
title: Primary side MCP parity and reply-route override wording
description: Primary side actor inherits main MCP per event boundary with a recorded count/digest; the reply connector setting is renamed so it is not mistaken for MCP inheritance.
tags: [side, mcp, reply, menu, verification]
timestamp: 2026-11-06
---

# Change

## Primary side MCP parity

- `side-intake.js` snapshots `$.tool.list()` MCP tools at each claimed side event, sorts names and server prefixes, and takes the first 16 hex characters of SHA-256 over `{names, servers}`. Only names enter the digest; no arguments, descriptions or credentials.
- The digest is part of the side conversation profile. An idle actor registered under the same digest is resumed; a changed digest registers `ciel-work:side-intake` again with the new tool/server lists and spawns a fresh actor at that event. A running actor keeps its registration and current event; nothing is cancelled.
- Only rows with `mcp === true` and a string name of the form `mcp__<server>__<tool>` are inherited; native tools, malformed names and non-boolean `mcp` flags are dropped before the digest.
- Registration is skipped when model and digest are unchanged. Each registration writes one transcript log line `Side MCP parity · tools N · servers M · digest D`; the `btw_with_mcp` pane header and `/btw_with_mcp status` (`mcp_parity`) show the current counts and digest.
- Non-Work MCP calls from the side actor return `next(e)` unchanged. The engine type contract documents that `next(e)` at `tool.call` runs core, including the permission prompt. No `permissionMode` is set on the agent, so the parent's mode applies.
- Work tools other than `work_status`, `work_parallel_status` and `work_side_resolve` remain denied. Shadow comparison actors are still registered with `tools: []` and `mcpServers: []`.

## Reply-route override wording

- `work_side_intake_reply_connectors` label: `Advanced external-event reply route override (JSON)` (en/ko/ja/zh, no "MCP" in the label).
- Description states that it is not MCP inheritance. Trusted correlated web chat replies automatically, while ordinary external SSE/CloudEvent replies need an exact `source` + `receiver_id` override and a server/tool already connected to main. The prelaunch menu fits descriptions to one line, so the distinction starts within the first 100 characters.
- Routing code is unchanged: `side_reply_outbox.prepare` still uses the correlated web-chat route automatically and the exact override for `external_event`; no match stays `route_unavailable`.
- `docs/CLI-Reference.md` describes the MCP inheritance behaviour and the override separately.

# Verification

- `tests/test_side_intake_mod.py` (3 tests) drives `side-intake.js` in Node: digest 2→3 tools after adding `calendar`, two registrations, actor1 keeps event `b` while actor2 takes `c`, same digest resumes actor2, replacing `calendar` with `drive` spawns actor4 with a third digest, non-Work MCP reached `next` once, `work_plan_update` denied, shadow registered with no tools. Harness output observed directly: digests `66b71cafde6c0c90`, `3c29987f84b3ed0e`, `8e13b4450898d3b1`.
- The harness fixture also contains `Read` (mcp false), `mcp__malformed`, `mcp: 'true'`, a nameless row and `null`; the first registration still lists exactly the two valid MCP tools.
- Focused suites (side mod/connector/outbox/mailbox/MCP, Work settings/parallel menu, provider option panel, Work mod write gate, Work MCP tools, packaging, network retry): 118 passed on a temporary export of HEAD with only this task's six source/test files overlaid, using an isolated config directory. The same run in the shared worktree failed 18 Work MCP tests: `runtime_paths.py` imports `release_workspace_router_launch_lease`, which the concurrently edited `workspace_router_selection.py` does not define. Neither file is part of this task.
- `ruff check --force-exclude` over all 1392 tracked Python files: pass (local ruff 0.14.11; CI pins 0.15.22). ES-module syntax check of both hooks and the harness: pass. `claude plugin validate` on `ciel-work`: passed (one existing icon warning).
- Hooks-host API check: a probe plugin holding a byte-identical copy of `side-intake.js` (sha256 `6bdea7c7…f63f4b`) ran under `claude plugin test` (Claude Code 2.1.318). That command loads the plugin through the engine's own host in the hooks environment. The probe's `command.run` called the real `startSide()`. Observed: `TextEncoder` and `crypto.subtle.digest` are functions; `process` and `require` are undefined, so this is not Node. Parity was `{toolCount:2, serverCount:2, digest:66b71cafde6c0c90}`, the same digest Node computed in the harness, and the log line was `Side MCP parity · tools 2 · servers 2 · digest 66b71cafde6c0c90`. Probe sources and output: `evidence/mcp-parity-probe/`. No pure-JS hash fallback was needed.
- Not performed: native CLI pane screenshot and interactive prelaunch menu capture of the new label. Running-session behaviour of an agent re-registration with a changed tool list is covered by the harness only.
