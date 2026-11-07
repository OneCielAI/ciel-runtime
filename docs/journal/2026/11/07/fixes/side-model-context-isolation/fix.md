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

# Superseding live evidence and fallback policy

The installed `cc9fd3f` was relaunched in the real Walkie workspace. Nine receipts failed to answer: seven requests correctly resolved the alias to `API KEYS - SSE`, but the gateway returned `404 Combo has no executable targets`; two requests were claimed before the first parent capture, retained leases for 120 seconds, then ended in a gateway `500 transient retry deadline`. The generic `reason=error → model_error` mapping quarantined all nine, and `$.ui.status` leaked `Side processing retained` into the main status line. This contradicts the earlier acceptance conclusion that quarantine was sufficient.

Commit `8c7d5ac` supersedes that behavior:

- full/recent events wait unleased until the exact parent capture is ready;
- post-claim context/spawn/send/heartbeat failures release the generation immediately;
- the child transcript gets a bounded redacted parent transcript preview while router projection remains authoritative and injects the exact durable external event once;
- router-owned side outcomes record bounded typed HTTP/model diagnostics;
- when the selected side model is unavailable, the receipt is generation-fenced and the original untrusted SSE event is queued into the main session exactly once. This is the only additional main-transcript exception beyond explicit advise/prioritize handoff;
- all routine side failure status/log output remains outside the main UI.

Controlled native success proves normal side delivery (`side_mcp`, pending 0, full context, inherited Walkie MCP). The reviewed lifecycle and failed-actor cleanup shipped as beta `0.2.51-beta.20261107-071057.7606864` from exact git head `76068646b72675b35fb57b666c24645fc366ab4b`; registry integrity and 506 source files matched while latest/nightly stayed unchanged. The verified archive was installed as immutable snapshot `C:/Users/djlov/.local/share/ciel-runtime-76068646b72675b35fb57b666c24645fc366ab4b` without restarting live sessions.

Installed native acceptance selected `_Walkie_Haiku_Terra`, reached the child wire as `ciel-runtime-cielairouter-_walkie_haiku_terra`, projected the exact main request into the side request, removed display-preview duplication before upstream, selected full context at 100%, sent through inherited Walkie MCP, and finished with pending 0. The real Walkie gateway independently proved the alias resolves to `_Walkie_Haiku_Terra`; its `404 Combo has no executable targets` means the combo currently has no executable backend, not that the runtime failed to find the catalog model. Existing Walkie processes still run the older `cc9fd3f` snapshot until explicitly relaunched.

Additional evidence:

- [Installed Haiku full-context E2E](evidence/installed-haiku-context-7606864.json)
- [Installed Haiku side pane](evidence/installed-haiku-pane-7606864.png)
- [Beta registry verification](evidence/beta-registry-7606864.json)
- [Installed SSE processing with full main context](evidence/installed-sse-processing-0ca35db.json)
- [Full-context default beta registry verification](evidence/beta-registry-0ca35db.json)

# Final live diagnosis and correction

The real `G:/ciel-Walkie` launch on `82e2e28` exposed a second exact mismatch. Its persisted side settings omitted `context_mode`, while the prepared actor profile correctly defaulted to `full`; `side_context_router.project()` compared the actor profile against raw `settings.get('context_mode') == None` and rejected projection with `409 SideContextUnavailable`. The side call then retried as an unprojected two-message request. Commit `0ca35db` applies the same semantic default (`full`) at the router comparison boundary.

Beta `0.2.51-beta.20261107-143907.0ca35db` passed every CI group, matched exact git head `0ca35dbd3ed8fc0fcdffe3f064e3aa815a2d0205`, preserved latest/nightly, and was installed as an immutable snapshot. Installed acceptance using the configured `_API_KEYS-SSE-1` identity proved the side request contained both `MAIN REQUEST: keep working.` and the new SSE question, selected full context at 100%, used inherited Walkie MCP exactly once, resolved while main remained busy, and finished with pending 0. The controlled provider accepts the request; the real gateway may still return `Combo has no executable targets`, in which case the typed unavailable-model policy moves the exact event to the main session once.
