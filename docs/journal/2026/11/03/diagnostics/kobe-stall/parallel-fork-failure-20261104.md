---
type: diagnosis
title: Kobi fork subagent failure follow-up
description: Current Kobi parallel attempts start but fail at the native Agent name gate; a new /work-mode parallel view exposes pending names and recent failures.
timestamp: 2026-11-04T03:45:00Z
tags: [kobi, work-parallel, fork, agent-spawn, ui]
---

# Current observed failure

Read-only SSH inspection of DESKTOP-GBJII5F (100.64.0.14:1004), sandbox u24, found the current Kobi transcript `219f1b51-1044-4d57-ba0e-27f8d12b3bcd.jsonl` modified 2026-11-04T03:34:20Z. The installed package is `@one-ciel-ai/ciel-runtime 0.2.51-nightly.20261104-005240.f30bff6`, not `fc4e6e9`.

At 2026-11-04T01:22:04Z the exact transcript tool-use block shows:

```json
{"name":"mcp__ciel-runtime-router__work_parallel_run","input":{"item_id":"n227","priority":"P1","role":"worker","expected_version":291}}
```

It returned `native_agent_required` with `name: work-mw1fluyy-wjrg6vvo5cc`. At 01:22:13Z the following native Agent call was:

```json
{"name":"Agent","input":{"description":"Approved Work agent","subagent_type":"fork","prompt":"Execute only approved Work item n227. Report question or result through work_parallel_event. Do not configure policy or accept your result."}}
```

The `name` field is absent. `ciel-work/hooks/register.js` stores the generated key in process-local `parallelPending` in `work_parallel_run` and requires the identical name in `agent.spawn`. Without it, the spawn is denied before `reserve`; no worker starts. The transcript error is `Subagent spawn denied by a plugin: Enabled Work parallel dispatch requires work_parallel_run and a verified native fork.`

This is not a policy/slot failure: `work_parallel_status` succeeds, policy is enabled, and no cap/rate error occurs. The local successful fixture `parallel-e2e-0b4b4bf3` included the generated `name` in the Agent call and then produced a running lease.

A second transcript event at 03:26:47Z shows `work_parallel_event` called as a free-text note without the required event enum and `lease_id`, yielding `unknown conversation lease`. That is a model call-shape error, not evidence that the ledger itself is missing.

# Local correction

`ciel_runtime_support/claude_mods/ciel-work/hooks/register.js` now:

- adds `/work-mode parallel`, showing policy, ledger version, active device slots, P1 reserve, launch rate, this conversation's leases, pending native Agent name, and the last five in-session parallel failures;
- makes `work_parallel_run` return `required_parameters.name` and explicitly warns that omitting it starts no worker;
- makes spawn denial state the precise failed condition (omitted/unmatched name, wrong conversation, non-coordinator parent, non-fork, wrong subagent type, or wrong origin);
- records native spawn denials and status failures in the recent-failure list;
- points `/work-mode`, summary, and help text to the parallel view.

`tests/work_mod_harness.mjs` scenario `parallel-missing-agent-name` reproduces the Kobi sequence. `tests/test_work_mod_write_gate.py` asserts the specific denial, pending name, UI text, and recent failure. The full focused gate suite passed: 17 tests and 10 subtests. `claude plugin validate .` passed. `claude plugin test .` is 84 pass / 7 fail, the same seven-name failure set already reproduced on the HEAD baseline.

# UI instructions

In a Claude session with Ciel Work attached:

```text
/work-mode parallel
```

Read:

1. `Policy` line for enabled/paused and slots.
2. `This conversation leases` for reserved/running leases.
3. `Pending native Agent dispatch` for the exact `name` the next Agent call must include.
4. `Recent parallel failures in this session` for the last denial.

Before launch, the launcher menu `LLM options` also shows `Work 병렬 실행`, pause, concurrent fork limit, P1 reserve, launches/minute, and rework limit, via `work_parallel_menu.py:16-24` and `provider_option_panel.py:105-107`.

# Limits

- No remote sandbox file, process, service, or configuration was changed or restarted during this diagnosis.
- The direct `work-parallel.json` ledger could not be read through the read-only SYSTEM probe because the roaming workspace directory returned WinError 5. The transcript's authenticated mod results are the stronger current evidence for dispatch behavior.
- The correction is in the local worktree only; it is not committed, pushed, published, or installed on .14. Kobi will keep failing the same way until a separately approved deployment and a new session/restart path are performed.
- The earlier stale-transcript hazard remains: old files `6094...` and `5ac1...` have zero `work_parallel_*` mentions but are not the current session. Diagnosis must select the live transcript by modification time/session identity, not byte size.
