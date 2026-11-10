---
type: Design
title: Immediate side claim and side-released waits
description: How an SSE event now reaches the side actor at once, how the side releases a recorded wait, and how the waiting fork or main is woken without an acknowledgment round trip.
timestamp: 2026-11-10T16:40:00Z
tags: [side-intake, sse, work, waits, claude-code]
---

# Requirements (user, 2026-11-10)

- The side actor must receive an event immediately. A worst case of 4.5 h is unacceptable.
- The side actor tells main what it processed. When that processing is correct, main has nothing further to do.
- A main or fork session may wait on git, a build, a review or a person's approval. It records briefly what it waits for. When a related event arrives, the side actor is the key that tells the waiting session at once and unblocks it.
- Releasing earlier blocks produced many permission errors, so this work had to be done carefully.

# Immediate claim

1. **Router wake frame.** After a side intake is durably committed, `claude_session_socket.py` writes one authenticated socket frame to the exact live session. The frame text is `[ciel-side-receipt-ready] <receipt id>` with `priority: now`. It carries no event content.
2. **Mod consumes the frame.** `register.js` consumes it in `session.receive` and drops it in `prompt.submit`; composer-typed text is excluded. It then starts the claim pump. The owned probe showed delivery in 1 ms whether main was idle, inside a running tool, or inside an open model stream, and the model never saw the frame.
3. **Pump drains bursts.** `runSidePump` repeats `pollSide` while a claim succeeds, up to every free slot (16 + P1 reserve). A burst no longer drains one event per 2 s tick. The 2 s clock stays as a fallback.
4. **Small claim responses.** `work_side_claim` no longer returns every settled handoff on every poll. It returns only handoffs whose receipt or follow-up still needs recovery. An empty claim with 5 answered 20K-character handoffs dropped from 110,063 characters (14 pages) to under 8,000 (no paging).
5. **Pane refresh off the claim path.** The pane's `work_side_status` refresh is single-flight. It runs immediately after a change (a receipt signal, a claim or a completed actor) and otherwise at most every 15 s. It never delays the next tick.
6. **Quarantine index.** Permanently quarantined `invalid_answer` events are indexed in `quarantine-index.json` and skipped by the claim window without being opened. Legacy rows are indexed the first time a window opens them. An unreadable index means "skip nothing".

# Recording what a session waits for

`waiting_on` already marks a Work node as blocked. Its contract now asks the waiter to name the signal and its source in one short line, for example `approval from Andy via Walkie` or `CI run 4312 result`. This wording is in the `work_node_update` tool description and the Work continuation notice.

# Side releases the wait

- **Decision field.** The side decision gains `release_waits: [{"id", "waiting_on"}]`, at most 16 entries, allowed only on `update`. The side prompt says to use it only when the event *is* the signal a wait names.
- **Host checks.** For each entry, under the board lock, the host requires:
  - the node exists, is unfinished and belongs to the same goal;
  - its current `waiting_on` equals the text the actor saw.
- **Effects.** A match clears `waiting_on` atomically with the decision and appends an audit line to the node's note. It does not change the execution phase or approval: `execution_approved` stays false. A mismatch is reported in `unreleased_waits`, and that wait stays for main's ordinary resume handoff.
- **No handoff when fully released.** If every gate on the target path was released, no main handoff is created and nobody has to acknowledge.

# Waking the waiter

Engine behaviour is from the owned probe `delivery-4f4c90b8`:

| Waiter | Delivery | Probe evidence |
|---|---|---|
| Fork running on the node's lease | `$.session.send({to:{agentId}})` | `isDelivered: true`; arrived in the fork's next request after its tool result; no new spawn |
| Fork parked in `waiting_input` | named in main's notice | resume stays on main's `SendMessage` path, which owns lease reacquire and resume authorization (item-4) |
| Main, busy | `$.session.append` user row | in main's very next request, inside the tool-result message |
| Main, idle | `$.prompt.submit` | an append starts no turn; the prompt starts one at once |

The same busy/idle choice now applies to handoff wakes. A busy main gets the wake appended into its running turn (wake state `appended`). Previously a prompt waited for the turn to end; the ciel-walkie wake waited over 1 h. The Stop gate still shows an unacknowledged `appended` handoff once at turn end.

# Already in place: telling main what the side processed

Every non-follow-up side decision is recorded in `side_reviews`, and the Stop gate shows it to main once ("Review side answers…; sends a correction only when wrong"). This already meets "알리고, 올바르면 메인은 별도 처리 불필요". It was left unchanged.

# Other fixes in this change

- **Activating a child handoff.** `work_side_handoff(activate=true)` on a handoff that targets a child (a wait gate) now activates the owning goal. Before, it failed with "handoff target is not an unfinished goal", which is what left ciel-walkie's n1235 handoff `requested`.
- **Completion gate message.** The parallel completion gate now says that nothing in the refused call was applied, and to send `waiting_on=""` in a separate update.
