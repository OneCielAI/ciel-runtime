---
type: Diagnostic
title: Stop read lost to display refresh
description: Overlapping display refresh silently discarded a successful Stop snapshot and skipped runnable queued Work.
tags: [work, stop, concurrency, continuation]
timestamp: 2026-10-27
---
# Correction

Earlier local-session diagnosis concluded no transition defect from recent `none can run now` notices. Those notices establish only the board state at those particular stops, not absence of a lifecycle defect. That broader conclusion is withdrawn. This record proves an independent defect; exact historical overlap in the user's local or Prody incident is not retained.

# Reproduction

The packaged `classic.Stop` used `refresh()`, which increments display generation. Another inspector/status/idle refresh supersedes its pending read; `refresh()` returns null despite a valid actionable snapshot. When the newer reader already filled the cache, Stop returns unchanged without either continuation or unavailable notice.

[Baseline actual-hook harness](evidence/baseline.json): active n1 waits; queued n2/n3 runnable; Stop read delayed 60ms while a later command status refresh completes after 5ms. Stop returns block=false, logs empty. The harness uses the actual shipped register.js, not a reimplemented hook.

# Change

Stop uses an independent verified gateRead. A separate main-turn epoch and session lifecycle fence preserve cancellation when a new main turn or session starts/ends, without treating a concurrent display reader as cancellation. Native preventContinuation/block, disabled Work, approval, no-progress ceiling and waiting/blocked semantics are unchanged.

[Fixed harness](evidence/fixed.json): same overlap returns a continuation naming `work_select root_id n2`. New main turn and session end still return no continuation. [Focused tests](evidence/focused-tests.txt): 9 tests pass, including existing concurrent Write/approval safety regressions.

# Actual CLI

[Claude Code 2.1.311 run](evidence/actual-queue-108c9125/result.json): 10/10 checks, one user prompt creates a waiting active root and runnable queued root. Stop continuation reaches the model, work_select succeeds without user_requested, queued Write executes, its leaf completes, waiting root resumes unchanged. [Screenshot](evidence/actual-queue-108c9125/01-queue-yield-completed.png). This proves ordinary real queued handoff under the fixed hook; the deterministic concurrency condition is proved separately by the actual-hook harness, not claimed forced in this real-CLI run.

Isolated HOME/config/ports, inherited runtime/provider environment stripped; outbound sentinel blocked external attempts. Live 9505 PID62984 and 9524 PID8544 unchanged. No live hook/session reloaded. Source fix is not yet published/installed.
