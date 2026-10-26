---
type: Diagnostic Finding
title: ciel-Walkie turn breaks are model-ended turns while the combo served gpt-6.1-sol
description: The stops the user reported in the ciel-Walkie Claude session were upstream end_turn replies passed through unchanged; all came from gpt-6.1-sol, which the _Walkie_Opus55-Sol61-Fast combo served from 19:55Z to 22:16Z, and no runtime, transport or hook failure was observed.
tags: [walkie, end-turn, cielairouter, combo-model, gpt-6.1-sol, claude-opus-5-5, turn-break]
timestamp: 2026-10-25T22:40:00Z
---
# Report

The user reported severe turn breaks in the local `ciel-Walkie` Claude session.

| Item | Value |
|---|---|
| Router | 9505, workspace `g:\ciel-walkie`, provider `cielairouter`, model `_Walkie_Opus55-Sol61-Fast` |
| Claude Code | 2.1.310, transcript `417ef30b-1289-47a5-8921-a4d45eb93f2e.jsonl` (2.84 GB) |
| In-session complaints | 21:54:41Z "이번은 턴끊김이야? 왜 뭠춰?", 22:11:39Z "그리고 다시 턴이 끊기네" |

Times are UTC. Router log times are local (UTC−5).

# Observations

## The reported stops

The scans count, per turn, the trigger and the number of tool-calling upstream responses ([turn_end_classify.py](evidence/turn_end_classify.py), [turns-2026-10-25.json](evidence/turns-2026-10-25.json)).

| Turn start | Trigger | Tool-calling responses | Ended | Upstream model |
|---|---|---|---|---|
| 21:48:23 | external channel | 4 | 21:49:38 | gpt-6.1-sol |
| 21:49:42 | external channel (`turn_ended` reminder) | 1 | 21:50:06 | gpt-6.1-sol |
| 21:54:41 | user | 2 | 21:55:35 | gpt-6.1-sol |
| 22:10:37 | external channel | 0 | 22:10:53 | gpt-6.1-sol |
| 22:11:23 | user | 0 | 22:11:39 | gpt-6.1-sol |
| 22:11:39 | user | 1 | 22:12:43 | gpt-6.1-sol |

- Every turn ended with an upstream message whose `stop_reason` is `end_turn` and whose last block is text. None is an API error message.
- The text is a status report. At 21:49 and 21:50 the reply put the remaining work on hold until the next day. At 21:55 the model wrote that it had ended the response itself.
- Stop hooks: both hooks (`ciel-runtime-tool-guard.py`, orca) ran at each end. `hookErrors` is empty and `preventedContinuation` is false.

## Runtime and transport

Router 9505, 14:50–17:30 local (19:50–22:30Z) ([router-9505-recent.txt](evidence/router-9505-recent.txt), [router_excerpt.py](evidence/router_excerpt.py)):

- All 317 `POST /v1/messages` returned 200. There were no `upstream_direct_http_error`, rate-limit or `[ERROR]` lines.
- The router's Anthropic relay rewrites `end_turn` only on a path that logs `auto-synthesized` or `downgraded` (`streaming_anthropic.py:1151-1272`). Neither appears in the window, and neither does `anthropic_sse_client_disconnected` or `router_client_disconnected`.
- External messages that arrived during an active turn were held back (`channel_windows_console_deferred reason=active_turn`). They were not injected mid-turn.
- Other warnings in the window:
  - 3 × `anthropic_sse_event_after_message_stop_ignored event=ping`. These are ignored pings after the stream ended.
  - 1 × `dropped emitted tool call with missing required fields` at 17:18:49 local. It happened inside the native compaction request, and that compaction completed (`compact_boundary` 22:18:50Z). The same drop happened at the 11:26, 15:15 and 18:55Z compactions.
- The session called `recover_sse_delivery status` itself at 22:11:52Z. Result: `problem=false`, 0 queued, history complete. The current `/ca/tui/status` reports `not_blocked` ([tui-status-after.json](evidence/tui-status-after.json)).

## Upstream model

`_Walkie_Opus55-Sol61-Fast` is a gateway combo (`owned_by: combo`). Claude Code records the member that answered in `message.model`: `claude-opus-5-5` or `gpt-6.1-sol`.

- From 19:55:13Z to 22:16:32Z, every main-thread response came from `gpt-6.1-sol`.
- From 22:20:04Z the combo served `claude-opus-5-5` again. Since 22:13:20Z the session has worked without ending a turn: more than 25 tool-calling responses through the 22:18Z compaction, still active at 22:25Z.

