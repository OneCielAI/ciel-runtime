---
type: Diagnostic
title: .14 pre-restart snapshot
description: Delivery outcomes and turn endings for Ara, Edward, Emily and Kobe on 100.64.0.14, read-only, 2026-10-24 01:10 PDT.
tags: [aap-win, delivery, session-socket, turn-end]
timestamp: 2026-10-24T08:10:00Z
---
# Scope

- Reported by the user: turns break often on the latest version (Ara), the ss2 messages are barely injected, and Edward's SSE fails.
- Access: ssh with the device key as each sandbox account. Read-only; nothing was written remotely.
- Raw copy, kept local only because it holds message bodies: `%LOCALAPPDATA%/Temp/ara-diag/snapshot-20261024-0110/`. It contains instance state, a 6 MiB router.log tail, runtime-inputs, status and acks.

# Observed

- Versions:
  - Ara (u21): `f276c63`, installed 00:41 PDT.
  - Edward (u22): `2e55ca4`, installed 23:24 PDT.
  - Claude Code 2.1.310 on both.
- Both routers log at level `ERROR`, so no delivery or WARN reasons are recorded.
- Ara's current Claude session `ad4b04e9` started 06:22 UTC after an ImportSession from Codex.
  - 32 turns. Every turn ends with model `end_turn`.
  - No API error, no interrupt, no `stop_reason` other than `end_turn`/`tool_use`.
- Edward's session `354281f4`: 31 `end_turn`, plus one user interrupt at 06:56 UTC.
- Ara admission state still carries the old Codex scope `01a0f637`, phase `compacting`, requested about 21:50 PDT, deadline passed. The current scope is Claude `ad4b04e9`.
- Final delivery status since 2026-10-23 18:00 PDT:

| Session | Replied | Failed | Left queued or submitted |
| :- | -: | -: | :- |
| Ara | 25 | 63 (18:00–21:59) | 20 queued (22:30–23:10) |
| Edward | 145 | 13 (21:12–23:15) | – |
| Emily (u4) | 134 | 2 | 22 submitted without a reply |
| Kobe (u24) | 18 | 31 | 8 submitted |

- Router logs contain repeated untimestamped `ConnectionAbortedError [WinError 10053]` tracebacks.
- "ss2" in the report means SSE: the external messages that ciel-runtime injects (user clarification).

# SSE delivery breakdown

**Failed rows.** Every failed row was `queued > submitted > failed`, and its `reason` field (top level) names the upstream turn error:

| Session | Reason | Rows | Time (PDT) |
| :- | :- | -: | :- |
| Ara | `unexpected status 504 Gateway Timeout` | 53 | 18:08–20:19 |
| Ara | "high demand" | 26 | 18:34–21:45 |
| Ara | `stream disconnected` | 3 | 20:23 |
| Ara | `turn_failed` | 4 | 23:12 |
| Edward | `turn_failed` | 6 | 23:03–23:20 |
| Edward | `stream disconnected: terminated` | 5 | 21:22 |
| Edward | "high demand" | 2 | 22:28–22:40 |
| Kobe | `turn_failed` | 53 | 21:00–00:12 |
| Emily | `stale_unconfirmed` | 2 | 20:00–20:02 |

**Ara's 20 messages that were never submitted.** Ids 10365–10384 were queued 22:30–23:10 PDT.
- The Codex rollout `01a0f637` (2.6 GB) shows one turn: `task_started` at 04:52Z, `compacted` at 04:54Z, then no `task_complete` before `turn_aborted interrupted` at 06:12Z. That is 78 minutes and 199 tool calls.
- The admission state, read at 00:58 PDT, was still `compacting` for that Codex scope, with an empty `checkpoint_id`.
- `codex_desktop_injection.deliver_pending` and `_submit` return without sending while `external_admission_paused()` is true (`codex_desktop_injection.py:525,572`).
- Router log level `ERROR` hides the deferral reason, so the gate is a match with the code, not an observed cause.
- At the 23:22 relaunch into Claude, `prepare_for_launch` fast-forwarded past messages older than `CHANNEL_LLM_LAUNCH_RECENT_SECONDS_DEFAULT = 600` (`channel_delivery_context.py:204-220`). The clear floor is now 10404. Those 20 rows are still `queued` and were never delivered.

**Emily's 22 rows "submitted without a reply".** Every one of them appears in Emily's transcript as `queue-operation enqueue`, then `remove`, and then inside an `api-request-blob`. They reached the model; only the reply confirmation is missing.

**After the relaunch onto the current builds, up to 01:07 PDT:**
- Ara (`f276c63`, since 00:44): 12 replied, wait to submit 1–2 s.
- Edward (`2e55ca4`, since 23:24): 38 replied, 0 failed, wait median 2 s.

# Not established

- Why the Codex compaction at 04:54Z never reached `verified`.
- Whether the user's "turn break" is the model's own `end_turn`. The Claude sessions show `end_turn` only; the Codex turn ended with 4 user `interrupted` aborts.
