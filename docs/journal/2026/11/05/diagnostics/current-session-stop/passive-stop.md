---
type: Fix
title: Passive Stop when Work cannot execute
description: Waiting-only boards do not manufacture model turns and Work off skips reconciliation.
tags: [work, stop, idle]
timestamp: 2026-11-05
---

# Change

User requested improvement after repeated blocker feedback. Previous dependency-signature dedup still forced one model turn per new dependency/lifecycle, despite actionable=0. Remove the blocked/waiting forced recheck entirely. Keep one local person-facing reason and preserve waits and approvals. Explicitly enabled rate-limited discovery remains separate and runnable Work still auto-continues under its existing bound. No goal_clear invocation added.

parallelTurnComplete previously queried work_parallel_status unconditionally; live Work OFF returned error visible as Work reconciliation. Check verified live board enablement before reconciliation; disabled/unavailable skips parallel calls without changing slots/leases.

# Evidence

34 scripted mod tests pass including OFF turn complete with zero parallel calls, blocked/waiting Stop no block, and existing runnable continuation. Native suite initially87pass8fail: seven known baseline failures plus idle-refresh count expectation changed by new enablement read; adjusted that exact expectation. Owned CLI passive-stop/version-churn probe be07uzk28 exited0: no additional model turn after waiting-only end-turn. Release and installed validation pending.

# Boundary

Betty .12 screenshot is not proof of goal_clear caller identity; no remote restart/state changes made. This correction prevents Work Stop from asking model to re-verify non-executable prerequisites, not suppression of all external SSE reminders.
