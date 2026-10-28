---
type: Feature
title: Waiting and blocked Work items in the status line
description: Status line rows mark waiting items with a clock and a short reason and blocked items with a red BLOCKED badge; hidden waits are counted.
tags: [work, statusline, waiting, blocked]
timestamp: 2026-10-27
---
# Request

User screenshot #20 (.14 KOBE agent 24): waiting items are reported in the chat window; the bottom status line should also show a clock and a brief reason, and blocked items need stronger emphasis. Work n367.

# Baseline (installed ea59a61)

Real Claude Code 2.1.311, isolated HOME/config/ports, loopback gateway, egress sentinel: [baseline-ea59a61/01-statusline.png](evidence/baseline-ea59a61/01-statusline.png).

- A waiting item kept its `[ ]`/`[~]` mark and ended with ` ◷ <full waiting_on>` (up to 120 characters).
- A blocked item was a plain gray `[!]`.
- Waiting/blocked items below the second displayed level (e.g. `Signal forwarding`, `Scheduler drain`) left no trace.
- 6 of the 9 fixture checks failed, as expected.

# Change

| Where | Change |
|---|---|
| `statusline_script.work_plan_status_text` | Waiting mark `◷` (bold amber) + ` · waiting: <reason ≤40 chars>`; blocked mark `[!] BLOCKED` (bold white on red) + red reason; header ` · ◷ N waiting · [!] N BLOCKED`; child rows ` (◷a !b)` for hidden descendants; `+N more (…)`; queued and next rows use the same marks; finished items show no wait. |
| `work_plan_tree.snapshot` | `attention` (active goal, excluding the goal itself), `children[].hidden`, `breakdown[].more_attention`, `next.waiting_on`. A blocked node that also waits counts as blocked. |
| `docs/Configuration.md` | Status line notation. |

`CIEL_RUNTIME_STATUSLINE_ANSI=0` keeps the same words without color.

# Verification

- Unit: `tests/test_work_breakdown_statusline.py` (+4: exact plain lines, ANSI badge/clock codes, next-line wait/blocked, finished item), `tests/test_work_plan_tree.py` (+1 attention counts). All `test_work*.py`: 201 OK ([unit-tests.txt](evidence/unit-tests.txt)). Ruff clean.
- Real CLI, source worktree: [source/01-statusline.png](evidence/source/01-statusline.png), checks 9/9, plan unchanged (display only), live routers 9505 pid 79472 / 9524 pid 65156 identical before and after.

# Not changed

The AbovePrompt Work panel (Ciel Work mod) still shows `[ ] … ◷ reason` and plain `[!]`; the request named the status line.
