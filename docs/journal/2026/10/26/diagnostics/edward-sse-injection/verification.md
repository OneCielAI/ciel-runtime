---
type: Verification
title: Edward background SSE handoff verification
description: Before/after native CLI evidence for exact parked background delivery and focused regressions; release pending.
tags: [sse, handoff, verification]
timestamp: 2026-10-27T03:35:00Z
---
# Native reproduction

- Old installed snapshot e6514c8, actual Claude Code 2.1.310: original socket receives the first SSE; native backgrounding writes parkedJobId and starts a bg peer; subsequent socket stays on the original, binding/transcript do not follow, no after-event request reaches bg ([baseline](evidence/baseline-7c708507/result.json)). This is an expected failure, not a passing run.
- Fixed source, same actual CLI: production SSE receiver -> runtime-input gateway queue -> pending injection -> native bg messaging socket -> model response. Both events progress queued/submitted/replied exactly once; all checks pass ([SSE proof](evidence/sse-4cdae182/result.json)).
- Full production launcher and HTTP-configured receiver: before and after events reach the correct session, bg successor scope and transcript are followed; durable status records both replies ([full proof](evidence/full-b7fe26b2/result.json), [status](evidence/full-b7fe26b2/runtime-input-status.jsonl), [before PNG](evidence/full-b7fe26b2/01-before-background.png), [after PNG](evidence/full-b7fe26b2/02-background-injected.png)).
- Live router 9505 pid77432 fingerprint72c6bfb456e30cd7 and 9524 pid25276 fingerprintf86ee3638ee760f3 unchanged. Isolated HOME/config/ports, inherited provider environment stripped, loopback model, egress sentinel; only harness-owned windows/processes stopped.

# Regression and limits

- 367 focused Python tests pass, 42 skipped: handoff identity/key/dead/reused/ambiguous peers, exact no-write deferred attempt, epoch preservation, background write uncertainty, direct SSE pipeline, transcript and architecture contracts. Whole repo Ruff passes.
- The no-write-only handoff exception can requeue its own attempt; failed/uncertain transport writes never fall back to the parked TTY and are not blindly retried. Native session/epoch remains exact; no newest-file heuristic.
- First harness attempt held the peer message because it omitted production crossSessionInbound accept. Second failed its immediate transcript assertion before the native file/TTL settled; retry waits up to 15 s. These are harness failures. Architecture test initially caught a static import cycle; canonical socket paths moved to a leaf module and revalidated.
- One focused sweep used a globally pinned CLAUDE_CONFIG_DIR against per-test key dirs and failed four fixture tests; fixtures now pin their own config dir. Re-run passed. No live target was modified.
- Installation and nightly verification passed; exact published version and proof are recorded below.

# Review follow-up

- Added published-record read-gap retention (including an unreadable first key owner's JSON), canonical coordinate comparison, definitely-unwritten connect failures deferred instead of permanently failed, and queued status event publication. Partial/uncertain writes remain quarantined.
- [Real production-launcher rerun](evidence/review-full-64116bea/result.json): every check passed after these changes; [background-injected screenshot](evidence/review-full-64116bea/02-background-injected.png). No live router changed.
- The native creation-token handoff verification is directly exercised on Windows. macOS identity verification is unavailable in the existing process helper and therefore fails closed; no unsupported identity is accepted. No timeout-based fallback to a parked TTY is added.

- Final review regression run: 355 tests OK (42 skipped), mod 48/48 pass, full-repository Ruff passes. A prior command misspelled test_architecture_contracts as singular and failed import; corrected invocation passed.

# Published installation

Installed exact nightly 0.2.51-nightly.20261027-031627.0555b6c; native CLI installed checks pass. See release proof at `docs/journal/2026/10/26/releases/edward-ara-nightly/verification.md`. Local live sessions unchanged; .14 was not installed/restarted.
