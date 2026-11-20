---
type: fix
title: Gate notices count reviewable results rather than submitted leases
description: Andy reported 42 main-review items while 20 had review pins; the gate check counted distinct submitted items without checking finalization.
timestamp: 2026-11-19T23:40:00Z
tags: [work, gate, review, readiness]
---

# Evidence

A peer reported a gate event saying 42 results awaited main while work_parallel_status showed 43 submitted leases and 20 undecided reviews with pins (review version 970). Those counts measure different things. Direct source inspection confirmed work_gate_check.lease_facts deduplicated submitted leases by item; work_mcp_tools used that list as review_pending without checking published reviews. No conclusion about the separate 503 retries is made.

KOBE's transcript independently reports repeated requests to review n835/n837/n838/n841/n849/n850 while no review pin existed. Their host recovery projection defect is recorded in the sibling host-recovery-projection journal.

# Change

- Keep ledger lease facts for gate condition inspection.
- Build main-review notices only from published, pending, undecided finalization rows, deduplicated by item and scoped to the session.
- Update the managed gate_actor instruction so submitted alone does not imply review readiness. This is a managed prompt change as well as a Python dispatch change; no default person names were added.
- Preserve the approval_changed boolean in the mod's bounded review view. Actual result decisions remain main-only.
- Review reminder timing (register.js). The peer also reported two main messages for one wake. A real-backend probe of the idle wake path recorded two prompts after one new result: the review wake, then at once the mod_review_reminder prompt ("포크 결과 1건이 메인의 검수를 기다립니다"). remindPendingReviews only skipped rows whose wake prompt was still unresolved; once the wake prompt resolved, the next idle tick or Stop sent the reminder. The mod now records when this process first saw each pending review row (at projection, or in the reminder itself for a row only the pane lists) and reminds only for rows still pending 20 minutes after that. The existing once-per-set-per-20-minutes limit is unchanged. No reminder text changed.
- An intermediate draft recorded first-seen only at projection. For a row only the pane lists (the Celly case the reminder already covers) that draft never reminded at all; the pane-only scenario below caught it and the final code records first-seen over both views.

# Reminder evidence

Harness scenario review-reminder-waits-after-the-first-wake (tests/work_mod_harness.mjs): one report, the wake prompt is submitted and released, its turn runs, then Stop; then the clock moves 21 minutes and Stop runs twice. Counts are reminder prompts naming the item ([evidence/review-reminder-before-after.txt](/docs/journal/2026/11/19/fixes/gate-review-readiness/evidence/review-reminder-before-after.txt)):

| register.js | wakes | right after the wake | after 21 min | Stop again |
| --- | --- | --- | --- | --- |
| HEAD d216883 | 1 | 1 | 2 | 2 |
| changed | 1 | 0 | 1 | 1 |

Scenario review-reminder-pane-only-row: the review is in the pane's read only; every Stop projection reads no review.

| register.js | wakes | at the first Stop | after 21 min |
| --- | --- | --- | --- |
| HEAD d216883 | 0 | 1 | 2 |
| intermediate draft | 0 | 0 | 0 |
| changed | 0 | 0 | 1 |

Each side of the first scenario ran five times, and the changed side of the second three times, with the same result. An earlier version of the first scenario released the prompts before the wake prompt was submitted, so its result depended on timing; it now waits for the wake prompt and for its prompt key to settle, and asserts settled=true.

After the final change: tests/test_work_mod_write_gate.py 148 tests, zero failures/errors (33 existing retired-behaviour skips) on Windows and on Linux (node:22-bookworm, Python 3.11.2, Node 22.23.5, no network). claude plugin test for ciel-work: 89 pass, 0 fail. The Linux Python 3.10 image has no Node, so the Node harness was not run under 3.10; the change is JavaScript only.

# Verification

Windows initial three-module run: 55 tests, zero failures/errors/skips. Added a real MCP notice-content regression; the gate modules reran with 27 tests, zero failures/errors/skips. Linux Python 3.11 and 3.10 three-module runs: 56 tests each, zero failures/errors/skips. REST review response suite: 23 tests, zero failures/errors/skips. Mod writing-gate and workflow review tests: 152 tests, zero failures/errors, 33 existing retired-behaviour skips. Real mod engine suite: 89 pass, zero fail.

# Not verified

This patch is not installed yet. Legacy ledger-only results without coordination receipts remain a separate unresolved recovery problem; they are not silently called reviewable. 503 transport retries are not fixed by this change.
