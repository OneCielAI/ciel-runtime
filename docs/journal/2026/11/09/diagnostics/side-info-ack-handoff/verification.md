---
type: Verification Record
title: Side route fix verification
description: Unit tests and an owned real-model console A/B with no reply route configured (the KOBE condition).
timestamp: 2026-11-10T05:20:00Z
tags: [verification, side-actor, walkie]
---

# Automated

- `test_side_reply_connector.py`: 9 tests OK.
  - An implicit Walkie route without configuration.
  - Refusal for a foreign event type, a malformed message id, a non-external source, and an operator-configured route set.
  - KOBE's `task.auto_assigned` answer is prepared and recorded as `submitted`.
- `test_side_intake_mcp.py`: 27 tests OK. Work status reviews carry `route_unavailable` and `submitted` from the receipts.
- `test_work_mod_write_gate.py`: 49 tests OK.
  - The review prompt splits sent, unsent and in-progress replies.
  - A later `failed` state is notified again.
- `test_work_http_controller.py`, `test_side_reply_outbox.py`, `test_side_work_decision.py`, `test_side_intake_mod.py` and `test_work_live_settings.py`: all OK.
- Targeted runs used a temporary `CIEL_RUNTIME_CONFIG_DIR`. The real `config.json` mtime stayed at 19:10:37.

# Owned real console A/B

Script: [owned_side_route_probe.py](evidence/owned_side_route_probe.py), derived from the 2026-11-09 side contract probe.

Setup:
- isolated config copy, with `work_side_intake.reply_connectors` removed;
- real Claude Code and router;
- the real Walkie side model `_Walkie_Haiku_Terra`;
- an owned Walkie MCP server and an SSE source that sends Walkie-shaped events with an empty body (as Prody showed).

The events were a status question and an info question that needs a code read.

| | 4eebfdc (installed beta) | fixed worktree |
|---|---|---|
| Source prefetch reads | 0 | 2 |
| Decisions | clarify, clarify ("the message body is empty") | answer/status 7.1 s, answer/info 10.5 s |
| Delivery | `route_unavailable` × 2 | `submitted` × 2 |
| Walkie sends | 0 | 2, distinct client IDs, replying to 9001 and 9002 |
| Info answer | none | `MAX_DEPTH = 8` at `work_plan_tree.py` line 25 (checked: correct) |
| Raw event in main transcript | no | no |

Evidence:
- [before](evidence/owned-route-before-4eebfdc.json), [after](evidence/owned-route-after-fix.json)
- [thread after](evidence/owned-after-walkie-thread.txt)
- screenshots: [before](evidence/owned-before-04-info.png), [after](evidence/owned-after-04-info.png)
- Temporary config copies holding real keys were deleted after both runs.

# Not yet verified

- KOBE runs 4eebfdc. The fix takes effect only after KOBE installs the new beta and relaunches. This needs the operator's approval; KOBE is a managed machine.
