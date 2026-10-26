---
type: Fix Verification
title: Bounded automatic retry for empty and reasoning-only Ollama turns
description: Ollama turns that end with done_reason=stop and no visible text or tool call are replayed inside the same SSE response, up to empty_response_retries (default 1, max 2).
tags: [ollama, glm, semantic-retry, sse, reasoning-only]
timestamp: 2026-10-24T21:10:00Z
---
# Problem (observed)

The `ciel-code` session (router 9464, a7e28f9, `glm-5.3-flash` on `ollama-cloud`) received three `ollama_reasoning_only_stream ... done_reason=stop` turns and one `ollama_empty_stream` between 14:43 and 14:46 ([observed-glm-failures.txt](evidence/observed-glm-failures.txt)). Each one ended with a notice, and the user had to type `계속`.

Ollama forwarding retried only HTTP/transport failures. A successful HTTP stream with an empty result was finalized as a normal turn.

**Correction (2026-10-24, later):** all four observed turns, and five more up to 16:30, were preceded by `dropped ExitPlanMode while plan mode is not active`. The model called `ExitPlanMode` and the router removed it, see [/2026/10/24/diagnostics/ciel-code-plan-exit-drop/finding.md](/2026/10/24/diagnostics/ciel-code-plan-exit-drop/finding.md). This retry does not apply to those turns, because the raw tool call counts as tool intent. It covers only turns where the upstream stream itself has no text and no tool call, which were not observed in this session.

# Change

- `ollama_semantic_retry.py`:
  - `retry_reason` qualifies only a terminal `stop` with no visible text (after thinking-markup filtering), no raw or pseudo tool intent, no invalid or error payload, and no runaway.
  - `configured_retries` reads the provider key `empty_response_retries`. It accepts only a real int, defaults to 1, clamps to 0..2, and returns 0 for the remote bridge.
  - `replay_body` copies the original wire body and appends one runtime instruction. Failed attempts are never replayed.
- `streaming_anthropic.py`: a per-attempt loop inside one SSE message. Headers and `message_start` are sent once, content indices keep increasing, and `message_delta`/`message_stop` are sent once.
  - Recovery shows only the real answer or tool call.
  - Exhaustion shows one notice, `end_turn`, and an `error` activity, with no TaskList synthesis. Delivery stays confirmed so the channel cursor commits and no external message is injected again.
- `ollama_forwarding.py`: reopens once per retry with the same endpoint, headers, timeout and rate limiting, and a disconnect-aware wait. Retries are disabled for the remote bridge and the compatibility probe. The non-stream path is bounded the same way.
- Menu, CLI and status: Ollama option row `Empty response retries`, and `ciel-runtimectl ollama-options empty_response_retries=0..2` (with `unset:`). The value is stored at the provider top level and never sent to Ollama.

# Verification

| Check | Result |
|---|---|
| New tests (stream, policy, options), Python 3.11 / 3.13 / 3.14 | 35/35 each ([py311](evidence/new-tests-py311.txt), [py313](evidence/new-tests-py313.txt), [py314](evidence/new-tests-py314.txt)) |
| Expanded exact-file regression set, isolated runner | 778 run, 0 failed, 122 pre-existing retired-MCP skips ([log](evidence/regression-tests-final-py311.txt)) |
| ruff, compileall, `git diff --check` | pass ([static-checks.txt](evidence/static-checks.txt)) |
| CLI set / clamp / reject / unset | 2→2, 9→2, 0→0, `true` rejected (value unchanged), unset→1; never in `ollama_options` ([menu-and-cli-check.txt](evidence/menu-and-cli-check.txt)) |
| Menu row | `Empty response retries [1/0/2 extra; default 1, 0 off, max 2]` for `ollama` and `ollama-cloud` ([menu-rows.txt](evidence/menu-rows.txt)) |
| Real Claude CLI 2.1.310 + this worktree's runtime + fake Ollama | see [e2e-real-cli.md](evidence/e2e-real-cli.md) |

Real CLI E2E, one typed prompt per run and no follow-up input:

| Run | POSTs | Router log | Screen |
|---|---|---|---|
| recover, retries=1 | 2 (reasoning-only → answer) | `ollama_semantic_retry … reason=reasoning_only`, `ollama_semantic_recovered attempts=2` | answer shown, no retry notice ([shot](evidence/run-recover-r1/shots/03-response.png)) |
| exhaust, retries=1 | exactly 2 | `ollama_semantic_exhausted attempts=2` | one exhausted notice ([shot](evidence/run-exhaust-r1/shots/03-response.png)) |
| exhaust, retries=2 | exactly 3 | retries 1/2, 2/2, `exhausted attempts=3` | one notice, "after 3 attempts" ([shot](evidence/run-exhaust-r2/shots/04-after-quiet.png)) |

Live routers 9385/9464/9489/9505/9524 kept the same PIDs before and after every run ([before](evidence/live-health-before.json)).

# Limits

- The SSE evidence is the router's own trace plus the transcript and screen; no independent proxy.
- No tool call was exercised in the real CLI runs; tool recovery is covered by unit tests only.
- Not observed against the real `glm-5.3-flash` service. A retry does not prove the model-side cause is fixed.
- Shipped together with the plan-mode fix; the combined regression and release record are in [/2026/10/24/fixes/plan-mode-reminder-text/verification.md](/2026/10/24/fixes/plan-mode-reminder-text/verification.md).
