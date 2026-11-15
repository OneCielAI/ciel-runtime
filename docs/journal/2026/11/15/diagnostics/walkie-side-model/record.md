---
type: diagnostic-record
title: Walkie SSE side forks send the configured side model; the _API_KEYS-SSE-1 combo answers with an Antigravity refusal
description: End-to-end evidence chain from the stored setting to the router request, the upstream answer and the fork transcript, for G:\ciel-Walkie on 2026-11-15.
timestamp: 2026-11-15T18:35:00Z
tags: [side, sse, fork-model, cielairouter, evidence]
---

# Evidence chain

Read-only collection. Every file is under `evidence/`. Router log times are converted from local CST to UTC.

## 1. Stored setting

[evidence/settings.json](evidence/settings.json)

- Walkie's workspace config stores `work_parallel_models.cielairouter.side = "_API_KEYS-SSE-1"`. The main launch model is `_Walkie_Opus55-Sol61-Fast`. The web Settings tab shows the same value.
- Two REST `settings_update` receipts for `work_side_intake_model` exist, both loopback and committed:
  - 16:42:33Z;
  - 17:57:08Z (the config.json mtime is 17:57:08Z).
- **Not recorded:** the receipts do not store the value that was written, so the side value between 16:42:33Z and 17:57:08Z is not recorded anywhere.

## 2. What Walkie's Claude Code sent

[evidence/router-requests.json](evidence/router-requests.json)

These are the `POST /v1/messages` model aliases the Walkie router received (router 9505, log from 13:20Z):

| Period (UTC) | Main alias | Other aliases |
| --- | --- | --- |
| before 16:42:33 | `_walkie_opus55-sol61-fast` 393 | `_walkie_fable5.2` 8 (P0 forks) |
| 16:42:33 – 17:57:08 | 264 | `opencode-go-deepseek-v4.1-flash-high` 12 |
| after 17:57:08 | 106 | `_api_keys-sse-1` 61 |

- **Where a side alias comes from:** a side fork is spawned with `model: inherit` (subagent meta), so only the ciel-work-gate `turn.step` hook names another model for it. The gate takes the side slot alias from the router's `fork_models` projection of the stored setting.
- **Inferred, not recorded:** the 12 deepseek-alias requests mean that the side slot held `opencode-go/deepseek-v4.1-flash-high` between the two writes. This is an inference from that code path.

## 3. Which fork sent each request

[evidence/side-forks.json](evidence/side-forks.json)

These come from the transcripts of every `Side SSE` fork today.

| Window (UTC) | Side forks | Response model recorded | Result |
| --- | --- | --- | --- |
| 00:00 – 16:33 | 88 | claude-opus-5-5 (84) / gpt-5.6-sol (3), main's combo; 1 `server_error` | answered (87) |
| 17:02 – 17:54 | 12 | deepseek-v4.1-flash | answered (12/12) |
| 17:56 – 18:31 | 20 | 14: none | 3 × "Output token limit hit" recovery, then `max_output_tokens` "exceeded the 128000 output token maximum" |
| | | 3: deepseek-v4.1-flash (a600ac7a after 2 recoveries) | answered |
| | | 2: none, no request in their window | ended without a model request (cause not checked) |
| | | 1: none, `unknown` error at 18:31:47 | not analysed |

The request times line up with the forks:

- Each failing fork made 4 requests: the first, plus one after each of its 3 recovery prompts.
- The router's `_api_keys-sse-1` request times sit between those recovery timestamps.
- Each later request carries about 54 more input tokens (the recovery prompt).

Example, fork `a0906a22`:

- requests at 18:20:16, 18:20:24, 18:20:32 and 18:20:47Z;
- recovery prompts at 18:20:23.9, 18:20:31.5 and 18:20:41.4Z;
- error at 18:20:53.9Z.

The 61 requests after 17:57:08Z match about 15 forks × 4 requests.

## 4. What the upstream answered

