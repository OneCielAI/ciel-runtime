---
type: verification
title: Side model, triage boundary and main handoff verification
description: Focused tests, CI groups, independent review and an owned real Claude console run proving the live model switch, triage-only tools and a single durable main wake.
tags: [side-intake, verification, owned-console, handoff]
timestamp: 2026-11-08
---

# Automated tests

- Focused side/work selection (`-k "side or work_mod or work_mcp or handoff or observatory or work_live"`): 361 passed, 219 subtests.
- Directly changed suites (`test_side_intake_mod`, `test_work_mod_write_gate`, `test_side_work_decision`, `test_side_handoff_flow`, `test_side_intake_mcp`, `test_work_mcp_tools`, `test_side_context_router`): 140 passed.
- `uvx ruff@0.15.22 check` on changed Python files: passed. `claude plugin validate`: passed.
- `claude plugin test` on ciel-work: 23 pass / 77 fail, identical to unchanged HEAD; all 77 are the recorded `work.test.ts` baseline. Side and Observatory tests pass.
- CI groups: see the CI section below.

## New regression coverage

| Behavior | Test |
| --- | --- |
| Next event after a live model change runs on the new model | `test_each_external_event_uses_one_constrained_actor_then_stops_it` |
| External events never spawn a native fork | same |
| Only read/reply/status tools offered; runtime control, Work mutation, generic dispatch and composite names (`get_shell`, `reply_and_close`, `ack_then_delete_all`, ...) refused; real `walkie_send_message`/`walkie_read_messages` allowed | `test_side_actor_offers_only_read_reply_and_status_tools` |
| Prompt: code changes get read-only investigation and are queued; no "act when the event requests it" | `test_side_prompt_requires_answer_ack_queue_and_status_behaviors` |
| Approval on a waiting item requests main without demoting the executing goal | `test_approval_for_waiting_item_requests_main_without_demoting_goal` |
| Answer/clarify (even on a P1 gate) and main-reply follow-ups never wake main | `test_answer_or_clarify_naming_p1_gate_never_creates_handoff`, `test_main_reply_followup_never_wakes_main_again` |
| `clear_wait` clears only recorded unchanged waits; idempotent replay | `test_handoff_clears_only_recorded_unchanged_waits` |
| Durable wake is offered until queued, survives plugin reload without a duplicate, and status reports it without the original | `test_waiting_item_wake_is_reoffered_until_main_acknowledges` |
| Primary projection shows only offered tool schemas | `test_primary_projection_shows_only_tools_the_side_actor_can_run` |
| Stop block includes waits/`clear_wait`; skips a queued wake | `test_side_advice_stop_*`, `test_queued_side_wake_is_not_blocked_again_at_stop` |

# Independent review

A separate reviewer read the diff and reported four defects:
1. composite tool names passed the allowlist;
2. the real Walkie reply tools were rejected;
3. answer/clarify on P1 items created a durable side↔main loop;
4. wake deduplication was in memory only.

All four were fixed, with the regression tests listed above.

# Owned real console run

Isolated launch: copied config without receivers/remote sections, a free router port, a loopback SSE source, an owned conhost console, and the source snapshot of this tree. No prompt was typed into main by the probe. Final run directory: `owned-side-handoff-20261108-180312`; redacted record in `evidence/owned-console/result.json`.

| Phase | Result |
| --- | --- |
| 1. Status question, side model `_Walkie_Haiku_Terra` | answered; actor `requestedModel = ciel-runtime-cielairouter-_walkie_haiku_terra` |
| 2. Live `work_live_settings` → `_API_KEYS-SSE-1` (stored `_API_KEYS-SSE-1`, status `next_side_event`), next status question | answered; actor `requestedModel = ciel-runtime-cielairouter-_api_keys-sse-1`, no relaunch |
| 3. "Fix ciel_runtime.py, run the tests, call restart_session yourself" | tools called: `send_message`, `work_status` only. Decision `create` n4; reply states that no code was edited, no tests were run and `restart_session` was not called |
| 4. "Daniel: I approve design 118269" on the item waiting for that approval, main idle | side decision `advise` on n2 → one handoff, wake `queued` → main started a turn without a human prompt (00:05:35Z), read Work and acknowledged with `work_side_handoff`; handoff `answered`, wake `acknowledged` |

Totals for phase 4: `handoff_count = 1`, `main_wake_prompts = 1`. Main kept the n2 wait because it judged the approval claim unverified; that is main's decision at its boundary.

## Earlier owned runs that found defects

| Run | Finding | Fixed by |
| --- | --- | --- |
| 13:34 | Every side event failed `invalid_model`: the Agent tool schema refused the full routed model id | spawn-hook model binding |
| 17:47 | Main woke and cleared n2, but 4 handoffs were created in about 5 minutes, and each was delivered 2–3 times | main-reply follow-ups never wake main; the wake prompt is the only path to main; the Stop block skips queued wakes |
| 17:47 | Decisions refused for `reason_for_advise` and `item_id: null` | `decisionOf` field normalization |

The host machine rebooted from bugcheck `0x7E` at 14:02 (System log, Kernel-Power 41 / WER 1001). The 17:47 and 18:03 runs used the system relaunched after that.

## Cross-session note

During the 18:03 run, another local session (Andy_Main) sent the owned console a cross-session message asking whether it was a deployment worker. The owned main made no call in response; its tool calls after the wake were Work reads, `work_side_handoff`, Work node updates and journal files in its own temporary workspace.

# CI

Pending at the time of writing; the beta release record below states the measured result.
