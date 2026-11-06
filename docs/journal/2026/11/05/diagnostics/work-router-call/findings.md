---
type: diagnostic
title: Work guard MCP call failure
description: User supplied Edit denial correlation wg-mw4dy55o-1 despite SSE identity-ready and open state.
tags: [work, mcp, gate, diagnostic]
timestamp: 2026-11-05
---

# Evidence

User pasted an Edit failure for `.local/wt/61802-profiles/server/test/workflow-p8-room-entry.mjs`: `Work status unavailable ... [router-call; wg-mw4dy55o-1]`, followed by Work unavailable and auto-continue status read failure. The pasted session reports Runtime error report112190 and concurrent SSE identity_ready=true/gate=open.

Source claude_mods/ciel-work/hooks/register.js226-239: readSnapshot calls native $.mcp.call(ciel-runtime-router,work_status,format=json,exact session). Catch maps exceptions to router-timeout or router-call and discards original error details. gateRead records generated correlation and fixed stage. Therefore router-call is evidence of a thrown MCP call, not evidence of Work board denial or SSE gate state.

# Read-only checks

Exact correlation not found in local Claude debug files,8872 or9505 recent2000event snapshots, or local router log search.8872 is Grant/ganti-ai;9505 is G drive Walkie. Pasted path alone does not establish which session produced it. No log access bypass, session restart, Work-off change or shell write bypass performed.

# Verified defensive handling, 2026-11-06

The mod now distinguishes fixed secret-safe `router-timeout`, `router-disconnected`, `router-auth` and `router-call` stages. Only a positively identified disconnected MCP attempts `$.mcp.connect`, then one pure `work_status` reread with unchanged exact session identity. Auth/policy failures stay closed; Edit/Write is not replayed. Owned native-hook regressions verify this behavior. This is not proof of the historical correlation's cause.

# Status

Cause unconfirmed. Need target native debug MCP connection evidence around the correlation/time, or an owned reproduction of the same native MCP call failure before selecting recovery behavior. Do not relax the verified-state write guard or use stale cached approval when current MCP status is unavailable. Diagnostic detail should remain secret-safe; raw error strings can contain endpoints or secrets and cannot be copied indiscriminately to UI.