- **Request path:** the router forwards `model=_API_KEYS-SSE-1` unchanged to `http://127.0.0.1:20128/v1/messages`, which is the local cielairouter gateway (`node.exe`).
- **Response shape in the router log** ([evidence/router-side-response-shapes.txt](evidence/router-side-response-shapes.txt)):
  - each side response has 7–8 frames and about 1 KB;
  - its `message_delta` data is exactly 127 characters, the compact form of `{"type":"message_delta","delta":{"stop_reason":"max_tokens","stop_sequence":null},"usage":{"input_tokens":0,"output_tokens":0}}` (an `end_turn` delta would be 125).
- **Direct probes** of `ciel-runtime-cielairouter-_api_keys-sse-1` ([evidence/probe-results.jsonl](evidence/probe-results.jsonl)):
  - **Streaming:** `message_start.model = "gemini"`, text "This version of Antigravity is no longer supported. Please download and install the latest version…", `stop_reason: max_tokens`, output 0. This happened 6 of 6 times.
  - **Non-streaming:**
    - in the first runs it alternated between `deepseek-v4.1-flash` ("pong") and `gemini-4-argon-high` (the Antigravity text);
    - in the last 4 runs it was `gemini-4-argon-high` every time.
  - **The deepseek-alias model** on the same path, streaming: `deepseek-v4.1-flash`, "pong".

# Conclusion

## Measured

- The side slot is applied. Every side request after 17:57:08Z, 61 of them, named `_API_KEYS-SSE-1`.
- That cielairouter combo answers each request from one of two members:
  - **deepseek-v4.1-flash:** answers normally.
  - **gemini / gemini-4-argon-high (Antigravity):** answers with the Antigravity "no longer supported" text, `stop_reason=max_tokens` and 0 output tokens.
- When the Antigravity member answers, Claude Code retries 3 times and then fails the fork. This happened to 14 of the 20 side forks after the change. 3 forks were answered by deepseek.
- The same side events are re-run by new forks:
  - de57d7fb5a65 ×6;
  - fdea6ee71ffe ×7;
  - a992766bc78f ×5;
  - 20a1e2356de6 ×2.

## Not known

- The value written at 16:42:33Z (not recorded).
- Why the 87 side forks before 16:42:33Z sent main's alias. The stored side value at that time is not recorded.

# Follow-up 18:52–18:58Z: the combo is fixed on central but not on the local gateway node

The setting has not changed. config.json was last written at 17:57:08Z and still stores side = `_API_KEYS-SSE-1`.

## Walkie side forks

Router lines are in [evidence/router-1854Z-side-requests.txt](evidence/router-1854Z-side-requests.txt).

| Fork | Side event | Requests | Result |
| --- | --- | --- | --- |
| `a288f22d` | 3fcbe62cbd86 | 4 to `_API_KEYS-SSE-1` via `127.0.0.1:20128`, node 6016dfc3; every answer 989 bytes / 7 frames | 3 recoveries, then `max_output_tokens` at 18:54:52Z |
| `a2c96176` | 7c410f1e22aa | — | answered by deepseek-v4.1-flash at 18:55:33Z |
| `ab4aade0` | 3fcbe62cbd86 (re-run) | — | started 18:55:14Z |

## Earlier error from the combo

The `a3d22bcc` fork failed at 18:31:47Z with: "Every target in this combo is unavailable: anthropic/claude-haiku-5-5-max — credits exhausted".

## Same request, central vs local node

[evidence/probe-central-vs-node.txt](evidence/probe-central-vs-node.txt): a streaming request for `_API_KEYS-SSE-1` with the same key, run 3 times on each endpoint.

| Endpoint | Answer |
| --- | --- |
| central `ciel-router-01.ezonebot.com` | `claude-haiku-5-5`, "pong", `end_turn` (3/3) |
| local gateway node `127.0.0.1:20128` | `gemini`, "This version of Antigravity is no longer supported…", `max_tokens` (3/3) |

## Why Walkie reaches the node

ciel-runtime's gateway selection (`cielairouter_gateway_overlay`) routes Walkie's cielairouter requests to that node. The router log shows `cielairouter_gateway_route endpoint=http://127.0.0.1:20128 node=6016dfc3…`.

## Not known

Why the node answers differently from central:

- it may hold a stale combo definition;
- it may use node-local accounts.

Neither is measured. The node process is `node.exe` pid 71648; its command line and start time could not be read from this account.
