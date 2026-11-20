---
type: fix
title: Host recovery must see completion runs older than the model display cap
description: KOBE's new report exposed completed results permanently missing from review because authenticated host status used the model's 24-run projection limit.
timestamp: 2026-11-19T23:10:00Z
tags: [work, parallel, recovery, projection, review]
---

# Observed

Read-only inspection of KOBE at 2026-11-19 22:56–23:02Z confirmed the installed router was still beta a9d4d0c. Its report at 22:32Z listed n835, n837, n838, n841, n849 and n850 as validating without a review pin. All six have submitted slot-free leases, a durable coordination completion exit and idle proof, but no finalization row. Each has workspace null.

Coordination contains 69 current-scope runs. Sorted by the same newest-first key as its projection, the six are ranked 46, 51, 52, 53, 54 and 55. n906 and n908 rank 29 and 30; main's explicit review calls had published rows for some missing results.

The current-scope n468 lease is done, its review finalized/approve. Another submitted n468 lease belongs to the previous session. That is a distinct record, not evidence that the current approval failed. No live state was edited.

# Cause

`work_parallel_mcp.project` requested `coordination.projection(limit=MODEL_LEASE_LIMIT)` even for authenticated host status. `MODEL_LEASE_LIMIT` is 24. The mod's `parallelRestartSubmitted` looks up the exact run in that status, requiring its generation, agent and completion receipt; if absent it skips the lease. A result older than the cap can never enter this recovery path.

# Change

The coordination projection accepts host_view. Authenticated host status returns every run in the scope, still bounded by the coordination store's MAX_RUNS and MAX_BYTES. Receipt details remain absent. Model-facing status retains the existing 24-row limit. No prompt, mod hook or decision authority changes.

The related null-workspace close crash and approval-change refusal are tracked in [review approval and P0 intents](../review-approval-and-p0-intents/record.md).

# Verification

- Added `test_host_status_keeps_completion_older_than_the_model_projection_limit` to restart-terminal tests. It creates newer runs, checks that the model view is capped and the host view retains the older exact completion, then publishes the review through the existing authenticated restart path.
- Before fix: assertion that the completed lease exists in host coordination runs failed. Evidence: `.local/tmp/kobe-host-projection-before.txt` (local only; includes temporary fixture paths).
- After-fix module results and Linux checks are recorded in [log.md](log.md).

# Live status

Not installed or restarted yet. Live recovery after deployment has not been verified.
