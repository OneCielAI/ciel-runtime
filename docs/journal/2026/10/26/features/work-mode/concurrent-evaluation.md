---
type: Evaluation
title: Mid-task coding request isolation
description: Real-model evaluation with three independent coding tasks and repeated in-flight requirement changes.
tags: [work, evaluation, concurrency, context]
timestamp: 2026-10-26T12:00:00Z
---
# Protocol

The user requested complex coding problems and several additional requests while work is already in progress. The evaluation uses the locally deployed runtime snapshot and real Claude CLI with the real configured cielairouter _Walkie_Opus55-Sol61-Fast model. It is not a scripted fake model test. All work occurs in an owned isolated workspace with isolated home/config and its own router port; existing user sessions are not targets of input injection.

# Tasks and deliberate interference

- A0: TTL/MVCC cache, immutable snapshots, optimistic transactions with read/write conflict detection.
- B0: dependency DAG scheduler with readiness, priorities and state transitions.
- C0: fragmented RFC4180 CSV reconciliation using exact Decimal arithmetic.
- A1 while active: bounded FIFO eviction, not LRU; overwrite/read do not refresh insertion order; eviction conflicts with prior transactions.
- B1 while active: atomic dependency changes/cycle rejection/retry; reverse ONLY the equal-priority tiebreak from lexical to insertion order.
- C1 while active: duplicate ids are last-row-wins across account changes; validate overwritten rows; retain zero totals.
- FINAL: consolidate all six requirement IDs, verify, preserve A/B/C branches and finish honestly.

A policy from one task must not leak into another. The request schedule and text are in [requests](evidence/concurrent-eval/requests.json). The harness records transcript/model activity at each injection and submits additions through actual router MCP submit_input using session_socket, not by rewriting the transcript.

# Independent criteria

[Independent verifier](evidence/concurrent-eval/independent_verify.py) is kept outside the evaluated workspace: eleven cache tests, seven scheduler tests and five ledger tests. It checks the original requirements and later amendments including FIFO-vs-LRU, insertion-vs-lexical, and last-row-vs-sum policies.

[Transcript assessor](evidence/concurrent-eval/assess.py) enumerates received request IDs, duplicate/missing requests, Work tool operations, persisted plan, and observed activity before each injection. Screenshots and console captures are retained in each run. A run is not a pass merely because the model says EVAL-COMPLETE. Upstream errors, transport losses, model mistakes and Work bookkeeping failures are reported separately.

# First attempted run

run-b20040c5 did not reach a valid coding evaluation. The isolated CLI used CLAUDE_CONFIG_DIR=<scratch>/claude, while the session-socket client searched <HOME>/.claude/sessions. The key was written under CLAUDE_CONFIG_DIR; lookup logged auth_key_unavailable and fell back to terminal/router. All six submissions then recorded prompt_not_submitted; router returned 503 channel_injection_deferred behind TTY message 1. No A/B/C implementation files were produced. This is a transport/configuration failure, not evidence of context retention success or model coding failure. Existing live router PIDs remained unchanged. The owned test session was stopped and evidence retained.

The second run aligns CLAUDE_CONFIG_DIR with the isolated HOME/.claude directory and repeats the same tasks and injection schedule. No product workaround was deployed; this only corrects the test sandbox directory mismatch. Initial local harness syntax failures occurred before model launch and are not model/runtime findings.

# Status

The second attempt run-e4a8df99 confirmed six socket injections, but the long initial A0 prompt was truncated by console typing: its stored user entry started midway through A0 and omitted the API specification. This is a harness input failure and was not scored. The next run places A0 in initial_request.txt and sends a short console instruction, requiring complete specification evidence in the Read result before injecting B0. The workspace is outside the source repository. Further additions still use actual session-socket delivery.

The third attempt run-7a2f7f19 was blocked before Claude startup: provider base URL preflight returned TimeoutError. No requirements were submitted and no coding score assigned. A fresh owned retry uses the unchanged corrected protocol.

# Status update

Valid complete-input evaluation retried; no coding pass/fail conclusion recorded yet. Independent test suite contains 23 tests; its criteria were saved before examining generated implementations.

# Valid run result

[run-f5587ac4](evidence/concurrent-eval/run-f5587ac4/result.json) completed without driver timeout/error. Initial full A0 spec was confirmed in Read output before further submissions. Six additions B0/C0/A1/B1/C1/FINAL were injected through the real authenticated session socket during observed activity. [Assessment](evidence/concurrent-eval/run-f5587ac4/assessment.json) confirms each request once, exact complete addition text, no missing request, retained plan and verified done leaves. Existing live router PIDs unchanged.

[Independent tests](evidence/concurrent-eval/run-f5587ac4/independent-tests.txt): **23 passed, zero failures/errors** across cache (11), scheduler (7), ledger (5). Evaluated implementation was not repaired by the evaluator. This run found no missing/cross-applied requirement under the prescribed interleaving. It does not establish universal context isolation across all models or workloads. Screenshot captures and the complete evaluation transcript are retained locally; the transcript may contain inherited instruction attachments and is not published.
