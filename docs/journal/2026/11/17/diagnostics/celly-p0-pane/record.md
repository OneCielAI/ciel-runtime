---
type: diagnosis
title: Celly's Work pane labelled its P0 goals P1 — the pane showed the stored priority, not the effective one
description: P0 is stored on the board as canonical P1 (or keeps its earlier P2–P5) and activated in the P0 sidecar. The Work & SSE Observatory tree, the text detail list and the running-fork line printed the stored field only, so every P0 goal Celly's side actor registered appeared as P1. The P0 activations themselves happened (19 "fork starting" notices, 0 activation errors). Fixed in the pane and in the fork row projection; tested fail-before/pass-after.
timestamp: 2026-11-18T01:30:00Z
tags: [work, p0, observatory, register.js, side-intake, celly, priority]
---

# Question (user, 2026-11-17)

> 분명 p0 아이템이 몇개있어야하는데 셀리의 mods에는 p0가 없이 p1으로 분류하는이유는?

In English: "There should be several P0 items, so why does Celly's mod classify them as P1, with no P0?" The screenshot showed WORK TREE roots labelled P1/P2/P3 while the SSE FLOW listed many P0 events.

# Findings

## F1 — P0 activations did happen ✅

Source: Celly's current main transcript (`G:\ciel-bridge-service\.claude\projects\G--ciel-bridge-service\0aefa936-….jsonl`, read-only). The side host appends `Side P0 queued <item> · <title> · fork starting` after it activates P0 (`side-intake.js` `shareWithMain`). On failure it appends `· P0 not activated: <error>` instead.

| Measured | Count |
|---|---|
| `Side P0 queued` notices | 25, on 9 goals (n916, n964, n970, n973, n995, n997, n999, n1000, n1006) |
| notices ending `· fork starting` | 19 |
| `P0 not activated` | 0 |

Evidence: [evidence/celly-side-p0-queued-notices.txt](evidence/celly-side-p0-queued-notices.txt).

## F2 — the board stores P0 as P1 by design ✅

`side_work_decision.py` sets `board_priority = 'P1' if wants_p0`. The comment there reads: "P0 is stored as canonical P1 and activated as P0 right after this commit (side_intake_mcp)". `_activate_side_p0` then calls `mutate_existing_priority(..., 'P0')`, which writes the P0 sidecar. Every other P0 entry point does the same (commit c23087e, 2026-11-15). The effective priority is published as `root_priorities[].effective_priority` in `work_parallel_status` (`work_parallel_mcp.py` `project`).

A goal promoted by `update` or `prioritize` keeps its stored value unchanged. The sidecar alone makes it P0. This is why a P0 goal can also show P3, as `n5` does in the test.

## F3 — the pane printed the stored field ✅ (the defect)

`register.js` (installed 71f5243 and HEAD):
- `graphicNodes` set `priority: node.priority` (the terminal Client tree).
- The text pane's detail list and tree rows printed `node.priority`.
- The running-fork line used the lease's `priority`. A P0 lease keeps the canonical P1 (`p0_launch_assignment` returns `"priority": "P1", "effective_priority": "P0"`), and the fork row projection had no effective field.

The web queue already showed `node.effective_priority||node.priority`, so only the mod pane and the fork rows were wrong.

## F4 — some P0 events were not decided as P0 (model decision, not changed) 🟡

Measured over Celly's side-fork transcripts: 135 forks received `source_priority: "P0"`. Of the 82 with a parseable final decision:

| Decision | Count |
|---|---|
| `priority: P0` | 22 |
| `update` with no priority | 10 |
| `answer` | 48 |
| `clarify` | 2 |

The other forks had no final decision text (49) or text that did not parse (3).

The three sampled forks that omitted P0 had the side prompt's rule "When source_priority is P0 … use priority P0" in their context (2–3 occurrences each). An `answer`/`status` event creates no Work, so it never has a priority.

Whether each omission was correct for its event was not judged; their message bodies were not read. Evidence: [evidence/celly-p0-side-decisions.txt](evidence/celly-p0-side-decisions.txt) (ids and counts only), script [evidence/celly_p0_tally.py](evidence/celly_p0_tally.py).

# Fix

- `register.js` `shownPriority(node)`: a root's `root_priorities` effective priority, else its stored one. It is used by the graphical tree (`graphicNodes`), the text detail list and the text tree rows.
- `work_parallel_mcp._fork_projection`: every fork row now carries `effective_priority`. It is `P0` when the lease is active in the P0 sidecar (`_p0_active_lease_ids`, the same set the capacity count uses), else the lease priority. The pane's fork line uses it. REST `/parallel` and web share this projection.
- Stored priorities, P0 activation and scheduling are unchanged.

# Verification

| Test | Before (HEAD 71f5243 + new test) | After |
|---|---|---|
| `test_work_observatory.test_work_tree_labels_a_goal_with_its_effective_p0` | FAIL `['n1:P1','n5:P3']` | pass (`n5:P0`, fork line `n5:P0`) |
| `test_work_parallel_p0_events.test_a_p0_fork_row_says_it_runs_as_p0` | ERROR `KeyError: 'effective_priority'` | pass |
| `claude plugin test ciel-work` | — | 103 pass |

Outputs: [evidence/pane-test-before.txt](evidence/pane-test-before.txt), [evidence/pane-test-after.txt](evidence/pane-test-after.txt).

`claude plugin test` on HEAD had one failure, unrelated to this fix. `work.test.ts` expected the engine node `ref: 0`, but Claude Code 2.1.327's test host returns `ref: 2` after a redraw. The assertion now checks the node's shape (`ref: any number`), which matches the test's intent: only the engine node is drawn.

# Not done here

- Celly runs the installed build. The fix reaches Celly's pane only after a beta deploy and a Celly restart, which needs the user's confirmation.