Windows today:

| Window | Model | Turns | Tool-calling responses | Ended turns | Longest turn |
|---|---|---|---|---|---|
| 13:40–19:55 | claude-opus-5-5 | 30 | 956 | 25 | 177, 116, 112, 109 (several continued across compactions) |
| 19:55–22:16 | gpt-6.1-sol | 19 | 123 | 18 | 24 |

For turns started by external channel messages alone, the counts are similar:

| Model | Turns | Tool-calling responses per turn |
|---|---|---|
| Opus | 16 | 0–177, median about 4 |
| Sol | 11 | 0–14, median 7 |

Share of unique upstream responses that ended the turn, per UTC day, whole transcript ([model_stop_by_day.py](evidence/model_stop_by_day.py), [model-stop-by-hour.json](evidence/model-stop-by-hour.json)):

| Day | claude-opus-5-5 | gpt-6.1-sol |
|---|---|---|
| 10-15 | 6.7% of 1503 | 20.4% of 614 |
| 10-16 | 7.1% of 1214 | 16.8% of 720 |
| 10-17 | 4.9% of 876 | 18.1% of 1068 |
| 10-18 | 2.0% of 587 | 5.7% of 1152 |
| 10-19 | 0.0% of 111 | 3.6% of 1347 |
| 10-20 | 5.7% of 331 | 7.9% of 1548 |
| 10-21 | 3.3% of 751 | 10.0% of 1413 |
| 10-22 | 2.5% of 1415 | 4.5% of 1341 |
| 10-23 | 3.8% of 1141 | 6.8% of 1811 |
| 10-24 | 0.8% of 2229 | 5.3% of 1394 |
| 10-25 | 3.2% of 2543 | 7.3% of 813 |

The share is higher for gpt-6.1-sol on every day. These counts are not controlled for what started each turn or for what work was in progress.

## Session load

- Requests carried 590k–674k estimated tokens, 266 tools and 650+ messages.
- There were 7 native compactions today: 11:26, 13:40, 15:15, 16:17, 17:17, 18:55 and 22:18Z.
- Most turns are started by Walkie external channel messages: mentions, `follow_up_due`, `turn_ended`, and task notifications.
- Every turn end is reported to the Walkie server: 58 `agent_runtime_event_delivered` lines to `http://100.64.0.9:8787`. Today 7 of the 39 injected messages contain `turn_ended` ([external-message-keywords.txt](evidence/external-message-keywords.txt), [external_message_keywords.py](evidence/external_message_keywords.py)), and each of them started a new turn.
- Whether that loop raises the stop rate was not measured.

## Checked and not involved

- **Runtime version.** Router 9505 has run `0.2.51-nightly.20261025-050332.144bebc` since its 06:22 local spawn; the snapshot was installed at 00:15 local.
  - The per-model pattern above already exists on earlier days, which ran older versions.
  - No plan-mode or Ollama retry path logged anything. The provider is `cielairouter`.
- **A separate, runtime-caused break.** At 10:18:43 local (15:18Z), `anthropic_route_error status=500 transient retry deadline or attempt limit reached` was followed by `agent_turn_ended reason=error` ([router-9505-error-turn.txt](evidence/router-9505-error-turn.txt)). Before it, the model list timed out twice. This break is not one of the reported stops.

# Status

- ✅ The reported stops are turns the upstream model ended with `end_turn` and a text reply. The router passed them through unchanged. No HTTP, stream, delivery or hook failure was observed in that window.
- ✅ All of them happened while the combo served `gpt-6.1-sol` (19:55–22:16Z). In that window every turn ended after at most 24 tool-calling responses. When `claude-opus-5-5` answers again from 22:20Z, the session keeps working.
- 🟡 `gpt-6.1-sol` may end turns more readily than `claude-opus-5-5` in this session. The evidence is correlation only, because triggers and work differ between windows. It is not verified by a controlled comparison.
- ❓ Why the gateway served `gpt-6.1-sol` from 19:55 to 22:16Z is unknown.
  - The gateway runs as the `CielAiRouterGateway` service, and its directory `C:\ProgramData\CielAiRouterGateway` is not readable from this account (Permission denied).
  - The router saw no 429 or 5xx in that window.
- No code, configuration, router, session, queue or c‍ursor was changed. The transcript was only read.
