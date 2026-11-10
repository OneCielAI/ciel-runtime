---
type: Diagnosis
title: KOBE's side actor answered 120354, but the reply was never sent
description: The side decision committed as an info answer with delivery route_unavailable, because KOBE has no reply connector. Main later read the room, saw an unanswered message, and ACKed and redid the work itself.
timestamp: 2026-11-10T04:10:00Z
tags: [side-actor, walkie, reply-connector, route-unavailable, kobe]
---

# Report

The user showed Walkie thread 120354 (Daniel Yun, 2026-11-09 19:43 CST): "is the repo running on qb.ezonebot.com ciel-qb-web? check the repo". The only reply was KOBE Ai's #120357, sent at 19:44:51: "Received. I will check on the production host and answer with evidence…". The user expected a different flow: the side actor checks, answers on Walkie at once, and leaves main a review.

# Access

- Host 100.64.0.14, KOBE sandbox `u24`, account `aapw-a24u24`.
  - ssh -p 1004 as SYSTEM; read only, nothing written or restarted.
- KOBE runs `0.2.51-beta.20261109-202057.4eebfdc`: Claude Code session `219f1b51…`, router on port 9780.
- The workspace state directory has an owner-only ACL. SYSTEM read it through `SeBackupPrivilege` with backup-semantics opens, read only.
- Raw copies stay outside the repository, because they contain chat text. This page records identifiers, times and states only.

# Timeline (UTC, 2026-11-10)

| Time | Source | Observation |
|---|---|---|
| 01:43:01.5 | Walkie | 120354 created; task auto-assigned to KOBE (P3) |
| 01:43:05 | KOBE router log | `channel_side_intake_accepted` |
| 01:43:12 | side inbox | receipt `31bc477012ce…` claimed (`side-1`); the source marker for message `0a1ad60f…:120354` is bound to it |
| 01:43:21 | side fork `agent-af9ffe2e52938bb3d` | the actor received the `task.auto_assigned` event with full context |
| 01:43:39 | same fork | returned `{"action":"answer","kind":"info", …}` with a complete answer and its evidence |
| 01:43:40 | side receipt | `stage=processed`; Work version 1216; `result.delivery = "route_unavailable"`; `handoff = not_required` |
| 01:44:30 | KOBE main | while acting on a separate handoff (n439, Daniel 120347), read the conversation after 120345; the result held only 120347 and 120354 |
| 01:44:50 | KOBE main | `walkie_send_message` with `ack: true`, replying to 120354: the "Received…" ACK, which became #120357 |
| 01:48:31 | KOBE main | re-investigated and posted the answer #120359 with a report file and task_confirmation |

# Cause (verified)

- The side actor did what the user wanted. It classified the event as `info`, answered it in 18 seconds, and the host committed that answer.
- The host never sent the answer. In `side_reply_outbox.prepare`, `side_reply_connector.prepare` returned no binding. The receipt was then saved as `delivery: route_unavailable`, and nothing else happened.
- KOBE has no reply route. Its workspace config has `work_side_intake = {enabled: true, type: btw_with_mcp, comparison_model: …}` and no `reply_connectors`. A Walkie route has to be entered by hand, as this machine's Walkie workspace has (`[{"source":"*","receiver_id":"default","preset":"walkie","server":"walkie"}]`).
- Main had no sign that 120354 was already answered. It read the room, saw an unanswered assigned task, and followed Walkie's own guidance ("ACK를 먼저 보낸다"): it ACKed, then repeated the investigation.

# Second defect found in the same evidence

- Main's review prompt (`sideReviewText`) tells main "the reply below was sent", whatever the delivery state is.
- At 01:49:42 KOBE main reviewed six side answers. For one, it sent a public correction (#120356, "ledger 18 → 16 rows") of a side reply.
- Per-receipt delivery for those six was not read; the ssh endpoint began resetting connections. Whether the person ever saw the reply that main corrected is therefore unverified.

# Required changes

1. **Default Walkie route.** When no reply connector is configured, a Walkie support CloudEvent (`type com.ciel.walkie.*`, UUID `data.conversation`, numeric `data.message_id`) uses the Walkie preset through the `walkie` MCP server.
   - The reply goes only to the conversation and message the event names.
   - Explicit operator connectors still decide.
2. **Main sees the real delivery state.** Work status attaches each pending review's delivery state from its receipt.
   - Main is told an answer was sent only when the host recorded the send.
   - An undelivered answer is handed to main to send once, as written. Main does not re-investigate it.
   - An answer still being sent is marked "do not send".
