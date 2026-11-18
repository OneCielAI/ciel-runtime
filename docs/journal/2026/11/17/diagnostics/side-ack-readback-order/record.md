---
type: diagnosis
title: Side SSE replies do not follow ACK → readback of understanding → queue → completion report
description: The host ACK's "복기" is the event text copied verbatim. The side fork's understanding arrives later in a second message that starts with "ACK —" again and ends with the queue line. Nothing reports to the source when the queued Work is finished. Seen on Celly (G:\ciel-bridge-service) and Walkie (G:\ciel-walkie), both running 42b1ff8 or later.
timestamp: 2026-11-17T17:40:00Z
tags: [side-intake, ack, readback, celly, walkie, sse, reply-order]
---

# Questions (user, 2026-11-17)

> 샌드박스의 celly 세션에서, side 액터에게 전달해주는 시스템 프롬프트가 뭐지?

> 메시지를 받아서 정말 고대로 readback하는데.복기라는것은 이해한사항을 다시 보내주는거고.. 작업큐를 정리한다음에. 작업완료상태를 다시보고해야하는데 이러한 순서를 전혀 안따름

> g:\ciel-walkie에서도 동일한 현상발견

In English: "In the Celly session, what is the system prompt given to the side actor?" Then: "It reads back the message exactly as received, but a readback (복기) means sending back what was understood. After organizing the work queue it should report the completion state again. It does not follow this order at all." And: "The same thing happens in g:\ciel-walkie."

# What the side fork receives (Celly, read-only)

Sample: Celly session `0aefa936`, fork `agent-af48a7c5627ed4a34`, event `3237517a73a7`, 2026-11-17T17:25Z. The answering model was `deepseek-v4.1-flash`.

**No separate system prompt.** A native fork inherits main's system prompt and conversation: record 0 is `fork-context-ref` with `parentSessionId` and `parentLastUuid`.

**The fork's first user message has two parts.**
1. Claude Code's `<fork-boilerplate>`: "worker fork, one directive, report once and stop".
2. "Your directive:" followed by the JSON that `side-intake.js` builds:

| Field | Size (characters) |
|---|---|
| `event_id` | 66 |
| `parent_session_id` | 45 |
| `claim_generation` | 34 |
| `external_event_preview` | 7,451 |
| `source_context` | 12 |
| `original` | 9,222 |
| `route` | 29 |
| `workload` (Work projection) | 85,075 |
| `handoff_context` | 2 |
| `priority_policy` | 10 |
| `instruction` | 6,982 |

The `instruction` is the fixed text in `side-intake.js`. It covers:
- what the fork may not do;
- reading `source_message` first;
- classifying the event as status, info, clarify or request — a request's reply restates the person's requirements;
- time limits, recorded waits, relay and gate re-checks;
- returning exactly one decision JSON.

# What is sent to the source (measured)

Order of messages per event:

| # | Sender | Text |
|---|---|---|
| 1 | Host, immediately (`side_ack_outbox._readback`) | `ACK — 요청을 받았습니다.` + `복기:` + the event's own message text, copied with whitespace collapsed (≤1,200 chars). No model reads it first. |
| 2 | Host, after the fork decides (`side_work_decision.apply`) | request: `ACK — ` + the model's restatement + the queue line (e.g. `n964 갱신 · P1 · 대기 1번째 (앞 0건).`). status/info: the model's answer + `큐에 등록하지 않았습니다.` |
| 3 | — | Nothing. No code reports to the source when the queued Work item is finished. A search of `ciel_runtime_support` found no such path. |

Receipts (identifiers only; texts stay out of the repository):
- **Celly `563df71d82c7`** (P0, person's rejection of a guidance). Message 2 is a request: it starts with "ACK — …" and ends with "n964 갱신 · P1 · 대기 1번째 (앞 0건)." Message 1 had already been an ACK, so it was acknowledged twice.
- **Celly `492f86f60ac3` and `bd4f1af42300`** (P0). Status and info answers that end with "큐에 등록하지 않았습니다."
- **Celly `bd4f1af42300`.** An audit bot treated an earlier ACK message as an action request. The side's own summary calls that message a 복기 ACK of a previous instruction. Whether it was the host's verbatim readback was **not verified**.
- **Walkie `4f6b1c2cc02b`** (P3, a person's rendering bug report with two @mentions). `external_ack=submitted`, still processing. Message 1 is built by the same `_readback`, so it repeats the report and its mentions verbatim. That text is derived from the code; the posted Walkie message was not read.

# Cause

- **The verbatim echo is my design.** I wrote `_readback` in `a75870f` to answer the earlier requirement that the ACK and readback be immediate regardless of priority. To be immediate, the host composes it without a model, so it can only copy the text, not restate what was understood.
- **Understanding arrives late and is fused with the queue result.** The model's restatement exists only in message 2, after the fork decided, and is fused with a second "ACK —" and the queue line.
- **There is no completion report.**

# Decision needed (not changed)

The earlier requirement (readback immediate, any priority) and this one (readback = what was understood) conflict. A host-only message cannot restate understanding.

The user must decide two things:
- where the understanding readback comes from, and when;
- what "작업완료상태" reports: the queue registration, or the Work item's own completion.

# Decision (user, approved plan 2026-11-17)

The conflict above was decided in the approved plan:
- **Receipt ACK.** The host sends it at once, with fixed text and no copy of the message.
- **Understanding readback.** It comes from the side actor in its own words, before the board changes.
- **Result.** The final reply states the committed queue outcome, with no second "ACK".
- **Out of scope.** Reporting when the Work item itself finishes is not part of this request.

Fix: [../../fixes/side-ack-understanding/record.md](../../fixes/side-ack-understanding/record.md).
