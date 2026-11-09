---
type: Incident Analysis
title: Prody P1 Work item not running in parallel
description: No Work fork can start on Prody; three dead leases also hold device slots, and no dispatch has been attempted since 05:36Z.
timestamp: 2026-11-09T18:40:00Z
tags: [work-parallel, prody, fork, ledger]
---

# Scope

Read-only inspection of AAP host Prody (`prody-ai`, workspace `c53b7c787223`) at 18:27–18:31Z, 2026-11-09. Nothing on the host was changed.

# State observed

| Item | Evidence |
|---|---|
| Installed version | `@one-ciel-ai/ciel-runtime` `0.2.51-beta.20261109-171240.503be09`. Relaunched 18:03:45Z (`cli --resume ef61c539-…`), and the router scope now records session `d19de9c3-…`. |
| Board | Version 1976, `active_root n293` (P3, executing). P1 root `n43` "[105816] FDA INSPECTION — JUN_TEST 병합·TEST 배포·44 QA·LIVE 직전 JUN 검수" is `in_progress`, phase executing, and is not the active root. |
| P1 leaves | `n194`–`n197` (QA44 actual TEST runs) all carry `waiting_on` (JUN re-review 117874, D8 decision). `n43`, `n187`, `n188` and `n193` have no wait. |
| Device ledger | `%APPDATA%\ciel-runtime\work-parallel.json` v19, policy `max_active 5`, `p1_reserved 5`. Three leases hold slots: P1 `n527`, `n528` and `n534`, scope `claude:4d24be21-…`, created 2026-11-04 04:27Z, state `running`. That session is not running and its board file no longer exists. |
| Dispatch attempts since 2026-11-08 12:00Z | 04:31:05Z `work_parallel_run n231 P1` → `spawn_denied … Parallel read failed: no eligible candidate`. 05:36:21Z `work_parallel_status` → `active_device 3`, `leases {}`. 05:36:51Z `work_parallel_run n245 P2` → error `no eligible candidate`. After that, neither transcript has any `work_parallel_*` call. |

# Causes

1. **No fork can start (code defect, still shipped).**
   - The host reserves the item and calls `$.agent.spawn` (`register.js:1096-1113`).
   - That spawn runs back through ciel-work's own `agent.spawn` hook (`parallelAgentSpawn`), which reserves the same item a second time (`register.js:1157-1165`).
   - The router skips an item that already holds a slot (`work_parallel.py:226`) and reports `no eligible candidate`.
   - The spawn is denied and the first reservation is released. This matches the 04:31Z result.
   - The fix is local commit `ac88bfd` on `feature/work-parallel-20261103`. `register.js` in 503be09 is byte-identical to 13c6f4a (SHA-256 `E860C192…`), so the fix has not shipped.
2. **Dead leases hold device slots.**
   - Leases are never expired without idle proof, so the three 2026-11-04 leases keep 3 of the 5 slots.
   - With `p1_reserved 5`, non-P1 work may start only while fewer than `max(1, 5-5) = 1` slots are active. That is why the 05:36Z P2 run was refused.
   - P1 still had 2 free slots, but cause 1 blocks it.
3. **No automatic dispatch.**
   - In 503be09 a fork starts only when the main model calls `work_parallel_run`.
   - The prompt hook only adds guidance text (`register.js:1327-1334`).
   - No call has been made since 05:36:51Z.

# What the code fix alone leaves

`ac88bfd` fixes cause 1, and its per-priority limits (P1 5, P2 3, P3–P5 1) stop the dead P1 leases from blocking other priorities. It does not release the dead leases themselves:
- `migrate_policy` keeps every lease.
- `project()` still returns only the current scope's leases (`work_parallel_mcp.py:78`), and `parallelTurnComplete` reconciles only bound leases of the current scope (`register.js:1259-1260`).
- The three leases are host-bound and `running`, so `release_unbound` does not apply to them.

So after the code fix, P1 would still be limited to 2 concurrent forks (3/5 held) until those three leases are released.

Even with causes 1 and 2 removed, `n194`–`n197` would be refused by `dispatch_refusal` while their `waiting_on` is set.
