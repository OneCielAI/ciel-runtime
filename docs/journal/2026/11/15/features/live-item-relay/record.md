---
type: feature-record
title: Side actor relays a follow-up about an item in progress to main and that item's fork at once
description: A side decision "update" with relay true forwards the original message to main and to the Work fork running that item; each decides whether it matters.
timestamp: 2026-11-15T22:00:00Z
tags: [work, side-intake, relay, fork, notice]
---

# Requirement (user, 2026-11-15)

- **Relay:** "싸이드액터가 메시지를 받았는데 메시지의 내용이 지금 작업중인 아이템에 전달해야하는거면 메인과 포크세션에 동시에 메시를 전달하는기능도 필요" (when a message the side actor receives should reach the item being worked on, deliver it to main and to the fork session at the same time).
- **Each recipient judges relevance:** "메인과 포크드세션은 필요없는내용인지 필요한내용인지 판단을해야함" (main and the forked session must each decide whether the content is needed).

# Design

- **Decision field `relay` (bool):**
  - It is valid only with `action: update` on an existing item. On any other action it is dropped, and `false` is dropped too.
  - A non-boolean value is rejected (`side_work_decision.validate`).
  - The resolve schema accepts it (`side_intake_mcp`).
  - The committed result carries `relay: {item_id}`.
- **Side prompt:** `side-intake.js` `PROMPT` explains when to relay:
  - relay a correction, new constraint or detail, answer, or changed requirement for that same outcome;
  - never relay a status question, a duplicate, or news about another item.

  `DEFAULT_SIDE_INSTRUCTION` is regenerated from this prompt.
- **Host delivery (`relayToItem` in `side-intake.js`):**
  - **Header:** `Ciel side relay for Work item <id> (<title>), event <ref>: <summary>`, then the original message (up to 3000 characters).
  - **Ask:** "Decide whether this matters to your current work; if not, ignore it. No acknowledgment is needed. This is not new execution approval and does not change your approved scope."
  - **Running fork:** a fork whose lease is on the item or any of its descendants, in state `running`, gets the text through `$.session.send` (its inbox, read at its next request).
  - **Parked fork:** a fork in `waiting_input` is never messaged from here, matching the released-wait rule. Main's notice names it (`Parked worker on it: <agent> (lease, item); resume with SendMessage to that worker only if this matters to it`).
  - **Main:** gets one at-most-once host notice through the existing `notice_prepare` claim (`side_notice._released_row` now accepts `result.relay`). When main is idle, a wake prompt starts its turn. The prompt names the relay notice and asks main to decide whether it matters.
  - **Released waits:** a relay that also released waits names them in the same notice, and no second release notice is sent.

# Verification

## Unit and harness

- **`tests/test_work_gate_check.py`:**
  - `RelayDecisionTests`: update with relay names the item; relay only on update, boolean only; a plain update is not a notice.
  - `test_relay_reaches_main_once_with_the_item`: MCP claim → resolve relay → `notice_prepare` is claimed once, and the second prepare returns `already_*`.
- **`tests/test_side_relay_notice.py`** (node harness `tests/side_relay_harness.mjs` with the real `side-intake.js`), 6 tests:
  - a running fork on a descendant of the item gets the original message, and a fork on another goal does not;
  - main gets the same message, "Also delivered to the Work fork running it", and the ask;
  - a parked worker is named for main and never messaged;
  - no fork on the item, or an undeliverable fork, means main only;
  - an idle main gets a wake prompt that holds none of the message and names the relay notice;
  - an update without relay tells nobody.

## Owned real claude.exe E2E

This feature shares the E2E with the gate re-check; see [gate re-check](/docs/journal/2026/11/15/features/gate-recheck/record.md), part 2:
- a real native side fork answered `update` + `relay: true`;
- the router committed `relay: {item_id: n2}`;
- main's request carried the relay header, the original message and the ask;
- the web reply was sent once.

Delivery to a running Work fork is covered by the node harness above, not by the claude.exe run.

# Defect found and fixed during E2E

- **Run 1** (`gate-recheck/evidence/run-20261115T211555Z`): the idle wake prompt for the relay said `a host message beginning "Ciel side released recorded wait"`. That prompt text was written when released waits were the only notice kind.
- **Fix:** `trackAppend` keeps the notice lead (`noticeLead`), and `wakePromptText` names "Ciel side relay for Work item" with the relevance ask.
- **Re-verified:**
  - the harness test `test_idle_main_is_woken_by_a_prompt_that_holds_none_of_the_message`;
  - the E2E checks `relay_wake_names_relay` and `release_wake_names_release` in run 2.
