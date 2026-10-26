---
type: Diagnostic
title: Celly stops work after compaction
description: User requests continuation of unfinished work after a successful compact; mechanism under investigation.
tags: [claude, compaction, continuation]
timestamp: 2026-10-22
---

# Scope

Distinguish successful automatic mid-turn compaction from manually invoked idle compact, error/cancel and intentional completion. Reuse native continuation semantics; do not blindly inject a new turn on every PostCompact.

# Current observation

Router logs include end_turn at19:35:03 before native_compact_started19:35:08, transcript summary19:38:09, then next input around19:57. This ordering alone does not prove a mid-turn continuation was lost. Exact transcript boundary and native hook payload inspection in progress. No live session input or queue changed.

# Implementation boundary

A safe once-only continuation cannot be implemented from the current observables without adding a new native lifecycle signal or an explicit continuation intent contract. The existing durable request is only `action=compact` with `source`, `reason`, and expiry; the request repository does not preserve the exact session/epoch or a continuation disposition. The controller records `awaiting_shrink` after a matching checkpoint, but its ports expose no transcript event stream for distinguishing a newer user prompt, cancellation, or native continuation after that checkpoint. The terminal poller exposes only a current busy/draft snapshot, not a monotonic input/cancel epoch. Therefore a controller that injects after `awaiting_shrink` could not prove the required once-only/scope/new-input suppression conditions. Reusing the existing error-recovery path would violate the requested boundary because that path is keyed to streamed API failures and has separate eligibility/claim semantics.

The official Claude Code command reference confirms `/compact [instructions]` accepts optional **summary focus instructions**, not a documented execute-after-compact argument. The hook reference lists `PostCompact`, but the runtime's tool guard currently handles it as observe-only; neither that hook's existence nor a compact boundary alone establishes a pending continuation intent. No product behavior was changed; the focused tests record the fail-closed boundary instead.

# Verification

Added seven characterization tests for runtime queue metadata, checkpoint-only waiting, absent/wrong-epoch proof, uncertain command submission followed by a checkpoint, unchanged manual semantics, release on a real smaller request, and completed/cancelled operator input disappearing from the current draft boolean. An exact-file isolated run of these and five existing controller/binding/injection suites passed **89 tests**. Runtime/Claude/Anthropic environment variables were removed before the run and the repository's `isolate_runtime_state()` helper supplied private test state. Captured output: [test log](evidence/continuation-boundary-tests.txt).

This is a blocker investigation, not implementation completion. Successful automatic continuation, suppression races, and real native CLI behavior were not verified. No live session was injected, restarted, or otherwise changed.

# Citations

- [Claude Code command reference](https://code.claude.com/docs/en/commands.md), `/compact [instructions]`.
- [Claude Code hook reference](https://code.claude.com/docs/en/hooks), `PostCompact` lifecycle entry.
- Source: `channel_compact_request_repository.py:54-79`, `runtime_compaction_control.py:291-310, 414-465, 475-540`, `channel_compact_injection.py:73-125`, `windows_conpty.py:975-1051`, `claude_stream_recovery_runtime.py:419-460`.
