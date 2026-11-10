---
type: Diagnosis
title: Celly side actors refused with 409 after the 503be09 relaunch
description: The router compared the side actor's tools with a parent captured under the previous build; main sent no request after the relaunch, so the capture never refreshed.
timestamp: 2026-11-09T20:40:00Z
tags: [side-actor, celly, side-context, quarantine]
---

# Report

The user saw failed P3 SSE rows in Celly's observatory. Celly's Claude Code showed `API Error: 409 Side parent context unavailable; request retained.`

Access was read only:
- the router instance `9385-52ea0f8a2296` log;
- the Claude transcripts under `G:\ciel-bridge-service\.claude\projects`;
- the packaged plugin source.

The workspace state directory (side inbox, side contexts) is access-denied to this account. Nothing was changed.

# Verified

- **Versions.**
  - `router_spawn_start` 2026-11-08 22:56:51 local: `0.2.51-beta.20261109-003242.1581aa5`.
  - 2026-11-09 14:06:57 local: `0.2.51-beta.20261109-171240.503be09`.
  - Claude Code went from 2.1.319 to 2.1.321.
  - Celly exited at 14:18:45 (`child_spawn ... result=exit_0`, `router_lifetime_stopped reason=claude_exit`).
- **Refusals.**
  - `router.log` has 38 `side_context_unavailable` lines, all with `detail=side tools were not present in captured parent; recapture parent first`.
  - Each is followed by `POST /v1/messages ... 409`.
  - The refusal is `side_context_router.py:180-182`: every tool in the side request must appear in the captured parent's `tools`.
- **Side tools.**
  - The side actor's request shape (`subagents/agent-ab8a3a3e5dcaeac69.jsonl`, `api-request-shape`) has 9 tools, including `mcp__ciel-runtime-router__work_side_code_read` and `work_side_code_search`.
  - Those two come from `SAFE_ROUTER_TOOLS` in `side-intake.js`. They were introduced in `30b0d70`.
  - They are absent from `1581aa5` (`git grep`) and present in `503be09`.
- **Parent tools.**
  - Main's last model request in session `a48b586b` was 2026-11-09T06:54:50Z, under 2.1.319 / 1581aa5.
  - Its shape `dbad69a73d6a` has 97 tools and lacks exactly those two.
  - After the relaunch, main's transcript holds only local commands (`/work-mode` 20:09:11Z, `/mcp` 20:10:02Z) and no `api-request`, so the router never recaptured the parent.
  - The five SSE messages (9113–9117) went to the side intake, not to a main turn.
- **Comparison.**
  - Walkie (same 503be09) has no `side_context_unavailable` line.
  - Its main sent requests after its 12:00 relaunch; its shapes since 18:19Z have 101 tools, including both side code tools.
- **Retries.**
  - 38 side agents for 7 events. Five events reached 6 attempts: `eb0a93680f53`, `59f7c6673615`, `25df4ad92ef1`, `3fd2cc5f88e5` and `a13d4523047e`.
  - `eaf938c47a0c` and `86e13a802789` had 4 attempts when Celly exited.
  - The intervals grow (about 12 s, 14 s, 35 s, 50 s, 85 s), and the 409 turns are retried. This matches the `turn_error` path: `side-intake.js:586`, `side_intake_processing.fail` with backoff `5·2^n`, and `quarantined` at `attempts >= 6`.
  - After its sixth attempt, `eb0a93680f53` (14:10:36) had no seventh attempt in the 8 minutes before exit, although the backoff would have allowed one at about 14:13.
- **Recovery path in code.**
  - `side_intake_processing.claim` skips a quarantined row when its `model_fingerprint` equals the current one. The exception is a legacy untyped `model_error`.
  - No menu or tool re-queues a quarantined side event. `work_side_retry_reply` only retries a reply in the outbox.

# Not verified

- The stored `side_failure` of each receipt (the inbox is access-denied). The quarantine of the five events is inferred from the code and the stop in retries, not read.
- Whether those five rows are exactly the failed rows in the user's observatory screenshot.

# Consequence

- The five quarantined events do not recover on their own under the current code. A change of the side model fingerprint would make them claimable again.
- On Celly's next launch, the two events with four attempts are claimed again. The capture still lacks the two tools until main sends a model request, so they fail the same way and are quarantined after two more attempts.
- `side-intake.js` is unchanged between 503be09 and 4eebfdc. The installed 4eebfdc does not add another side tool.
