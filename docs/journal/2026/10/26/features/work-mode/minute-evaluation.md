---
type: Evaluation
title: One-minute amendment coding evaluation
description: Three fresh complex problems with nine one-minute changes and independent final-artifact tests.
tags: [work, evaluation, timing, context, amendments]
timestamp: 2026-10-26T15:30:00Z
---
# User request

Evaluate three complex coding tasks again, inject three amendments for EACH task at one-minute intervals while work proceeds, and verify final artifacts rather than accepting model claims.

# Prescribed schedule

All original A0/B0/C0 specifications arrive together through a file Read whose full contents are verified in the transcript. After observing actual Write/Edit/Bash coding activity, a monotonic clock starts. Each minute one independent amendment is injected through authenticated session_socket using actual router MCP submit_input:

| Offset | Amendment | Key requirement |
| --- | --- | --- |
| 60s | A1 | blocked nodes and constrained max hops |
| 120s | B1 | atomic reservation batch and replacement |
| 180s | C1 | atomic inventory batch with ordered operations |
| 240s | A2 | frozen graph snapshots, atomic optimistic apply/version |
| 300s | B2 | wait priority descending then FIFO, skip nonfitting |
| 360s | C2 | undo net deltas, tombstones, retry failed undo |
| 420s | A3 | supersede path tie to lexical, explicit original-policy mode |
| 480s | B3 | same-calendar immutable checkpoints and version semantics |
| 540s | C3 | same-stream checkpoint including buffer, atomic line overflow |
| 548s | FINAL | verify all twelve requirement IDs and finish |

A0 is a directed weighted shortest-path engine with simple paths including zero-cost cycles. B0 is a cumulative-capacity half-open booking calendar. C0 is a fragmented JSONL inventory event engine with idempotency/conflicting duplicates. Distinct ordering and checkpoint rules are deliberately used to expose cross-task contamination.

# Controls and scoring

Real deployed breakdown-enabled runtime snapshot, real installed Claude CLI, configured real cielairouter _Walkie_Opus55-Sol61-Fast model. Fresh owned temporary HOME/config/workspace and router port. Existing live user sessions are not targets and no existing cursor/queue is modified. Credentials copied only into the temporary owned config, never printed or included in evidence. The evaluated implementation must not be repaired by the evaluator.

An independently authored test suite uses only [the specification](evidence/minute-eval/requests.json), not generated code. [Assessment](evidence/minute-eval/assess.py) checks original Read completeness, exact addition text, exactly-once receipts, nine amendments/three per task, actual dispatch intervals, activity evidence, plan branches and verified leaves. Clock cadence and workload overlap are reported separately: a regular timer does not prove the model was busy.

Final artifacts are tested by independent unittest assertions. Failures are classified as delivery/timing, context requirement retention, algorithm correctness, or Work bookkeeping. One passing run is evidence for this prescribed workload only, not universal proof of no context interference.

# Status

Two scored runs completed with the same frozen specification (sha256 `088093d0…`) on the deployed breakdown snapshot. The original `independent_verify.py` had become 23,482 NUL bytes; it is preserved as `evidence/minute-eval/independent_verify.corrupt.bin` and was re-authored from the specification only, then hash-frozen before any generated code was imported ([provenance](evidence/minute-eval/rerun/verifier-provenance.json), verifier sha256 `8261b2ec…`, 38 tests). Generated code was not modified; workspace hashes match the assessment records.

# Results

| Check | run-2e0993a3 (earlier) | run-4997239b (rerun 2026-10-26 10:06–10:21 CDT) |
| --- | --- | --- |
| Full initial A0/B0/C0 Read verified | yes | yes |
| Amendments received exactly once with full text | 9 + FINAL | 9 + FINAL |
| Dispatch intervals | 59.99–60.01 s | 59.988–60.008 s |
| Model active at every injection | 9/9 | 9/9 |
| Plan: A/B/C branches, every leaf done with evidence | yes (39 nodes) | yes (51 nodes) |
| Requirement IDs only under their own branch ([audit](evidence/minute-eval/rerun/audit_context.py)) | yes, no cross-branch IDs | yes, no cross-branch IDs |
| completion.json maps all twelve IDs | yes (`requests` map) | yes (`requests` map) |
| Independent verifier | 37/38; one disputed B3 assertion | 38/38, all twelve IDs passed |
| Live router PIDs 57432/25276 unchanged | yes | yes ([pre](evidence/minute-eval/rerun/preflight.json)/[post](evidence/minute-eval/rerun/postflight.json)) |

The earlier run's first assessment listed all twelve IDs as missing from completion.json because the report nests them under `requests` with `REQ-` keys; the corrected assess.py records the structure and finds none missing.

# Disputed assertion (run-2e0993a3)

`CalendarContract.test_checkpoint_restores_original_wait_sequence` waits `a`, checkpoints, cancels and re-waits `a`, then restores and expects the version to increment. The implementation treated the restore as a no-op (version 3, not 4). Bookings, priority and FIFO order are identical before and after, so the B3 rules "already identical state no-op" and "restores original waiting insertion sequence" do not settle whether an unobservable insertion counter counts as state. A genuine two-item FIFO reorder restored `["b","a"]` to `["a","b"]` and incremented 4→5 ([classification](evidence/minute-eval/run-2e0993a3/independent-classification.json)). The assertion is kept unchanged and is reported as ambiguous, neither a pass nor a confirmed defect.

# Limits

- Scope is one conversation holding three task contexts plus timed amendments. Concurrent isolation between separate conversations is covered by [session isolation](session-isolation.md), not by this run.
- Two passing runs are evidence for this workload only, not a general proof that contexts never interfere.
- The verifier author's statement that it read no generated code before the hash freeze is recorded in the provenance file; read history itself is not machine-verifiable.
- The tested snapshot is the selected breakdown snapshot; the newer staged navigation snapshot differs in `work_mcp_tools.py` and the mod `register.js` ([hashes](evidence/minute-eval/rerun/tested-snapshot.json)).
