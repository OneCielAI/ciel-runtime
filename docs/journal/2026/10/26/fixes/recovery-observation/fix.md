---
type: Recovery Observation Fix
title: Separate recovery observation from admission
description: Settles exact submitted wake evidence while preserving the no-replay recovery hold and release gate.
tags: [recovery, compaction, sse, socket, queue]
timestamp: 2026-10-27T04:35:00Z
---
# Change

`observe_pending_channel_inflight` now runs in the terminal proxy and direct
socket poller before pending-message admission. It can settle exact completed
and typed failed-attempt evidence for the current and backlog IDs, while
`admission_paused` disables age-based release. The existing
`recovery_protected` behavior remains in force for unknown or missing evidence.

Backlog attempt start times are retained so a typed failure is matched only to
the attempt that owns it. A scheduled retry retains a cursor ceiling below its ID even when a later send completes in the same observation pass. New queue scanning and injection still return early
while recovery admission is paused; after the gate releases, the queued batch
is delivered once.

# Evidence

- Final 63 focused tests (9 inflight, 27 pending poll, 22 dispatch, 5 SSE/socket), Ruff, and compile checks pass; see [focused-after.txt](evidence/focused-after.txt), the final added [retry-frontier regression](evidence/focused-final-retry-guard.txt), and [compile/lint](evidence/compile-lint.txt).
- [Baseline regression](evidence/baseline-regression.txt) loads the HEAD proxy/direct modules without altering files; the new five-ID paused-completion test fails with all five still `submitted`.
- The owned Windows protocol-child/SSE fixture is not native Claude Code. Its
  concrete batch evidence is [recovery-batches.json](evidence/socket-fixture/recovery-batches.json): IDs 1-3 settle while held, six admission checks occur,
  the gate releases, IDs 4-6 are submitted once, and every ID ends `replied`.
- The fixture logs show two socket injections and two confirmations, with no
  repeat scan IDs. The record is a protocol/queue observation, not a claim
  about native Claude internals.

# Scope and limits

Final runs use fresh temporary HOME, USERPROFILE, config, state and launch workspace per suite, after stripping inherited runtime/provider prefixes. An earlier runner set test-isolated mode without a fresh CONFIG_DIR; one import encountered `workspace router launch already reserved` and failed. It was not retried by clearing leases. No live router/queue mutation, install, commit, push, or provider configuration change was initiated.
The production fixture is an owned Python protocol child on Windows; native
Claude Code behavior is not proven by this fixture.
