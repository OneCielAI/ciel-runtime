---
type: diagnosis
title: Celly side forks fail with "all targets were skipped by pre-dispatch filters" once the inherited request passes about 1.9 MB
description: 152 of Celly's side forks on 2026-11-16 failed with the gateway's pre-dispatch 503. Every failure hour had side requests of 1.87-5.25 MB; the only all-answered hour had 0.63-1.82 MB. The filter that skips them is on the gateway and was not identified.
timestamp: 2026-11-16T22:00:00Z
tags: [side-intake, celly, cielairouter, pre-dispatch, 503, request-size]
---

# Question (user, 2026-11-16)

> 사이드액터가 Celly 세션에서 실패했던이유는뭐지?

# Read (read-only; Celly runs on this machine under G:\ciel-bridge-service)

- **Router log:** `AppData/Roaming/ciel-runtime/router-instances/9385-52ea0f8a2296/router.log{,.1}`. Its timestamps are local time (UTC−6). The side model's request and error lines are in [evidence/router-side-requests-local-time.txt](evidence/router-side-requests-local-time.txt).
- **Side forks:** the fork transcripts in `.claude/projects/G--ciel-bridge-service/4c0f31d0-…/subagents`, every one whose description starts with "Side SSE".
- **Router build:** `0.2.51-beta.20261116-170819.b9379d2` since 15:22:47 local.

# Measured

Side fork outcomes per UTC hour, together with the size of the side model's (`_API_KEYS-SSE-1`) requests in that hour, taken from the router log `bytes=`:

| UTC hour | Forks | Answered | Pre-dispatch 503 | Other | Side request size |
| --- | --- | --- | --- | --- | --- |
| 14Z | 18 | 0 | 1 | 15 sonnet thinking-signature 400, 2 timeouts | (before 11:00 local; not in the kept logs) |
| 17Z | 50 | 0 | 48 | 2 with no result | 3.85–4.19 MB |
| 18Z | 31 | 0 | 28 | 1 pre-output limit, 2 timeouts | 4.42–5.01 MB |
| 19Z | 47 | 6 | 40 | 1 timeout | 0.62–5.25 MB (13:50 local onward under 0.7 MB) |
| 20Z | 10 | 10 | 0 | — | 0.63–1.82 MB |
| 21Z | 19 | 1 | 13 | 5 with no result | 1.87–2.30 MB |

Across the router log for 13:00–15:30 local, by request size:
- every side request of 0.62–1.87 MB went out with no error event;
- every one of 1.96 MB or more was followed by a pre-dispatch error event.

**Why the requests are this large.** A side actor is a native fork: it inherits main's whole conversation. Main's own requests at 15:04 local were about 2.0 MB (`_Walkie_Opus55-Sol61-Fast`, accepted). The side fork's request is main's conversation plus the event prompt (197–458 K characters in these forks).

**What the gateway lists for the side combo's target.** `/v1/models` on the central gateway gives `opencode-go/deepseek-v4.1-flash` a `context_length` of 1,000,000 tokens. The router's own estimate for a 2.0 MB request is about 509 K tokens.

The 15 sonnet errors at 14Z read: "anthropic/claude-sonnet-5-5-high: messages.N.content.0: Invalid `signature` in `thinking` block (HTTP 400)". At that hour the combo sent the inherited conversation to Sonnet 5.5, and the thinking blocks in it were not signed for that model.

# After the restart on aceb3aa (user question 2026-11-17: "celly 세션에서 sse가 계속 실패하는이유")

Celly's router restarted at 2026-11-16T23:50Z on `0.2.51-beta.20261116-223620.aceb3aa`, in a new session `2bb4dbb6`. Sources:
- [evidence/celly-session-2bb4dbb6-side-forks.txt](evidence/celly-session-2bb4dbb6-side-forks.txt)
- [evidence/router-2026-11-16T19-local-side-and-main.txt](evidence/router-2026-11-16T19-local-side-and-main.txt)
- [evidence/celly-forwarded-originals.txt](evidence/celly-forwarded-originals.txt)

**What failed.**
- 31 side forks between 01:01Z and 03:44Z. 30 failed and 1 answered (02:26Z, `deepseek-v4.1-flash`).
- Every failure carries the same upstream message, quoted from the fork transcripts: "anthropic/claude-sonnet-5-5-high: model — This API key is not scoped to a workspace, so this request must include the anthropic-workspace-id header with the ID of the workspace to use. Add the header, or use an API key that is scoped to a workspace. (HTTP 400)".
- So in this window the gateway's `_API_KEYS-SSE-1` combo sent the side request to `anthropic/claude-sonnet-5-5-high`. The key the gateway holds for that target was refused for lacking a workspace scope.
- That key and header belong to the gateway's provider credentials, not to ciel-runtime's request.

**Correction.**
- At 19:01 local (01:01Z) a 10.18 MB side request (router estimate 2.56 M tokens) passed pre-dispatch and reached that target.
- The earlier "≥1.96 MB → pre-dispatch 503" pattern therefore does not hold across combo states. The target set the combo uses at a given moment is not visible from here.
- What is known is that the combo answered with deepseek-v4.1-flash at some times and sent to anthropic/claude-sonnet-5-5-high at others (14Z thinking-signature 400; 01–03Z workspace 400).

**What happened to those events on aceb3aa.**
- Each of the 5 events failed 6 times and was quarantined.
- Each original reached Celly's main session as `[Ciel side 처리 실패 · 원문 전달 · … · turn_error · 6회 시도]`.
- The same backfill also forwarded 21 older `source_context` quarantines, 26 originals in all.
- `cadd1a5` (retry on main's model after a side-model failure) is not on Celly yet. On aceb3aa every attempt used the same side combo.

# By priority (user question: "p0만 실패하는것우연이야?")

The fork outcomes were grouped by the `source_priority` in each fork's prompt. Only finished forks are counted, and failed / finished is shown per hour and priority:

| UTC hour | none | P0 | P1 | P3 |
| --- | --- | --- | --- | --- |
| 17Z | 30/30 | 6/6 | — | 12/12 |
| 18Z | 16/16 | — | 15/15 | — |
| 19Z | 32/38 | — | 9/9 | — |
| 20Z | 0/3 | 0/1 | 0/2 | 0/4 |
| 21Z | 4/4 | 4/4 | 1/1 | 4/5 |

**What the table shows.**
- Within one hour, every priority failed at about the same rate. P0 failed in 17Z and 21Z, when everything failed, and the one P0 fork in 20Z answered.
- The P0 failures follow the hour, not the priority.

**Why P0 failures are more visible.**
- P0/P1 are claimed first, so their retries come quickly. Event 1e8928e8d185 made 6 attempts between 17:40 and 17:49Z and was then quarantined.
- In 21Z only 3 P0 events existed, so their repeated failed rows filled the SSE flow pane.

**Not established.** P1 fork prompts were larger (median 316 K characters, against 141 K for unprioritized events). Whether that changed P1's success rate in 19Z cannot be told from 9 forks.

# Not verified

- **Which pre-dispatch filter skips the targets, and at what limit.**
  - The error comes from the gateway, and its 81-character message names no filter.
  - The listed 1 M-token context is above the router's estimate for the failing requests.
  - The 1.87 MB / 1.96 MB boundary is an observed correlation, not a known limit.
- **Which targets the `_API_KEYS-SSE-1` combo held at each hour.** The router log records the combo name only.
- **The node gateway's `/v1/models`.** It timed out (60 s) during this read.
