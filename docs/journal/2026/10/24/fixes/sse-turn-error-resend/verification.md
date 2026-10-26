---
type: Fix
title: Resend SSE inputs after transient turn errors
description: Injected runtime inputs whose turn dies on a transient upstream error are resent (10 attempts, 10 s apart); everything else fails at once with a typed reason and delivery moves on.
tags: [delivery, sse, runtime-input, codex-app-server, claude, retry]
timestamp: 2026-10-24T10:15:00Z
---
# Problem

- The .14 snapshot ([diagnostics](../../diagnostics/dot14-turn-and-delivery/snapshot.md)) shows every failed SSE input as `queued > submitted > failed`, with no retry:
  - Ara: 53 × "unexpected status 504", 26 × "high demand", 3 × "stream disconnected".
  - Kobe: 53 × `turn_failed`.
- Ara's 20 queued inputs were left `queued` forever after the launch fast-forward.
- Claude transcripts count any assistant record after the prompt as a reply, including `isApiErrorMessage`. A Claude turn that died on an API error was therefore recorded as `replied`.
- User decision (2026-10-24): resend the original message, 10 attempts, 10 s apart.

# Change

**Shared policy: `runtime_input_retry.py`.**
- Classifiers read typed fields only:
  - Codex `codexErrorInfo` and `httpStatusCode`, with a fallback to Codex's own `unexpected status NNN` prefix.
  - Claude `error`, `apiErrorStatus`, `apiErrorIsTransient`, `resendCannotCure` and `truncatedAfterOutput`.
- Transient:
  - Codex: `serverOverloaded`, or a connection/stream variant with no status or with status 408/500/502/503/504/529.
  - Claude: `server_error`/`api_error`/`overloaded_error`/`timeout_error` with a 5xx status or none; `rate_limit` only when `apiErrorIsTransient` is true.
- Never resent: bad requests, `other`, context/usage limits, auth, policy, user interrupts, output already shown (stream recovery owns those) and anything unclassified.
- The marker line `[ciel-runtime 재전송 n/10 · 이전 시도 업스트림 오류: <type>]` opens the message body. The delivery prompt's first line is left alone, because it carries the id that confirmation reads and the automatic-input prefix.
- Settings live under `runtime_input_retry.limit` (default 10, 0 disables, max 30) and `interval_seconds` (default 10, 1–600).

**Status repository: `runtime_input_status.py`.**
- `schedule_retry` moves `submitted -> queued` and stores the `data.retry` record. The resend count is a compare-and-set.
- `claim_retry` and `release_retry_claim` give single-sender ownership.
- `expire_launch_backlog` marks queued rows below the launch clear floor `skipped / launch_replay_window_expired`.

**Codex: `codex_desktop_injection.py`.**
- `turn/completed` is classified, using a final `error` notification when the turn's own error is untyped. `willRetry=true` is only logged.
- Tool items mark the turn, and `tool_activity` is recorded on the resend.
- A scheduled resend stays ahead of every later input. It waits for its due time and an idle thread, then goes out as a fresh `turn/start`. A paused compaction gate defers it without spending the attempt.
- A new injector adopts scheduled resends. A claim that is stale for 120 s fails as `retry_send_uncertain` and is never sent again.

**Claude path.**
- `channel_transcript.wake_state_evidence_from_text`:
  - An API-error record answering the prompt becomes `turn_failed:<0|1>:<epoch>:<label>`, not `completed`.
  - A later prompt or queue record for the same id starts a new observation.
- `channel_inflight`: a new optional `turn_failed` effect. `retry` rescans from the durable cursor without committing. `failed` commits and moves on. `stale` means the error belongs to an earlier attempt and is treated as missing evidence.
- `ChannelWakeDeliveryRepository.turn_failed` schedules the resend, checking ownership against the attempt id, or fails the input with its typed reason. Inputs without a status row keep the old completion reading.
- `inject_pending_channel_messages` holds a scheduled resend, and every input after it, until it is due and no turn or tool call is running. It then sends the marked copy. A failed turn with no scheduled resend is never replayed.

**Launch, compaction, visibility.**
- `prepare_for_launch` expires the stranded queue.
- `recovery_status` reports a state left by an earlier scope as `stale_scope ... not_gating` (diagnostic only, never opens admission). `checkpoint_unconfirmed` now records `failure_at`.
- Delivery health and its text show resending, resend-exhausted and expired-at-launch counts plus the last failure.
- The statusline also shows a failure for 10 minutes after it happens.
- Menu "10. External event inputs" shows `resend Nx/Ss` and has rows for the attempt limit and the interval.

# Verification

