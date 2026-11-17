---
type: fix-record
title: Side SSE replies — receipt ACK without echo, the actor's own understanding, then the result
description: The host's receipt ACK no longer repeats the message, the side actor's own-word understanding goes to the source before the board changes, and the final reply carries no second "ACK —". A failure gets a truthful reply, routine P2–P5 and status answers no longer hold main, and another Ciel session's receipt ACK starts no actor and gets no reply.
timestamp: 2026-11-17T22:10:00Z
tags: [side-intake, ack, understanding, readback, walkie, sse, reply-order, celly, andy, kobe]
---

# Request (user, 2026-11-17)

Diagnosis: [../../diagnostics/side-ack-readback-order/record.md](../../diagnostics/side-ack-readback-order/record.md).

> 또 최근들어. 사이드액터들이 기본 시스템 프롬프트가 제대로 동작하지 않는점도 수정 앤디 샐리 동일.. 메시지 수신 ACK, readback을 sse 받은데로 에코하면 안됌. 받은 메시지를 자신의 이해한스타일로 복기하는절차임. 그리고 분석하고 큐를 조절한후에 필요하면 메인스레드에 알리고.. 처리과정을 다시 sse 메시지온거에 적절히 다시 대답해야하는제.. 지금은 단순 에코만하고있음

In English: "Side actors' default prompt also does not work properly lately; same on Andy and Celly. The message-received ACK and readback must not echo the SSE as received. A readback restates the message in the actor's own understanding. Then it analyses, adjusts the queue, tells the main thread only if needed, and answers the SSE message with how it was handled. Right now it only echoes."

Stage 2 of the approved plan (Stage 1: [../work-workspace-trust/record.md](../work-workspace-trust/record.md)) fixed the order:
1. immediate receipt ACK;
2. the actor's own-word understanding;
3. analysis and queue change;
4. main only when needed;
5. the result back to the sender.

Each send has its own Walkie `client_id`, and an ACK coming back over SSE starts no new actor.

# Measured before the change

Inbound events, read-only probe of this machine's Walkie session ([inbound-walkie.txt](evidence/inbound-walkie.txt), script [inbound_walkie_probe.py](evidence/inbound_walkie_probe.py)):
- Unique Walkie events, 2026-11-17 22:01Z: **1,599**.
  - **0** carry a `client_id` key; 35 mention the word only in their text.
  - `own` is False on all 1,599.
  - So the plan's "recognize our own message by `client_id`" cannot work on what Walkie returns. Recognition uses only the two exact host ACK templates.
- **177** inbound events are another Ciel session's legacy receipt ACK (`ACK — 요청을 받았습니다.\n\n복기: …`):
  - from KOBE 154, from Celly 23;
  - received 07:49Z–22:00Z;
  - this session sent a receipt ACK back to **118** of them;
  - 81 are still pending as requests for a side actor.
- They are still arriving because the live sessions run the old build.

Messages sent to the source, measured with the real Claude Code E2E before the change (below):

| # | Text |
|---|---|
| 1 | `ACK — 요청을 받았습니다.\n\n복기: <the person's message, verbatim>` |
| 2 | `ACK — <model reply>\n\nn1 등록 · P3 · …` |
| Failure | Only message 1. No reply ever said the message was not handled. |

# Change

1. **Receipt ACK** (`side_ack_outbox.py`, `side_reply_connector.RECEIPT_ACK_TEXT`).
   - Fixed text with nothing from the message: `메시지를 받았습니다. 내용을 확인한 뒤 이해한 내용과 처리 결과를 이어서 알려 드리겠습니다.`
   - Owed from the moment the event is in the inbox (marker under `ack-pending/`).
   - `claim` and `status` list the events owed one (`pending_acks`). `side-intake.js` sends them before the slot check, and also while every slot is busy.
   - A definite refusal is owed again, at most 3 times. An uncertain send is never resent.
2. **The actor's own understanding** (`side-intake.js` PROMPT, `side_intake_settings.DEFAULT_SIDE_INSTRUCTION`, kept identical).
   - The actor answers `{"understanding": …, "decision": {…}, "expected_version": N}`.
   - New router tool `work_side_understanding` (`side_understanding_outbox.py`):
     - validates the understanding: not empty, not starting with `ACK`, not the original event or its message text;
     - saves the whole proposal on the receipt before anything is sent;
     - sends the understanding with its own phase;
     - refuses a different second proposal.
   - `work_side_resolve` then commits exactly the saved proposal, and only after the understanding step ran.
3. **Restart without a second model run** (`side_intake_processing.claim`, `side-intake.js resumeSide`).
   - A claim whose receipt already has a saved proposal offers it back. The host commits it without a fork.
   - The understanding outbox gives no send when it is already submitted or uncertain.
   - A board-changed recheck drops the proposal so the next actor decides again. The understanding already sent is not sent again.
