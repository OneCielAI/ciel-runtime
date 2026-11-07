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

# Release status

Not published or installed yet. Beta-only publication and installed acceptance remain required; stable/nightly must remain unchanged and live Walkie sessions must not be restarted automatically.
