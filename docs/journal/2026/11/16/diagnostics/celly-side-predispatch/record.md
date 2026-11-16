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

# Not verified

- **Which pre-dispatch filter skips the targets, and at what limit.**
  - The error comes from the gateway, and its 81-character message names no filter.
  - The listed 1 M-token context is above the router's estimate for the failing requests.
  - The 1.87 MB / 1.96 MB boundary is an observed correlation, not a known limit.
- **Which targets the `_API_KEYS-SSE-1` combo held at each hour.** The router log records the combo name only.
- **The node gateway's `/v1/models`.** It timed out (60 s) during this read.
