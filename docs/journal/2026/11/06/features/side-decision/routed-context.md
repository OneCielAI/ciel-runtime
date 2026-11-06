---
type: journal
title: Routed parent context and read-only comparison
description: Actual native CLI prefix preservation, recent token selection, full Work snapshots and shadow model experiments on the routed path.
tags: [context, router, comparison, work, verification]
timestamp: 2026-11-06
---

# Architecture correction

The earlier native-only conclusion remains true for the native APIs, but it did not exhaust the existing Ciel router's extension point. Owned wire probe45fe72f2 demonstrated that projecting a parent API request ahead of the native side loop still permits its normal MCP tool execution. Product code now uses that router boundary; it does not import or rewrite native transcript files, patch Claude internals, or serialize the whole parent transcript as quoted user text.

1. Only a main request bound by the existing runtime-compaction identity/launch epoch is captured. Native HTTP headers distinguish parent session and child agent; actual78a79ec7 confirmed main agent-header absent and side agent-header present under the same session header.
2. Host-only work_side_context binds an expiring event generation to a random context handle. The router checks parent session, native agent, event generation, provider and launch epoch. Unknown source, expired claim, profile drift, tool-set growth or unavailable count fails closed.
3. full preserves captured parent system, tool definitions/order and complete API messages. The native side event and Work board are appended. Same parent version reuses the actor; parent progress/compaction changes cause a new context generation at an event boundary, never a rewrite ahead of already-produced side reasoning.
4. recent uses the provider's actual /v1/messages/count_tokens endpoint, not the runtime's local estimator. Conversation-only token share excludes top-level system/tools and the full Work board. Complete user/tool boundaries may exceed30percent; actual ratio/count/source are stored and displayed. No character estimate fallback exists in production.
5. Recent or custom-model branches omit parent thinking/redacted_thinking blocks that cannot retain their original prefix binding; omitted count is explicit. Same-model full retains them. No provider cache-hit or preserved-thinking acceptance is inferred from loopback fixtures.
6. Context projection is routed-only. Direct gateway cannot capture parent requests and fails explicitly if full/recent/comparison is selected. isolated remains default to preserve functioning direct sessions and avoid silently adding parent prefill cost. Explicit full/recent settings are now wired, not stored-only.

# Current Work board contract

Every primary event receives the entire versioned Work projection: IDs/parents/titles/status/P1–P5/phases/waits/urgency/refs/notes/evidence, active root, focus and exact queue order. It is never reduced to20preview rows or included in the history percentage. Each shadow experiment freezes one identical current Work snapshot across all arms.

# Actual verification

- Full product projection03b2c217: captured main and side system exactly equal, tools exactly equal and all parent message objects present as an exact side-request prefix; side MCP executes and correlated external reply is submitted during main.
- Full/custom persistent30cc02b9: explicit fixture-side-model, two events processed before main ends, root1/pending0. Initial runsba5788ba/ea63a6be failed exact event fencing after native session.send wrapped resumed JSON in its coordinator prefix/suffix. Captured native request proved that wrapper; parser now accepts only the exact wrapper and matching host handle, and resumed run passed.
- Recent f8f096aa and f5a10f49: owned completed-history corpus, older marker excluded, recent marker retained, selected41.5percent at a complete boundary; full25item Work board reaches the wire in f5a10f49. Those CLI probes use deterministic count fixtures, not a real model tokenizer. Separate test_side_context_http uses a real loopback HTTP provider count endpoint through the production Claude router: counts1000/350, selected35percent, unchanged Work version99, upstream200.
- Comparison18e2312d: four profiles (full/recent × main/custom) all returned proposals, root1 and exactly one primary external reply. Parent capture and Work are frozen across arms. Actual usage is recorded with each arm; fixture values do not establish backend cost.
- Forced-tool shadow3a6ef010: four model responses deliberately emitted an external send tool despite tool_choice=none. Native shadow agent has tools=[]/mcpServers=[] and maxTurns1; no shadow external call occurred, only the primary reply. No shadow Work decision is applied. Invalid non-JSON proposals are recorded invalid_proposal, never success.
- Native retry284ae00c: explicitly focused receipt pane plus autoFocus retry control opened the real Cancel/Retry dialog; confirmation caused exactly one reset and one successful resend after initial failure. Earlier mouse/site-focus failures remain in the journal.
- Context/store/router focused tests cover wrong agent/parent/epoch, failed provider counts, complete tool groups, frozen repeated prefix, rebase, Work-independent percentage and shadow tool isolation. Side suite65, WorkMCP26, hook42, settings7, architecture263/42skip passed before final packaging cleanup. Latest production-handler/native four-profile probe afaf7460 verifies whole25item Work in every arm and41.5233percent recent selection from explicit fixture history. See [release validation](release-validation.md) for final-source gates and retained failures.

# Remaining verification / constraints

- API KEYS - SSE live inference cannot run: exact provider response404/model_not_found/no_executable_targets/poolSize0. No other model substituted. Real cache cost remains unknown.
- Direct-gateway full/recent is unsupported rather than silently falling back. History snapshot input is limited16MiB and explicitly rejected above it; no truncation is labelled full.
- Actual fixtures prove data flow, not open-ended semantic classification quality or real Walkie recipient delivery. External connector infrastructure uses operator-confirmed exact source/receiver/tool bindings; this session has no Walkie MCP contract.
- Full gatebhxi4dc9v passed its source snapshot (unit3319/50skip, router1526, channel604/80skip, runtime458/19skip). Independent review timed out before its final report but had identified two defects; both were independently reproduced with failing tests and fixed: provider changes now rebase even with identical request bytes; every projected actor refuses provider-hosted execution tools before binding. A further valid Unicode receipt overflow was reproduced and fixed with a separate2MiBreceipt ceiling,128KiBcomparison cap, and pre-write read-limit enforcement. Final full tests are rerunning after those fixes. No beta publish, install or live-session/router restart has occurred.
