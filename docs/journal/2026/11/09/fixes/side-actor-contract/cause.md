---
type: Incident Analysis
title: Side actors answered as main or failed decision validation
description: Walkie and Prody side events kept failing after the auxiliary-context fix; four separate causes were measured.
timestamp: 2026-11-09T17:20:00Z
tags: [side-actor, sse, walkie, prody]
---

# Observations

Walkie session `feeac3ad`, running `13c6f4a`, after its 09:27 relaunch:

| Outcome | Count |
|---|---:|
| decision committed, reply `route_unavailable` | most processed receipts |
| `invalid_answer` (no decision JSON) | 6 in 30 minutes of monitoring |

Prody, running `13c6f4a` since 15:29Z: 7 of 9 recent receipts submitted, one
`invalid side decision action`, one turn error.

# Causes

1. **No Walkie reply route on the Walkie workspace.** `work_side_intake` had
   no `reply_connectors`, so every committed decision ended `route_unavailable`.
2. **Side actor answered as main.** The projection gives the actor main's
   system prompt and transcript. The side contract existed only inside the
   event JSON. Finals included main-style progress reports ("진행을
   재개했습니다 …") and calls to `Bash` and `work_node_update`, which the
   actor's tool set refuses.
3. **Main's open turn absorbed the side event.** A captured parent ends on
   main's unanswered user turn. The owned run on `31e246d` answered main's
   pending instruction (`TURN-2-DONE`) instead of the event.
4. **Valid decisions rejected for field slips.** `action:"status"`, Work
   fields on an answer, a missing create title, and an update aimed at Work
   main had already finished each failed the whole event.

Coordinator follow-ups (`handoff:` receipts) also had no route: their own
text is main's answer, not the Walkie event they answer.