4. **Final reply** (`side_work_decision.py`, `side_reply_outbox.py`).
   - The reply plus the committed outcome (`n1 등록 · P3 · …`), with no `ACK —`.
   - A quarantined event, whose original goes to main, gets one truthful reply: `이 메시지는 사이드 처리에서 자동으로 처리하지 못했습니다. 원문을 메인 세션에 그대로 전달했고, 메인 세션이 직접 확인한 뒤 답변드리겠습니다.`
   - A reply the host cannot send (`route_unavailable`, failed) is recorded for main.
5. **Main only when needed** (`side_work_decision.needs_main_review`).
   - A main review row is written for:
     - P0/P1;
     - a time limit;
     - released waits;
     - a relay;
     - clarify/advise;
     - a requested handoff.
   - A routine status/info answer or a P2–P5 queue entry stays in the side history only.
6. **Distinct `client_id` per send** (`side_reply_connector.phase_client_id`).
   - `uuid(sha256(receipt_id + ':' + phase))` for `ack`, `understanding` and `final`.
   - Walkie deduplicates a repeat of one phase without swallowing another.
7. **Another session's receipt ACK** (`SideIntakeInbox.is_inbound_receipt_ack`, `settle_receipt_ack`).
   - At admission it is stored as processed, with result notice `receipt_ack_inbound`. It gets no ACK, no side actor and no reply.
   - An older build left such rows pending as requests. `claim` settles one before any fork, and receiving it again (as a drain does) settles it rather than queueing it as Work.
   - The session socket sends no claim wake for a receipt already processed.
   - A person writing "ACK …" is not affected; only the exact templates match.

Not in scope (plan, step 7): telling the source when the queued Work item itself is finished.

# Verification

- **Behavior probes, before (35ba17e via `git archive`; the side-intake files equal 0dc0014) and after.**
  - [ack-probe.txt](evidence/ack-probe.txt) ([ack_probe.py](evidence/ack_probe.py)):

    | | Before | After |
    |---|---|---|
    | ACK echoes the message | true | false |
    | Final text starts with `ACK` | true | false |
    | P3 queue entry holds a main review | true | false |
  - [ack-loop-probe.txt](evidence/ack-loop-probe.txt) ([ack_loop_probe.py](evidence/ack_loop_probe.py)), for both templates:

    | | Before | After |
    |---|---|---|
    | Inbound receipt ACK gets an ACK back | true | false |
    | Inbound receipt ACK is claimed by a side actor | true | false |
    | A drain queues an older build's pending legacy ACK as Work | true (2 nodes) | false (1 node, the request) |
- **Restart resume, fail-before / pass-after** ([resume-tests.txt](evidence/resume-tests.txt)).
  - I found this gap in my own Stage 2 draft before committing: the claim did not offer the saved proposal, so a restart would fork the model again and its new proposal would be refused.
  - On that draft the 4 new tests fail (1 error, 3 failures). On the change, 75 of 75 pass.
- **Unit and harness tests.**
  - New: `tests/test_side_understanding_flow.py` (8) and `tests/side_resume_harness.mjs` (2 tests in `test_side_intake_mod.py`).
  - `tests/test_side_ack_outbox.py` gained receipt-ACK settlement and claim tests.
  - Every side harness decision now carries `understanding`.
  - The side, Work MCP, session-socket, gate-check, fork-gate, mod real-backend and architecture test files, on Windows and on Linux (963 passed, 0 failed): [log.md](log.md).
  - `claude plugin test ciel_runtime_support/claude_mods/ciel-work`: 103 pass, 0 fail.
- **Real Claude Code 2.1.326 E2E** ([run_side_order_e2e.py](e2e-source/run_side_order_e2e.py); [e2e-side-order.png](evidence/e2e-side-order.png) renders both runs' sends and checks).
  - Setup: owned temporary config, the packaged ciel-work and ciel-work-gate mods, the real Work tools, a stand-in `walkie` MCP server and a scripted Messages endpoint. Live sessions were not touched.
  - [Before](evidence/e2e-before/result.json): 0 of 8 checks pass. The sends were the two `ACK —` messages above, and the failed event got only the echo ACK.
  - [After](evidence/e2e-after/result.json): every check passes, including the added check that an inbound receipt ACK gets no send and no actor.
    - The request event got three sends: ACK, understanding, final.
    - Their `client_id`s are distinct.
    - The board had 0 nodes at the understanding send and 1 at the final send.
    - No main review was written for the P3 entry.
    - The failed event got the ACK and then the truthful failure reply.
  - Both runs:
    - ciel-work loaded;
    - 0 owned survivors;
    - 0 overlap with protected processes;
    - the host token appears 0 times in the evidence.
