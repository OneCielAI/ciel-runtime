---
type: verification
title: SSE side admission verification
description: Automated and owned-console evidence for the 32K limit, raw fallback, cursor progress and side-only delivery.
tags: [verification, sse, side-intake, beta]
timestamp: 2026-11-08
---

# Automated verification

| Check | Result |
| --- | --- |
| Focused fallback and transport suites | 146 tests passed, 1 skipped |
| Full unit CI group | 3,427 passed, 50 skipped |
| Full router CI group | 1,543 passed |
| Full channel CI group | 606 passed, 80 skipped |
| Full runtime CI group | 458 passed, 19 skipped |
| Ruff 0.15.22 on changed Python files | passed |
| Observatory plugin focused tests | 5 passed |
| Whole plugin suite | baseline harness failure: 23 passed, 77 failed because test doubles did not implement `clock.after`/`ui.log`; no plugin source changed in this fix |

The focused tests include:

- the captured 16,055-character Prody shape followed by a canary;
- exact 32,000-character four-byte text across receive, restart, claim and finish;
- 32,001-character ordinary rejection and raw fallback acceptance;
- a too-large-for-record content-free notice while the exact queue body is retained;
- malformed frames with and without transport identity;
- dead-letter cursor and rejected-ACK ordering;
- queue/dead-letter storage failure retaining the cursor;
- redelivery identity deduplication;
- side rows never typed, router-injected, batched or main-fallback delivered.

# Live-router safety

The supported isolated test entrypoint was used with inherited `CIEL_RUNTIME_*` variables removed. Local live router PIDs were identical before and after all CI groups:

- port 9505: PID 41716
- port 9489: PID 36140
- port 9524: PID 19788

# Owned isolated console proof

Pending. The probe uses a new workspace, a copied sanitized config, an owned router port and an owned console. It sends four frames in order: 16,055-character Prody shape, over-32K valid event, malformed frame with transport id, and canary. Evidence is recorded under [evidence](evidence/index.md).

# Beta and Prody pickup

Pending until the owned proof passes, the intended diff is committed, beta CI/publish succeeds, the registry artifact is verified and installed locally, and Prody updates through its normal AAP path.
