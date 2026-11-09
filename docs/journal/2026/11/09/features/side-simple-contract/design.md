---
type: design
title: Side actor contract - answer what it can, recap and queue requests, one host-sent message
description: The side actor returns one decision of kind status, info, clarify or request. The host commits it, adds the committed queue outcome, and sends exactly one message through the operator's reply route. The actor never sends and never mutates Work.
tags: [side-intake, sse, work-mode, queue, design]
timestamp: 2026-11-09
---

# Request (user, 2026-11-09)

- **Answer at once whatever can be answered.**
  - A status question gets the current Work state as it stands, with no recap.
  - An information request gets read-only gathering and an organized answer.
  - Both say that nothing was queued.
- **A request gets a recap, then goes into the queue.** The recap is the model's restatement of what was asked, based on the main transcript. The message says the request's queue position right away.
- **A priority change for an existing item is judged and applied.** The queue is reordered accordingly.
- **Context and model.** The full main transcript and the separately selected side model stay. `$.model.fork` was declined.
- **ACK latency must come down.**

# Prody baseline (old contract, read-only, 2026-11-09 UTC)

| Measure | Value |
| --- | --- |
| Claim → first model response | median 49 s (n=14) |
| Claim → reply tool call | median 81 s (n=7) |
| Input per side request | median 332k tokens |
| Side model | `_Walkie_Haiku_Terra` → served as `gpt-5.6-terra` / `claude-haiku-5-5` |
| Cache on each actor's first request | 7,680 read (later requests in the same actor read about 360k) |
| Model round trips | read the Walkie message, read Work status, send the ACK, send the reply. Each one resends the full context. |

The real `walkie_read_messages` result is `{"messages":[{"id":"118784",…,"text":…}]}`. This was read from a Prody side actor transcript.

# Contract

| kind | Model returns | Host does | Message to source |
| --- | --- | --- | --- |
| `status` | `reply`: the current state, from the workload projection, the transcript and code reads | no Work change | reply + `큐에 등록하지 않았습니다.` |
| `info` | `reply`: facts gathered with read-only tools | no Work change | reply + no-queue line |
| `clarify` | `reply`: the question | no Work change | reply + no-queue line |
| `request` | `reply`: a recap from the transcript, plus `create`, `update` (an existing matching item) or `prioritize` (priority and/or `queue_position`) | one atomic commit, then reads the committed queue position | `ACK — ` + recap + `n### 등록/갱신 · P# · 대기 N번째 (앞 N-1건).`, or `현재 진행 중인 항목입니다.` |

Rules:
- The actor never implements, edits, builds, tests, deploys or executes anything.
- It searches the existing Work items before creating one, and updates the matching item instead of adding a duplicate.
- It never claims a queue position; the host appends the committed one.
- It never sends; the host sends exactly one message per receipt.

# Fewer model round trips

- **Prefetch.** For the operator's Walkie reply preset, the host reads the message the event names (`walkie_read_messages {conversation_id, after: message_id-1}`) before spawning the actor, and passes it as `source_message`.
- **No Work-read tool.** The workload projection is already in the payload; its `queue_order` comes from `queued_roots`.
- **No send tool.** Response verbs are dropped from the actor's tools. What remains is read MCP tools plus two router-guarded code tools, `work_side_code_search` and `work_side_code_read`. They are bounded to the launch workspace, deny secret paths, and redact their output.
- **Registration.** `maxTurns` is 4, `effort` is low, and `omitClaudeMd` is true.
- **Cache breakpoint.** One `cache_control` mark goes on the last text block of the projected parent prefix, but only while fewer than 4 marks exist.

# Queue placement

- `reorder_queued_root(doc, root, position)`:
  - 1-based among `queued_roots`;
  - the active root never moves;
  - an invalid position is rejected, not clamped.
- `queue_position(doc, item)` returns `active`, `queued` (with position, ahead and total) or `done`. A child item is mapped to its root.
- **Concurrent duplicates.** The host passes the digest of the unfinished goals the actor saw.
  - If a `create` finds that digest changed, it commits nothing. The claim is released once for re-evaluation (`work_changed`) and no message is sent.

# Unchanged

- The transport ACK (`event-ack/1` queued/injected).
- The handoff and wake rules, the P1 reserved slot, the side model selection and `context_mode` (full by default).
