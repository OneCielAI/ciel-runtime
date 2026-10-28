---
type: Diagnostic
title: Kobe Work gate failures
description: Concurrent Work hook reads denied healthy Edit/Write calls, and waiting-goal selection refusals had no actionable reason.
tags: [work, kobe, hooks, concurrency]
timestamp: 2026-10-27
---
# Scope

Work n409 and n418: `.14` Kobe session `5ac1ee55-d0d1-4ef7-b4df-3d68e62bbd1b`, inspected read-only. Kobe's Work store was not readable from the admin connection (`Access denied`), so its exact board is known only from tool results in the transcript.

# Observed

| Observation | Evidence | Status |
|---|---|---|
| `7792965` was installed at 18:37Z; the router and Claude restarted about 18:40Z. | npm debug log; process creation times | confirmed |
| Main and subagent Edit/Write calls were denied with `Work status unavailable` while direct `work_status` calls succeeded. | [17:42–17:44Z timeline](evidence/timeline-1742-1744Z.txt), [18:46–18:50Z timeline](evidence/timeline-1846-1850Z.txt) | confirmed |
| Several denials overlap another agent's Edit/Write gate; the most recently started gate passes. | same timelines | confirmed |
| Some later denials have no overlapping tool call in the transcript. Another hook refresh may have overlapped, but no log proves which one. | same timelines | unknown |
| `work_select` without `user_requested` returned only "changing the active goal requires an explicit user request". | transcript tool results | confirmed |
| The prior guard already allowed only the queued goal holding the first runnable step. Whether n67 held that step at v679/v681 is unknown. | `work_plan_tree.select_root`; Kobe v681 render | partly unknown |

# Cause

`register.js` used `refresh()` for both display state and safety gates. Every `refresh()` increments a shared generation, and an older read returns `null` after a newer read starts. The Edit/Write and approval gates treated that `null` as an unavailable router and denied the operation.

The [baseline harness](evidence/harness-baseline-56358fb.txt) reproduces this with the released hook: two concurrent agent gates, a gate during a new main turn, and an approval during an Edit all report `Work status unavailable`.

# Change

- Edit/Write and `work_execute` gates now read their own verified snapshot. Another agent's read or a new turn no longer invalidates the decision.
- Failures still deny. The denial names the step (`router-call`, `router-timeout`, `router-error`, `snapshot-parse`, `snapshot-schema`, `snapshot-version`, `identity`, `identity-changed`) and a generated correlation ID. Router text, tool input and secrets are not logged.
- A rejected `work_select` now states the active goal, the first runnable goal or step, whether the requested goal can run, active phase, waiting/blocked counts and version. Its permission rule is unchanged.

# Verification

| Check | Result |
|---|---|
| [Fixed harness](evidence/harness-fixed.txt) | concurrent gates allow; planning, router failures, stale snapshots and identity changes deny with typed stages |
| Focused Work tests | 219 passed; 1 Windows store test also fails before this change ([focused-tests.txt](evidence/focused-tests.txt)) |
| Actual Claude Code 2.1.311, installed `7792965` (same script) | 38 child Edit denials with Kobe's exact `Work status unavailable` text; all three files stayed `BEFORE` ([result](evidence/run-5cc0be3f/result.json), [requests](evidence/run-5cc0be3f/requests-summary.json); the screenshot was taken while the children were still retrying) |
| Actual Claude Code 2.1.311, source worktree | waiting-goal handoff without `user_requested`, planning Write denied, three parallel subagents each made five successful Edits, no child tool errors ([screenshot](evidence/run-93e137ef/01-concurrent-edits.png), [result](evidence/run-93e137ef/result.json)) |
| Live routers 9505/9524 | same PID and fingerprint before and after the isolated run |
