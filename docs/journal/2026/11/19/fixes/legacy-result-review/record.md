---
type: investigation
title: Submitted legacy results without coordination history
description: Reproduced the ledger-only completion path and added a read-only provenance helper; production review integration remains unresolved.
timestamp: 2026-11-19T23:00:00Z
tags: [work, parallel, legacy, review, provenance]
---

# Verified cause

The delegated incident observation was 21 submitted legacy leases with no coordination run or receipt. This task did not inspect or mutate any live state.

The repository reproduces that lifecycle without deleting a run:

1. A legacy bound worker with no coordination run is accepted by the existing bind/report contract.
2. `work_parallel_mcp._worker_event` persists completion detail only when `assignment is not None`, but calls `store.report_completion` regardless.
3. `WorkParallelStore.report_completion` records `state=submitted`, the artifact and evidence, releases the slot, revokes the worker capability, and copies result evidence into `idle_proof`.
4. `_review_facts` refuses the result because there is no durable coordination completion receipt.
5. `_restart_submitted_terminal` also requires the exact durable completion receipt, so the existing restart repair cannot recover a ledger-only result.

The actual report/review path is executed by `test_real_worker_report_reproduces_submitted_without_a_receipt_then_review_refusal`; all files are temporary.

Historical source was also checked at commit `57dbfb3`: `_worker_event` has the same assignment-conditional receipt write and unconditional ledger completion; `report_completion` also sets `idle_proof=evidence`.

# Important correction

A slot-free submitted lease with a nonempty `idle_proof` does not, by itself, prove an independently observed idle native fork. Historical completion reports wrote the worker's result evidence into that field. The initial hypothesis that this field alone was positive native idle was contradicted by source and withdrawn.

The read-only helper therefore requires an exact fresh native `idle` observation or exact durable native terminal status before reporting eligibility for pending review. Missing, stale, or mismatched native history stays blocked. Missing-list absence is never changed into death by this helper.

# Implemented boundary

`ciel_runtime_support/work_parallel_legacy_source.py` preserves the recorded artifact, evidence, host, plan version, rework counter, idle proof and workspace binding in a domain-separated canonical source digest. It does not persist data or change lifecycle state.

It explicitly leaves historical facts unknown:

- completion receipt: null;
- assignment generation and approval: null;
- structured coverage and worker verification: null;
- projected coverage: empty;
- projected decision: null.

It refuses reclassification when a current coordination run, a surviving receipt, or archived run already names the lease. Such a partial modern history is not treated as an absent legacy history. Source revalidation detects changes to artifact, evidence, host, plan, idle proof, binding, current board root, or provenance.

Absence of all coordination history alone cannot distinguish a pre-coordination lease from a later lease whose entire coordination history was lost. `legacy_ledger` describes the surviving source format, not a proven historical build or date. Automatic migration would additionally need the incident's verified historical provenance or an explicit host capture contract; this helper does not assert an unrecorded creation era.

# Production integration not completed

This helper is not called by the production review path yet. The user-visible missing-review defect is not claimed fixed.

The existing schema-5 finalization contract requires a completion receipt digest, assignment fingerprint, and integer generation. None of those historical values exists in a ledger-only legacy result. A fabricated completion receipt, generated worker verification, or current approval presented as historical assignment approval was not created.

A safe integration needs a provenance-explicit review source contract. Required integration points, owned by main in this task:

1. **Finalization validation/projection:** discriminate a legacy source from a structured completion. Preserve missing receipt/assignment values explicitly; do not manufacture `verified=true` coverage. Bind a source digest into the semantic review pin.
2. **Pending publication:** resolve the current board root without claiming historical ancestry; inspect exact native evidence and workspace ownership. Expose insufficient-evidence blockers instead of silently omitting the result, approving it, or forcing a rerun.
3. **Main review revalidation:** re-read the legacy source and require its digest/identity unchanged. Existing main-only origin and semantic CAS gates must remain.
4. **Approve roll-forward:** reuse `main_accept`, board completion and `finalize_accepted` only after a durable main decision. Retain the workspace. Skip coordination archive only for an explicitly validated legacy source with no coordination history; the review journal is the audit.
5. **Explicit rework:** only if main chooses it. Use current approved goal data for the newly created assignment, not a fictional old run. Preserve existing launch/admission/idempotence gates and do not demand reexecution merely because historical structure is absent.
6. **Restart:** current host absence/ownership fencing is a separate observation contract. A raw complete-list absence cannot mint a missing completion receipt or approve the result.

Main performed a read-only live snapshot check afterward: 21 ledger-only submitted leases exist device-wide, but only 20 belong to Andy's current session; one belongs to its previous session. All 20 current-session results have recorded workspaces. Eighteen have no native record; two n1438 leases have old idle records with missing_since set. Consequently the helper's strict native eligibility alone cannot recover any of them now. The earlier total 'Andy 21' must not be interpreted as 21 current-session results. A current authenticated host absence/ownership observation and provenance-explicit journal integration are still required; no live state was changed.

# Verification

- Source helper suite, including actual report-path reproduction: **14 tests**, **0 failures**, **0 errors**, **0 skipped**, **0.134 seconds**.
- Earlier source helper plus coordination receipt and finalization regression run: **124 tests**, **0 failures**, **0 errors**, **0 skipped**, **237.570 seconds**. This run preceded the final report-reproduction test (the helper then had 13 tests).
- `py_compile` passed for the two new Python files.
- Targeted `git diff --check` passed.
- Tests removed every inherited `CIEL_RUNTIME_*` environment variable, set `CIEL_RUNTIME_TEST_ISOLATED=1`, and used a new `CIEL_RUNTIME_CONFIG_DIR` beneath TEMP. They ran through `.local/tmp/run_tests.py` by file path.
- No live config, credentials, sessions or state files were changed. No commit, push or deployment was performed.

See [log.md](log.md) for the execution record.
