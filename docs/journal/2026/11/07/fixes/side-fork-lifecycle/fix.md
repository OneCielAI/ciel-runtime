---
type: fix
title: Bounded context-aware SSE side agents
description: "SSE side processing now uses a one-event native fork for inherit mode, terminates every owned actor, preserves selectable same-provider models, and exposes configurable ACK/reply/priority instructions."
tags: [sse, side-intake, fork, agent-lifecycle, mcp, work-priority]
timestamp: 2026-11-07
---

# Verified production failures

- Local `f37cbac` started one `in_process_teammate` per retained SSE and left each idle. The live team roster reached 18 members within minutes; the session has 522 historical subagent files.
- No newly processed receipt completed. Failed events reached attempt 8 and were quarantined.
- After `--continue`, Claude Code 2.1.310 appended a newline after the `<teammate-message>` wrapper. Exact envelope matching failed, so side requests used `tools=36 msgs=2` rather than the parent `tools=93` plus projected history.
- Side turns did call Walkie MCP tools, but final output was often prose/fenced JSON. The host rejected it and retried, risking duplicate external effects.

# Fix (`3638400`)

- `inherit` mode spawns a native unnamed `fork`, so it receives the main session transcript, system, model, MCP servers and credentials.
- A separately selected model from the current provider remains available. It uses the existing routed parent-context projection and main session connection/auth.
- Every event owns one actor. Success, failure and lease expiry all call `TaskStop`; session start also stops every owned idle/completed side actor. Delayed TaskStop notifications remain isolated from main through a bounded tombstone set.
- Trailing-newline teammate wrappers are recognized for selected-model context projection.
- Fenced JSON decisions are accepted; invalid final decisions are quarantined rather than replayed after possible MCP side effects.
- Configurable `work_side_intake_instruction` is exposed in en/ko/ja/zh.
- Default policy requires exact-source ACK first, detailed source/thread inspection, immediate answer for simple questions, and a P1–P5 root for work that cannot be completed immediately. A P1 root becomes the first pending root for main parallel pickup.
- If a selected model is unavailable, that exact event gets one side retry on the parent model. Raw SSE is never injected into main.

# Verification

- 441 focused tests passed; 42 skipped. All inherited `CIEL_RUNTIME_*` variables were unset.
- The lifecycle harness spawned two unnamed native forks for two events and called `TaskStop` for `actor1` and `actor2`; both actor records ended `retired=true`, `current=null` ([evidence](evidence/lifecycle-harness.json)).
- The harness accepted fenced JSON, preserved parent MCP policy, committed both decisions, and denied ordinary Work mutation from the side.
- Installed local `0.2.51-local.20261107-3638400`; User `CIEL_RUNTIME_HOME` points to its immutable snapshot.
- Live Walkie verification is intentionally not run: the user stopped the faulty session. No live session was restarted or killed by this task.