**Confirmed (✅).**
- Exact-file isolated runs ([focused-tests.txt](evidence/focused-tests.txt)): 909 tests OK (122 platform skips) across 23 related files, including `test_architecture_contracts` and the full `test_channel_bridge`. ruff, metadata check, compileall and `npm pack --dry-run` all pass. `ciel_runtime.py` is unchanged at 4970 lines.
- Real codex-cli 0.158.0 app-server behind a fake Responses upstream ([codex_resend_e2e.py](evidence/codex_resend_e2e.py), [result](evidence/codex-resend-e2e.json)):
  - A 504 arrives as `codexErrorInfo {"httpConnectionFailed": {"httpStatusCode": 504}}` with `willRetry: false`.
  - A 400 arrives as `codexErrorInfo "other"`.
  - A: 504 three times. Resends went out 10.4 s apart with markers 1/10, 2/10, 3/10 in the upstream request body, and the delivery first line was kept. Result `replied`.
  - B: 400. Failed `turn_failed:other …`, no resend, and the next input was `replied`.
  - C: 504 every time. 10 resends, then `retry_exhausted:httpConnectionFailed 504`. The input that shared the first failing turn was resent next and was `replied`.
- Real Claude Code 2.1.310 transcripts against a fake Anthropic upstream ([claude_resend_e2e.py](evidence/claude_resend_e2e.py), [result](evidence/claude-resend-e2e.json)):
  - 503 writes `error=server_error, apiErrorStatus=503`, which becomes `turn_failed:1`. The inflight action is `retrying` with no cursor commit, and the row is queued with retry attempt 1.
  - The injector held the resend before its due time, then wrote the marked body. `claude -p --resume` with that prompt reached `completed`.
  - A persistent 400 writes `error=unknown, apiErrorStatus=400`, which fails `turn_failed:unknown 400` with no resend.
- Real prelaunch menu of this worktree, isolated, driven by key events ([menu_e2e.py](evidence/menu_e2e.py), [menu_driver.py](evidence/menu_driver.py)):
  - The main row shows `resend 10x/10s`.
  - The panel shows `Resend after upstream error [10 attempts]` and `Resend interval [10s]`.
  - Entering 5 saved `{"limit": 5}` and the row became `resend 5x/10s`. Shots are in [menu-shots](evidence/menu-shots/).
  - Cleanup stopped only that run's process tree.

**Observed, cause not established.**
- Claude Code 2.1.310 sent a 400'd request again on its own (4 requests in under a second) even with `CLAUDE_CODE_MAX_RETRIES=0`. When a later one got 200, the turn completed normally. Why it retries a 400 was not investigated.
- Two inputs submitted together into one Codex turn share that turn's outcome. Each one is then resent on its own, lowest id first.

**Not verified.**
- A resend that sits behind a running turn on the Claude path is picked up by the 5 s safety rescan. The effective spacing there is 10–15 s and was not measured in a live session.
- The "Session delivery" menu row with a live router was not driven. The text it renders comes from `delivery_health_text`, which the E2E exercised.
- No live session, router or remote host was touched during verification.

# Release

- **Nightly.** `a7e28f9` was pushed as a fast-forward from `495cd41`. Publish to npm run 40085040778 succeeded, and so did CI run 40085040779.
- **Registry.** The `nightly` tag is `0.2.51-nightly.20261024-102326.a7e28f9`, with gitHead `a7e28f9005a91cbdb32380b65a7593d3abbf580f`.
- **Local install** ([deploy_local.py](evidence/deploy_local.py), [deployment](evidence/deployment.json), [output](evidence/deploy-output.txt)):
  - The exact version was packed and extracted with member checks, then installed with `npm install -g`.
  - 454 files were replaced one at a time, atomically, into `~/.local/share/ciel-runtime` and `~/.local/bin`. `install.ps1` was not used.
  - The 15 changed modules have identical hashes across git, tarball, snapshot and global npm.
- **Installed-path tests** ([installed-tests.txt](evidence/installed-tests.txt)): 256 tests OK, importing `~/.local/share/ciel-runtime/ciel_runtime_support/runtime_input_retry.py`. `ciel-runtime --version` reports the new version.
  - It also prints a `SyntaxWarning` ("'return' in a 'finally' block") from `codex_app_server_session.py:417`. That file was last changed in `9ac82f0`, not in this release.
- **Live routers.** The six live routers kept their PIDs and old versions:
  - 9385 → 69748
  - 9464 → 79804
  - 9465 → 96784
  - 9489 → 95780
  - 9505 → 33444
  - 9524 → 81068
- No session was restarted and no remote host (.14) was touched. The change applies only to sessions launched after this install.
