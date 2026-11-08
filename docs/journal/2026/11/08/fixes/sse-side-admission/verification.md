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
| Focused side, receiver, ACK, transport and Codex suites (final tree) | 258 tests passed, 1 skipped |
| Review regression tests against `ede68b1` source | 7 of 7 fail there (5 failures, 2 errors); all pass with the fixes |
| Full unit CI group | 3,437 passed, 50 skipped |
| Full router CI group | 1,543 passed |
| Full channel CI group | 607 passed, 80 skipped |
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

Snapshot: `git archive` of commit `ede68b1`, launched in an owned console with a new workspace, a copied sanitized config, router port 9611 and a loopback SSE source. The source sent four frames in this order:

1. a 16,055-character Prody-shape CloudEvent (47,655 UTF-8 bytes);
2. a valid 32,259-character CloudEvent;
3. `not-json` carrying SSE id `invalid-t`;
4. a canary CloudEvent.

Result ([manifest](evidence/owned-console/result.json)):

| Event | Fallback | Queue row exact | Side stage | Fork action | Copies in main transcript |
| --- | --- | --- | --- | --- | --- |
| Prody shape, 16,055 chars | none | yes | processed | answer | 0 |
| over 32K, 32,259 chars | `raw_text` | yes | processed | answer | 0 |
| malformed, transport id | `raw_text` | yes | processed | clarify | 0 |
| canary | none | yes | processed | answer | 0 |

- The SSE cursor reached `canary-t`. In the earlier code, the first frame alone pinned the stream.
- Each exact original was found in exactly one side-fork transcript.
- No queue row was `raw_injection`, and each runtime status ended `replied`.
- Two earlier runs of the same scenario gave the same four `processed` receipts and the same cursor. Their screenshots were blank because the probe window was minimized; the capture helper now restores the owned window first.

Screenshots (title bar and debug-log path redacted):

- [before the stream](evidence/owned-console/01-before-sse.png)
- [three side forks active](evidence/owned-console/02-three-side-forks-active.png)
- [all four answered (`IN 0 AI 0 ANS 4`)](evidence/owned-console/03-all-four-answered.png)

Observed but not explained: in this run the status line read `SSE gate on: held (identity_pending)`; the earlier run showed `open`. That label comes from `runtime_compaction_binding.py`, which this change does not modify, and delivery completed in both runs. Its cause was not investigated.

# Beta and Prody pickup

Pending until the owned proof passes, the intended diff is committed, beta CI/publish succeeds, the registry artifact is verified and installed locally, and Prody updates through its normal AAP path.
