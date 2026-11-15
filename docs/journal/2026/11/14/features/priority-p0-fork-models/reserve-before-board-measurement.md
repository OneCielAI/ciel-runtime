---
type: measurement
title: Reserve before board feasibility
description: Real 57dbfb3 evidence and lock-order inventory for the proposed pre-board execution fence for new P0 roots.
timestamp: 2026-11-14T06:55:00Z
tags: [work, p0, compatibility, ledger, locking, measurement]
---

# Verdict

**CHANGE remains.** The hypothesis is only partially viable and does not yet provide a safe mixed-version saga.

# Real 57dbfb3 observations

Isolated child imported only `.local/mod-gates/beta-57dbfb3`; return code 0.

- Public old `_reserve_assignment` refuses an absent board item as `unknown Work item`.
- The bare legacy `WorkParallelStore.reserve` accepts a host-projected P1 candidate whose item/root are absent from the board and writes one unbound `reserved`, slot-true lease with deterministic `dispatch_name`.
- Old pause/unpause read/writes retain that lease.
- The legacy duplicate fence refuses another same-scope+same-item execution-family reservation before and after board publication.
- Old root removal leaves the exact lease present. Recreating the root allocates a different item identity, and an ordinary old reserve for that new item succeeds; two leases then exist for different item identities.
- A legacy-no-owner dead observation can delete the unbound provisional reservation through `orphan_release`.
- For a multi-leaf plan, the first runnable leaf excludes a waited first child and selects the later runnable child. If every leaf waits, no first runnable item exists. Bare store reserve has no board gate and therefore requires the new host operation to define/validate the selected item itself.
- `_approval_current` returned false after board publication and retained the lease.

The old MCP reserve/reserve_next calls in this Windows child hit an unrelated missing `pwd` import before an endpoint result. The duplicate fence is proven at the real store boundary, not at those two wrapper endpoints in this run.

Evidence:

- [Harness](evidence/reserve_before_board_probe.py), SHA-256 `290cc97667243b7754a183eef71a7375775fb1ac587419aeee255c7e13b27584`;
- [Result](evidence/reserve-before-board-result.json), SHA-256 `73b25bc045cd1ebf060fc73c657e77cec9f2937a6ed70a2e677a1e4f0c0aedc7`.

# Lock-order inventory

Every store owns an in-process `RLock` and one path-specific cross-process advisory file lock only during its own read/change:

| Store | Lock site | Observed composition |
|---|---|---|
| board `WorkPlanStore` | `work_plan_store.py:156` | board lock only; callback runs after release |
| ledger `WorkParallelStore` | `work_parallel.py:255` | ledger lock only |
| coordination `WorkCoordinationStore` | `work_parallel_receipts.py:251` | coordination lock only |
| current reserve path | `work_parallel_mcp.py:808-830` | ledger reserve completes/releases lock, then coordination reconcile/record |
| current bind path | `work_parallel_mcp.py:2881-2885` | coordination pre-read, ledger bind completes, then coordination bind |
| board result completion | `work_parallel_mcp.py:1409` | board transaction after other lifecycle decisions, not nested in ledger lock |

No current public service holds the legacy ledger file lock while entering a board transaction. `WorkParallelStore._change` encapsulates its lock and returns before callers can write board/coordination. Implementing “hold ledger lock through final board CAS” therefore needs a new cross-store transaction primitive and would introduce ledger→board nesting that existing board→scheduler/observer paths have not been proven to order safely. The current code provides no rollback across ledger+board+coordination+P0 files.

**Conclusion:** do not hold the legacy ledger lock through board CAS under the current primitives. A safe design would need a measured global transaction lock used by every conflicting new path, but old 57db writers do not take it, so it cannot supply mixed-version atomicity.

# Rejection conditions met

The hypothesis as stated fails the full requirement today:

1. absent-board reserve is possible only below the normal host validation path;
2. an old orphan action can remove the provisional fence;
3. old root remove/recreate changes item identity, allowing different-item execution despite the old lease;
4. no current safe lock order makes ledger+board publication atomic against old writers;
5. a user-waited/blocked all-leaf spec has no deterministic eligible first item, while preselecting a gated item would weaken normal candidate validation.

The evidence supports neither adopting reserve-before-board as a complete B3 fix nor claiming old-writer exact convergence. Keep new-root P0 creation CHANGE unless the contract explicitly excludes concurrent old writers through an operator-enforced scope (which current attachments cannot prove) or weakens the guarantee and accepts these races.
