---
type: implementation-record
title: Priority fork models and immediate P0 implementation
description: Implemented per-priority fork models, side-fork model, existing-root P0 immediate launch, four surfaces and CI deduplication.
timestamp: 2026-11-15T05:31:00Z
tags: [work, p0, models, side, ci, beta]
---

# Requirement restored

This implementation supersedes the earlier NO-GO and disabled-model conclusion in this bundle. The measured Claude Code mod API supports a streaming `turn.step` hook with `e.agentId`; a separate `ciel-work-gate` plugin sees ciel-work-owned forks and can rewrite each fork request's `model` before the first request leaves.

# Implemented

- `work_parallel_models[current_provider]` stores independent `P0` through `P5` and `side` model ids. Missing/unavailable values inherit main.
- The shared Work settings projection exposes seven enum rows to the initial menu, web Settings, REST/MCP and `ciel-runtimectl work`.
- The gate resolves router aliases and direct-gateway upstream ids, identifies Work forks by bound host or pre-bind `dispatch_name`, and applies the selected model from step zero. Main and user agents are untouched.
- Existing-root `priority=P0` writes canonical P1 plus effective-P0 marker authority, then the pump drains P0 before ordinary work even when parallel execution is disabled/paused or plan approval is pending. The backend deterministic P0 dispatch name is used for native spawn.
- A new goal created at P0 is created at P1, then activated as P0 in the same call, on MCP, REST and the web Add goal form ([new-root operation](new-root-p0-operation.md)); this replaces the earlier refusal.
- REST/web and `ciel-runtimectl work priority` route existing-root P0 through the same application boundary. Web board rows and parallel status show effective P0.
- Released-wait projections now include the allowlisted item title and render it instead of only the id.
- CI removes duplicated branch-push runs and publish-time `npm test`; release publication waits on sharded test, quality and Python-minimum jobs.

# Evidence

- `claude plugin validate ciel-work-gate`: passed after converting `turn.step` to the required async generator.
- Gate harness: 28 passed.
- Focused feature/settings/surface suite: 188 passed + 100 subtests in 46.52 s.
- Immediate P0 real-backend mod scenario: 1 passed; spawn name `work-p0-<hex32>`, disabled+paused policy retained, assignment/spawning/bind observed.
- Changed priority/model/wait paths: 21 passed in 6.11 s.
- ciel-work plugin tests on local Claude Code 2.1.321: changed and HEAD baselines had identical pass/fail names (23 pass, 77 fail); this is an existing version/baseline failure set, not counted as a pass.

# Release verification (2026-11-15)

- Windows, all seven isolated groups on a clean LF export of b4b390d: unit 1499 + 1324 + 1869, router 1669, runtime 458, channel 626, real-backend 111; every group EXIT=0.
- Linux (python:3.11 container): changed-module suites 328 passed + 178 subtests; real-backend 111 OK.
- Web console: 39 tests including the 10 opt-in Chromium tests.
- Four surfaces, owned run in an isolated config: REST rows, choices, set and refusal; per-provider store; REST P0; parallel `root_priorities`/`fork_models`; ctl set/list (7 rows); menu choices and description; web desktop and 390 px without horizontal scroll; a new goal created at P0 from the web form. Evidence: `evidence/release/surfaces/`.
- Initial settings menu, real ConPTY: the Work · SSE settings panel draws `P0 Work 포크 모델: _Walkie_Fable5.2` through `P5` and `SSE 사이드 포크 모델: _Walkie_Haiku_Terra`. Evidence: `evidence/release/menu/menu-work-settings-screen.txt`.

# ciel-work mod suite (claude plugin test)

The released beta's own mod suite failed 77 of 100. Two harness gaps caused most of it: `setup()` answered no `env.get` or `agent.list`, so every snapshot read and the host start phase failed. With those answered and stale expectations moved to the current contracts (host-recorded approval M1, X2 capped start retry, focus wording, the observatory's mobile render and pager keys), 97 of 100 pass. The restored suite found three product defects, fixed in 644792c:

- Stop with a board read past its 2 s wall-clock deadline returned only after a second bounded read (about 4 s); it now returns at the first deadline.
- A gate read that sees a conversation change now clears the previous conversation's native plan-file exemption, as `refresh()` does.
- Session start logged `Work forks confirmed by native agent list: 0` beside the start notice; the first observation now logs only running forks.

Open, needs the user's decision: three tests press terminal-pane controls the Work & SSE observatory (5266982, 2026-11-07) no longer draws: move a queued goal up, append an item to focus, go to the parent node. The web console (Queue up/down, Move to position, Focus), REST (`queue-position`, `focus` with ordered `node_ids`, `[]` clears) and the model tools still provide them.

# Real Claude acceptance (2026-11-15T04:43Z)

Owned Claude Code 2.1.325 with the packaged `ciel-work` and `ciel-work-gate`, an isolated config, local MCP backend, and local Messages stand-in passed all request-level checks. The first P2 request carried `fixture-p2`, the first P0 request carried `fixture-p0`, and the first side request carried `fixture-side`; main requests stayed on `fixture-main`. P0 was activated and launched while parallel was `enabled=false`, `paused=true`, and its root remained `planning`. The owned process tree had zero survivors and zero overlap with protected sessions/routers. Structural evidence: [real Claude first-request result](evidence/release/real-claude-first-request-result.json).
