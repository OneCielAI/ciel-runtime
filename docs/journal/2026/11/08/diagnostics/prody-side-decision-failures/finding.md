---
type: diagnosis
title: Prody receives SSE but most side actors return invalid decisions
description: Read-only field evidence shows Prody receives and forks SSE on beta 857fd52, while only 14 of 63 observed side actor finals satisfy the host decision schema.
tags: [prody, sse, side-intake, decision, model-routing]
timestamp: 2026-11-08
---

# Scope and access

Read-only inspection of Prody u41 on the WMS host through the established AAP SSH path. No process, setting, file or Work item was changed; no live side claim was made.

# New beta and SSE transport

Confirmed from router health and receiver status:

- runtime version: `0.2.51-beta.20261108-172016.857fd52`;
- router: 9592, one active client;
- receiver: `connected/read`, heartbeat observed, 0 consecutive failures at capture;
- last event id and time are populated, so the cursor moved past the formerly blocked frame;
- the current session has 63 `Side SSE` actor transcripts.

This means the 32K/raw-text admission fix is active and SSE reaches side actors. The failure is after admission.

# Side model actually requested

All 370 model turns recorded across those 63 actors requested this one routed alias:

`ciel-runtime-cielairouter-_walkie_haiku_terra`

Native targets selected under that alias:

| Native target | Turns |
| --- | ---: |
| `claude-haiku-5-5` | 222 |
| `gpt-5.6-terra` | 148 |

No turn requested `_API_KEYS-SSE-1`. Therefore Prody is not testing the API-SSE model; it is testing the `_Walkie_Haiku_Terra` combo. The combo changes native targets between turns, sometimes within one side actor.

# Exact final-answer validation

I replayed the installed plugin's `decisionOf` extraction and the host rules in `side_work_decision.validate` against the final `end_turn` text of all 63 actor transcripts.

| Result | Actors |
| --- | ---: |
| Valid decision shape | 14 |
| No parseable object carrying `decision` | 30 |
| `update` without `note` | 6 |
| `update`/`prioritize`/`advise` without `item_id` | 7 |
| `answer` carrying Work edit fields | 2 |
| Missing `summary` or `reply` | 1 |
| No final answer yet / aborted | 3 |

Only **14/63 (22.2%)** satisfy the host's structural contract. The remaining **49/63** cannot be committed as a valid side decision.

# What the invalid actors do

The actor prompt explicitly says to return one JSON object; it also says not to call `work_side_resolve` directly. The plugin is designed to parse the final text on `turn.complete` and call `work_side_resolve` itself. The absence of that tool inside actor transcripts is expected.

Many invalid actors instead execute work-like tools before ending in prose or a malformed decision. Example observed patterns include `Write`, `Edit`, `Bash`, `PowerShell`, `work_node_update`, and Walkie thread/message tools. Among the latest 54 classified actors:

- invalid final: 28;
- invalid actors with at least one tool error: 24;
- invalid actors using native filesystem/shell tools: 23.

This is correlation, not proof that tool use causes the malformed final. It is direct evidence that the actor is not remaining a narrow triage/decision actor.

# Conclusion

1. **SSE transport is working on the new beta.** Events reach side actors and the receiver cursor advances.
2. **API-SSE is not being used.** Every observed request is `_Walkie_Haiku_Terra`.
3. **Most failures are decision-contract failures after the model responds**, not API transport failures: no decision JSON or missing/forbidden decision fields.
4. The `_Walkie_Haiku_Terra` combo routes to both Haiku and Terra targets. Both produce valid and invalid results; neither native target alone explains all failures.

# Tool scope versus attempted tools (follow-up read, 18:5xZ)

The installed plugin registers the actor with `tools` = inherited MCP names only, and
`disallowedTools` = every `ciel-runtime-router` `work_*` tool except `work_status` and
`work_parallel_status`. Read-only replay of the newest 40 actor transcripts:

| Attempted tool | Calls | Result |
| --- | ---: | --- |
| `Bash` | 20 | `No such tool available` |
| `PowerShell` | 8 | `No such tool available` |
| `Read` | 5 | `No such tool available` |
| `Grep` | 2 | `No such tool available` |
| `ListAgents` | 1 | `No such tool available` |
| `work_node_update` | 2 | `No such tool available` |
| `work_status` / `work_parallel_status` | 7 | 6 failed: `conversation identity is unavailable` |
| Walkie send/read/threads/call_tool | 26 | executed |
| `restart_session` | 1 | **executed: `ok:true, queued:true`** |

So the native-tool boundary holds: those tools are not offered, and the model calls them anyway.
The router-control MCP tools are inside the inherited set, though, and one actor used
`restart_session` at 18:27:22Z with the reason "Restore workspace terminal tools after session
restart; ... requires immediate post-install browser verification". The installed prompt says
"Use any relevant inherited MCP ... and to act when the event requests it" and "This is not limited
to Walkie or to reply tools".

The main transcript has no restart-complete notice after 07:03Z, and the restart slot file is gone.
The last 12 MB of the Prody router log has no `runtime_session_restart` line, so whether the
request expired or was claimed is not established.

# Fix

The causes and the fix are recorded in the side-main-handoff fix journal for the same day
(`docs/journal/2026/11/08/fixes/side-main-handoff/`): explicit per-event side model, a fail-closed
read/reply tool surface with no native fork, and a durable side-to-main handoff.

# Not established

- Prody's persisted side model value: its workspace config is not readable from the read-only context. The cause of a changed setting not reaching requests was found in code and is fixed (spawn-time model binding); the owned proof shows the next event using the newly selected model without relaunch.
- Why the combo selects one native target versus the other: no selection reason is recorded in the actor transcript evidence.
