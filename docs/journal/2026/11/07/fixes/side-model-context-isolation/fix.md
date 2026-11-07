---
type: fix
title: Side model context and transcript isolation
description: Side SSE events now use provider-routed model aliases, full parent context by default, and no routine main transcript rows.
tags: [side-intake, sse, model-routing, context, transcript]
timestamp: 2026-11-07
---

# Observed production failure

- `G:/ciel-Walkie` received and durably staged the question `지금 하고있는일은 어떤거지?`.
- The side agent metadata and wire transcript named raw model `API KEYS - SSE`; Claude Code rejected it with `404 model_not_found` before a decision or MCP reply.
- The receipt stayed `received` with a processing lease and was attempted repeatedly.
- Routine `Side processing`, parity, retained-event, and agent failure notifications entered the main terminal and JSONL.
- The side context default was `isolated`, so the side request did not receive the parent transcript.

# Fix

- Python projects persisted catalog model IDs to canonical Claude-facing provider aliases; the persisted setting remains unchanged.
- Missing context mode now means `full`; router-captured parent system, tools and messages are projected only into the side request.
- Routine receipt/progress/model/reply logs were removed from the main terminal. Exact owned side task notifications are consumed at `session.receive`, before queue, screen, transcript or model ingestion.
- Failed claims record a generation-fenced durable failure. Invalid models are quarantined by model fingerprint; transient failures back off. A changed model fingerprint makes the event eligible again.
- The side pane remains the observable surface for receipt, context, MCP parity, reply and failure state. Explicit `prioritize`/`advise` handoff remains the only side append to main.

# Source verification

- Focused side/model/context/mod/Work tests: 100 passed.
- Owned native Claude Code E2E: configured catalog model `API KEYS - SSE` reached the side wire as `ciel-runtime-cielairouter-api-keys-sse`; full context selected 100%, 41-node board, two side model calls, inherited Walkie-compatible MCP send while main was busy, `side_mcp`, pending 0.
- Owned terminal output contains none of `Side processing`, `Side processed`, `Side MCP parity`, `Side event`, or owned task-notification rows.

# Release and installed acceptance

- Beta `0.2.51-beta.20261107-032628.cc9fd3f` was published from exact git head `cc9fd3fe866c5dc50d1d7bba6494b3441e985288`.
- Registry SHA-512 and 505 source files matched. Protected tags remained `latest=0.2.51` and `nightly=0.2.51-nightly.20261106-020333.c00c8b6`.
- The verified archive was installed globally and as immutable snapshot `C:/Users/djlov/.local/share/ciel-runtime-cc9fd3fe866c5dc50d1d7bba6494b3441e985288`; 559 package files matched and six isolated installed CLI commands passed.
- Installed native acceptance resolved the Walkie-shaped question while main was busy: 41 Work nodes, pending 0, action `answer`, delivery `side_mcp`, model alias `ciel-runtime-cielairouter-api-keys-sse`, full context selected 100%, and inherited `owned-walkie` MCP send.
- The captured main screen contained zero routine receipt/progress/parity/event/task-notification/reply-status rows. The side pane retained the decision, model/context, delivery and redacted original event.
- No live runtime or Walkie session was restarted. Existing processes continue on their launch snapshot until explicitly relaunched.

# Evidence

- [Registry verification](evidence/beta-registry-verification.json)
- [Installed package smoke](evidence/installed-beta-smoke-cc9fd3f.json)
- [Installed native E2E](evidence/installed-native-e2e.json)
- [Installed side pane screenshot](evidence/installed-side-pane.png)
- [Captured main terminal](evidence/installed-main-screen.txt)

# Known boundary

The owned acceptance proves alias routing, full context projection, inherited MCP use and transcript isolation against controlled provider/MCP endpoints. It does not prove that the real upstream `API KEYS - SSE` combo currently has an executable target. Earlier real-provider evidence returned `no_executable_targets`; the new lifecycle quarantines that failure instead of retrying continuously.
