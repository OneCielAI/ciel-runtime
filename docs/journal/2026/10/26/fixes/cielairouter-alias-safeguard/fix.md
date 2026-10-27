---
type: Fix
title: Ninety-percent compact safeguard for CielAiRouter aliases
description: The router records which native model answered a CielAiRouter alias, so the existing ninety-percent compact safeguard reads usage that Claude Code stores under that native name.
tags: [cielairouter, compaction, safeguard, alias, fix]
timestamp: 2026-10-26T21:45:00Z
---
# Problem

Emily on .14 reached 102.8% of the context window. The CielAiRouter ninety-percent safeguard from 2026-10-23 exists, but it only reads transcript usage recorded under the configured model name. Emily's alias `_walkie_opus55-sol61-fast` was answered as `claude-opus-5-5` and `gpt-6.1-sol`, so it never had a sample ([diagnosis](../../diagnostics/emily14-context-overflow/findings.md)).

# Change

- `streaming_anthropic.py`: a new optional `stream_model_observer` receives `message_start.message.model`. A failing observer is logged and never changes the relayed bytes. `response_stream_context.py` passes it through.
- `claude_router.py`, `router_request_assembly.py`, `ciel_runtime.py`: a new port `observe_native_model`. It is wired only for main-session streams (the same request-start scope the stream recovery uses) and receives the request's unmodified model.
- `runtime_compaction_binding.py`: `observe_native_model` stores the native names per request alias in the compaction scope file, only for the bound epoch and session. It keeps at most 8 aliases, 16 names each, 128 characters per name, and never raises. The safeguard passes the names stored for its configured model to the sampler.
- `native_model_usage.py`: `model_names()` and `request_model_key()` are shared; `latest_native_model_usage` accepts the extra names.

Threshold, gates and dispatch are unchanged: strictly above 90% of the configured window, one queued `/compact` at a safe turn boundary, manual priority, deduplicated samples.

# Verification

- New tests fail on the previous code with 6 errors ([01](evidence/01-new-tests-on-previous-code.txt), [02](evidence/02-all-new-tests-on-previous-code.txt)).
- 28 related modules, 395 tests, pass on Python 3.14 and 3.10; live router PIDs unchanged ([3.14](evidence/03-focused-py3.14.txt), [3.10](evidence/03-focused-py3.10.txt)). Ruff 0.15.22 on the whole tree passes ([04](evidence/04-ruff.txt)).
- Real CLI runs with Claude Code 2.1.310, the real launcher and router, and a loopback CielAiRouter that answers alias `owned-route-alias` as `claude-opus-5-5` with 190,000 of 200,000 tokens ([harness](evidence/e2e/alias_safeguard_e2e.py)):

| Run | Runtime | Result |
|---|---|---|
| `run-worktree-alias-163358` | worktree | `runtime_compaction_native_model ... native_model=claude-opus-5-5`, `proactive_usage_observed`, one `/compact` typed, `attempts=1`, source `model-compact-safeguard`; compacted 190,002 → 2,682 tokens ([screenshot](evidence/e2e/run-worktree-alias-163358/02-after-compact.png)) |
| `run-previous-3c95a4b-163558` | sources of 3c95a4b | same usage in the transcript; no safeguard, no `/compact`; screen shows `0% until auto-compact` ([screenshot](evidence/e2e/run-previous-3c95a4b-163558/02-no-compact.png)) |

- Both runs: live routers 9505 and 9524 kept their PIDs, and the owned console was never the foreground window (0 of 1031 and 0 of 1253 samples).
- Invalid runs kept for the record: `invalid-run-worktree-alias-163021-catalog-capabilities` (the fixture catalog row lacked tool-calling/thinking capabilities, so launch stopped at model selection; the console was foreground for 14 samples, about 0.7 s, during that failed launch; cause not determined) and `invalid-run-worktree-alias-163143-fixture-turn-detection` (the fixture looked only at the last user message, and the route appends its own).

# Limits

- Emily's overflow was a single step from 87.98% to 102.75% in one response. A ninety-percent trigger cannot fire on a step that skips the 90-100% range; the next request would still overflow. Whether that step came from the alias switching to another upstream model is not determined.
- The names are kept per launch epoch. After a relaunch, the first response of the new launch records them again before the safeguard can use them.
- Running sessions keep the code they started with until they are relaunched.

# Published and installed

- Source fix f9c3eea is an ancestor of published 0555b6c, verified with `git merge-base --is-ancestor`.
- The earlier installed 5054af2 native run reached `compact_dispatched_once` with source `model-compact-safeguard`, preserved live routers, and sampled zero owned foreground hits out of 1058 samples. See `evidence/e2e/run-installed-5054af2-180641/result.json` and its after-compact screenshot.
- Current local exact nightly is `0.2.51-nightly.20261027-031627.0555b6c`. Its CI/publish/snapshot/shim proof is recorded in `docs/journal/2026/10/26/releases/edward-ara-nightly/verification.md`.
- Screenshot #13 compaction timeout/retry is a separate ongoing diagnostic; it is not claimed fixed by this alias usage change.
