---
type: Verification
title: Work parallel scheduler focused verification
description: Source-level verification evidence collected before full groups, owned runtime proof and beta deployment.
tags: [work, parallel, tests, evidence]
timestamp: 2026-11-10T00:00:00Z
---

# Focused tests

Isolated environment (`CIEL_RUNTIME_*` removed, temporary `CIEL_RUNTIME_CONFIG_DIR`):

- Parallel ledger, coordination sidecar, MCP lifecycle, native evidence, workspace salvage/rebind, plan tree, MCP tools, host pages, HTTP controller and Claude mod harness: **352 passed, 144 subtests passed**.
- Python changed modules compile successfully.
- `git diff --check` reports no whitespace errors.

# Covered behavior

- Every priority fills independently; candidate focus order is preserved.
- Multiple reviewers may run; accepting a review retires its reviewer lease.
- Questions/completion/handoff release slots atomically.
- Unrelated board changes preserve the assigned root generation; assigned scope changes invalidate it.
- Missing fork exit reports resume the fork instead of stopping it; salvage is persisted before terminal stop.
- Session-start recovery sends directly to the fork and does not enter main prompt context.
- Fill exceeds the previous 20-start ceiling and retains a trigger-during-fill latch.
- Terminal history does not consume the open-lease admission bound.
- Status is bounded and omits private terminal workspace/evidence history.
- Legacy unbound reservation repair and owner-identity-unknown behavior are covered.
- Workspace handoff keeps dirty partial work and prevents the prior generation from mutating or deleting the replacement workspace.
- Host page pruning removes expired orphan `.io.lock` sidecars.

# Isolated repository groups

All inherited `CIEL_RUNTIME_*` variables were removed and every group used a new temporary `CIEL_RUNTIME_CONFIG_DIR`:

- unit: **3,641 passed, 52 skipped**
- router: **1,552 passed**
- channel: **607 passed, 80 skipped**
- runtime: **458 passed, 19 skipped**

All four groups exited 0. Logs: `.local/groups-parallel-scheduler-repair/{unit,router,channel,runtime}.txt` (local evidence; release journal will retain the final summarized output).

# Compatibility check

Both live old validators (`4eebfdc`, `5c91ea8`) accepted new serialized lease output.

# Owned source scheduler proof

`evidence/scheduler-run-source-6/result.json` passed every acceptance gate:

- host started four native forks while main was idle;
- four distinct agent IDs occupied P1=2, P2=1, P3=1;
- marker windows overlapped for 46.4 seconds;
- after the short P1 worker released its slot, a new P1 worker started automatically under a new lease/agent;
- 24 seeded terminal-history rows did not block admission;
- fork-only canaries remained out of model-facing main conversation rows;
- desktop and 390px Parallel screenshots both showed the four confirmed cards.

# Owned restart proof

`evidence/restart-run-source-7/result.json` recorded the decisive restart evidence before its screenshot-only probe tail failed:

- the probe terminated only its owned parent/router tree;
- `--continue` resumed the same Work session;
- the same lease, generation and native agent ID were retained;
- the exact fork transcript contained the pre-restart canary and the fork wrote its post-restart marker;
- model-facing main rows contained neither canary nor the host recovery instruction.

The run's acceptance booleans for restart correctness were all true. Desktop/mobile capture was already proven in `scheduler-run-source-6`; the source-7 runner itself exited nonzero only because its detached resumed console could not be attached for duplicate screenshots.

# Remaining definition-of-done work

Beta registry integrity verification, local immutable install and installed-build repetition remain required.

# Related concepts

- [Scheduler lifecycle](/architecture/scheduler-lifecycle.md)
- [Mixed-build compatibility](/compatibility/mixed-build-ledger.md)
