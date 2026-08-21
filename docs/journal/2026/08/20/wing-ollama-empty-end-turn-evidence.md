# Wing Ollama Cloud empty end-turn evidence

## Live environment

- SSH authentication succeeded as `wing\daniel.yun` on port 1004.
- Wing's globally installed package is
  `0.2.21-nightly.20260821-003258.0ea4cce`.
- The active Node, Python, and Codex processes started between 21:29:56 and
  21:30:48 on 2026-08-20. The failure therefore occurred after a process
  restart, not in a process retained from the prior deployment.
- The active router log is under instance `9139-f895679997f4`.

## Observed failing turn

- 21:30:57: Ollama Cloud `deepseek-v4-pro:0813` received a request estimated
  at 60,826 tokens and returned two `exec_command` calls.
- 21:31:05: after both tool results, the next request was 61,249 tokens and
  returned the same two command argument payloads. SHA-256 matched exactly for
  each command across both rounds; the result bodies differed.
- 21:31:11: after the repeated tool results, a third request was 60,856 tokens
  and 252,152 bytes.
- 21:31:13: the router logged
  `ollama_empty_end_turn_notice model=deepseek-v4-pro:0813
  latest_tool_results=update_plan`, then returned HTTP 200.
- The Codex transcript records the runtime-generated 142-character assistant
  notice and then `task_complete`.
- The last token record reports one output token and zero reasoning tokens.
  Source inspection shows the Ollama projection assigns a minimum of one output
  token even when decoded thinking and text are empty, so this is not evidence
  of one visible upstream token.

## Confirmed source boundary

- `project_ollama_response` emits `ollama_empty_end_turn_notice` only after the
  decoded response has no thinking block, no visible text, and no emitted tool
  call.
- No `dropped emitted tool call`, duplicate-tool suppression, suppressed
  thinking markup, or reasoning-only marker appears between the third request
  and the empty-turn marker. The runtime did not log removal of a third-round
  tool call or thinking block.
- The request itself was normalized before dispatch and logged removal of 688
  unmatched historical tool uses and 688 unmatched historical tool results.
  This is an observed transformation, but the current evidence does not prove
  that it caused the upstream's empty result.

## Not yet established

- The router did not preserve the raw Ollama Cloud NDJSON response. Therefore
  the evidence does not distinguish an upstream terminal message with empty
  content from malformed/unrecognized response lines ignored by the stream
  collector.
- The exact Kevin call ID
  `call_KjPlhUWIbFkQDJD6ywRc8ZCJ` does not occur in Wing's active router logs
  or its 30 most recently modified Codex session files. Kevin's missing-output
  error and Wing's empty Ollama turn are not established as the same incident.
- No runtime code was changed during this evidence collection.

## 2026-08-20 22:48 recurrence and confirmed recovery gap

- Wing was reached again over SSH port 1004. The active package was
  `0.2.21-nightly.20260821-033715.a6b8c58` and the affected Codex process had
  started at 22:47:53, so the recurrence was observed after the latest deployed
  package and a fresh process launch.
- The affected turn sent Ollama Cloud requests at 22:48:03, 22:48:11, and
  22:48:17. The first two responses emitted two `exec_command` calls each. The
  third request contained approximately 61,089 tokens and 253,092 bytes, then
  returned HTTP 200 at 22:48:19.
- The transcript recorded the runtime's exact empty-end-turn notice at
  `2026-08-21T03:48:19.141Z`, followed by `task_complete`. The router log
  contains no `codex_turn_retry` entry for that turn.
- Source inspection confirms the Ollama response projector converts a decoded
  response with no thinking, text, or emitted tool call into a distinctive
  runtime notice. The routed Codex recovery path recognized Kimi reasoning-only
  notices and ordinary preambles, but did not recognize this empty-turn notice;
  it therefore returned the projected notice without its bounded retry.

## Repair

- The routed Codex recovery path now recognizes only Ciel's distinctive
  empty-end-turn notice, removes that synthetic notice from retry history, and
  performs one continuation retry.
- A retry wins only when it returns a real tool call or visible final text. A
  second empty response preserves the original notice and does not loop.
- The rule is protocol-shape based. It contains no Wing user, machine, model,
  provider, session, or agent exception.

## Verification

- The focused memory, routed-Codex recovery, replay compatibility, and backend
  retry group passed 96 tests.
- The complete pre-release suite passed: unit 1,105, router 902, channel 372,
  and runtime 247; 136 declared environment-dependent tests were skipped.
- Ruff, documentation metadata, and `git diff --check` passed.
