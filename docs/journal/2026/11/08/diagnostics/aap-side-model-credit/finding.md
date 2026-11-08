---
type: diagnosis
title: AAP side model _API_KEYS-SSE-1 fails on Anthropic credit balance
description: "On Betty u42, side forks on the _API_KEYS-SSE-1 combo got 'Combo has no executable targets'. The combo's single Anthropic API-key target had run out of credit, and the gateway then dropped it from the pool. From 22:49 the side forks on both hosts ran on _Walkie_Haiku_Terra and sent Walkie replies."
tags: [aap, side-intake, sse, cielairouter, gateway, anthropic, credit]
timestamp: 2026-11-08
---

# Why `_API_KEYS-SSE-1` failed (confirmed from the .12 gateway log)

The .12 gateway is the `CielAiRouterGateway` service, a node of the shared control plane, listening on `127.0.0.1:20128`. All times below are gateway UTC unless stated otherwise.

1. The combo `_API_KEYS-SSE-1` is `[least-sessions] with 1 models`. Its only target is `anthropic/claude-haiku-5-5-max`, routed to `anthropic/claude-haiku-5-5` on the `anthropic` API-key account.
2. At 02:23:48 a one-message request through this combo succeeded (HTTP 200).
3. At 06:40:46 the first real side request, Betty's side fork with about 364k tokens, failed:
   - The target `anthropic/claude-haiku-5-5-max` failed with: `Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits. (HTTP 400)`.
   - The gateway logged `Provider anthropic quota exhausted — marking for skip on remaining targets`.
4. Each following request (06:41:01–06:42:09) logged `Least-sessions ordering emptied the pool: anthropic/claude-haiku-5-5-max: unavailable — falling through to the switch-combo 404`.
   - That 404 is the `Combo has no executable targets` that Claude Code showed.

Evidence:
- [Gateway Haiku target trace](evidence/gateway-haiku-target.txt)
- [Gateway combo trace](evidence/gateway-sse-combo-trace.txt)

**Resolving it needs either credits on that Anthropic API account or a different target in the combo.** Both are outside this repository.

# Side forks by model (agent transcripts, `ciel-work:side-intake` type)

| Host | Time (host clock, 11-07) | Model | Result |
| --- | --- | --- | --- |
| Betty u42 | 22:40:46–22:42:09 (6 forks) | `_api_keys-sse-1` | 5 `API Error: Combo has no executable targets`; the first got a `<synthetic>` reply |
| Betty u42 | 22:49:40–23:10:57 (13 forks) | `_walkie_haiku_terra` (gpt-5.6-terra / claude-haiku-5-5) | Walkie read/send tool calls, no API error |
| Prody u41 | 22:52:36–23:03:12 (6 forks) | `_walkie_haiku_terra` | Walkie read/send tool calls, no API error |

[Per-fork list](evidence/side-forks-by-model.txt)

The fork type stayed `ciel-work:side-intake` (selected model) throughout. The model's configured value therefore changed from `_API_KEYS-SSE-1` to `_Walkie_Haiku_Terra` between 22:42 and 22:49. **Who or what changed it, and where it is stored, has not been found:** no ciel-runtime config JSON on u42 names either model.

# The five API errors, fork by fork

The six `_api_keys-sse-1` forks were only **two** receipts, each tried three times. They alternated about every 17 s ([per-fork extract](evidence/fork-errors.txt)).

| Fork end (host) | Receipt | Claude Code error |
| --- | --- | --- |
| 22:40:46 | 5084b4154ee8 | `Credit balance is too low` (`error=billing_error`) |
| 22:41:01 | e0b3500cf8d6 | `API Error: Combo has no executable targets` |
| 22:41:18 | 5084b4154ee8 | same |
| 22:41:38 | e0b3500cf8d6 | same |
| 22:41:53 | 5084b4154ee8 | same |
| 22:42:09 | e0b3500cf8d6 | same |

Each fork has a single assistant row: `model=<synthetic>`, `isApiErrorMessage=true`, and no tool call. So no model ran and nothing was sent externally.

**Why the same model was retried instead of falling back to the main model** (code, confirmed):
- The gateway returned these failures inside an HTTP 200 SSE stream as an `error` event. The router logged `anthropic_sse_upstream_error_event` (`streaming_anthropic.py:1465-1467`), not `upstream_direct_http_error`.
- The router records a typed side outcome only on an HTTP error status or an exception (`claude_router.py:740-757`, `822-830`). The in-stream path records none.
- The mod therefore reported `work_side_fail` with category `turn_error` and no outcome.
- `fail()` (`side_intake_processing.py:150-173`) treats a failure as model-unavailable only on `model_not_found`/`invalid_model` or a `no_executable_targets` outcome. This one was therefore not unavailable, so no `use_parent_model` retry happened: only a 5 s, then 10 s, then 20 s backoff on the same model. Quarantine comes only at 6 attempts.
- Reproduced locally: an untyped `turn_error` gives `use_parent_model False` with 5/10/20 s retries. A typed 404 `no_executable_targets` gives `use_parent_model True` on the first failure.

**What happened to the two events** ([per-receipt list](evidence/fork-followup.txt)):
- The model setting changed and Betty relaunched. Both receipts were then handled by `_walkie_haiku_terra` forks: 5084b4154ee8 at 22:49:40, e0b3500cf8d6 at 22:52:25.
- Both forks made Walkie send calls. No event was lost.

Later observations, outside the five errors:
- Three receipts started at 22:56–22:57 were processed again after the 22:57:44 reinstall and 22:58:30 restart: 045dca669fa8, 180f1fa48d6f and cddc99470eb8.
- For 045dca669fa8, both runs contain Walkie send calls. Whether the Walkie thread received duplicate replies has not been checked.
- At 23:18 one fork ended with `Upstream Anthropic stream failed before message_stop (TimeoutError)`.
- Another ended with `409 Side parent context unavailable; request retained`. That receipt (07defb0c291f) succeeded on retry at 23:20:32.

# SSE handling state (read-only logs, host clock)

Both hosts now run `0.2.51-beta.20261108-063433.6933b8d`:
- u42: written 22:57:44, router started 22:58:30.
- u41: written 22:59:05, session relaunched 23:02:03.

Neither install nor relaunch was done from this job.

Betty u42, from 22:58:30 to 23:09:33:

| Item | Value |
| --- | --- |
| `channel_side_intake_accepted` | 4 |
| `channel_side_intake_deferred` | 0 |
| path errors | 0 |
| upstream requests | 20 `_Walkie_Haiku_Terra`, 6 main `_Walkie_Sol61-Opus55-Fast` |
| upstream error | 1 (`_Walkie_Haiku_Terra` stream error event at 23:07:11) |
| `external_event_sse_disconnected receiver=default error=EOFError` | 10 |

The receiver disconnects every 30–60 s and reconnects, and events still arrived.

Prody u41:
- The router now logs at level ERROR, so there are no INFO counts.
- Its last error is the MAX_PATH `FileNotFoundError` at 21:46:21. The router has restarted several times since, with no errors after that.
- Side forks on 22:52–23:03 made Walkie read and send calls.

Gateway, last 40 minutes:
- 74 terminal 200, 3 terminal 504, 1 terminal 400.
- The 504s are claude/codex timeouts on combos other than the side combo.
- The claude account was rate-limited (429) for about 30–50 minutes.

[40-minute summary](evidence/sse-health-40m.txt), [since relaunch](evidence/sse-health-since-relaunch.txt)
