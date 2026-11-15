---
type: feature-record
title: Main session model per workspace, applied from the next main turn
description: A work_main_model setting changes, remotely, the model main runs on, without touching the P0-P5 and SSE side fork model settings.
timestamp: 2026-11-15T20:20:00Z
tags: [work, model, settings, gate, turn-step]
---

# Requirement (user, 2026-11-15)

- **Goal:** "메인세션의 모델도 변경할수있게해줘" (let the main session's model be changed too).
- **Storage:** "워크스페이스에 저장해야하지" (save it per workspace).
- **Leave the fork settings alone:** "p0-p5, sse 모델이 지정되어있는것을 오버라이트하거나 건드리면안됌" (never overwrite or touch the P0-P5 and SSE model settings).
- **Remote change:** "원격에서 메인이 동작하고있는 모델을 바꿔주고싶은것임" (change the model main is running on, remotely).

# Design

- **Storage:**
  - `config.json` is already the workspace config (`<config>/workspaces/<digest>/config.json`; only credentials are shared).
  - The main model is stored in its own table, `work_main_models[provider] = model id`.
  - `work_parallel_models` (P0-P5 and side) is never read or rewritten by this setting.
  - `inherit` removes the entry and keeps the model the session was launched with.
- **Setting row:**
  - The `work_main_model` row (label "Main session model" / "메인 세션 모델") is the first model row of the shared settings path.
  - That path serves the prelaunch menu, the web Settings tab, REST, MCP and `ciel-runtimectl work`.
  - Choices are `inherit` plus the current provider's models (`work_fork_models.model_ids`).
  - Statuses are inherit, active, unavailable_inherits and forced_model_overrides.
- **Projection:** `work_parallel_status.fork_models.main = {model: router alias, upstream: direct-gateway id, id}`. It is None when inherit, or when the stored id is no longer in the provider's list.
- **Application (ciel-work-gate `turn.step`):**
  - For main's own steps (no `agentId`), the gate reads the projection at the turn's first step (`index 0`) and keeps that answer for every step of the same `turnId`.
  - A remote change never switches models inside a tool loop.
  - A turn first seen past step 0 (gate loaded mid-turn), or whose read failed, keeps the engine's model.
  - Forks never take the main model. Their own slots apply as before, and `inherit` forks keep the engine's model.

# Verification

## Unit and harness

- `test_work_fork_models.py`:
  - separate table, P0-P5/side unchanged;
  - projection `main`;
  - unavailable id keeps the launch model;
  - the settings row and the "applies" text.
- `test_work_fork_gate.py` (packaged gate module, fake host):
  - the model applies from step 0 and holds when the setting changes mid-turn;
  - the next turn reads again;
  - a turn first seen at step 3 is never rewritten;
  - a router failure keeps the engine model and logs no text;
  - a fork never takes the main model.
- `claude plugin validate ciel-work-gate`: passed.

## Real claude.exe

[e2e-source/run_main_model_e2e.py](e2e-source/run_main_model_e2e.py), result in [evidence/run-20261115T200147Z/result.json](evidence/run-20261115T200147Z/result.json):

- **Setup:** Claude Code 2.1.325, the packaged ciel-work and ciel-work-gate plugins, the real Work MCP tools and a recording Messages stand-in.

| Main request | Model sent |
| --- | --- |
| Turn 1, no main model set | `fixture-main` (launch model) |
| Set `work_main_model = fixture-main-chosen` through `work_live_settings` (status active) | — |
| Turn 2 step 0 | `fixture-main-chosen` |
| The stand-in resets the setting to `inherit` before answering with a tool call; turn 2 step 1 (tool result) | `fixture-main-chosen` (held for the turn) |
| Turn 3 | `fixture-main` |

- **Fork table:** the P0-P5/side table is identical before and after.
- **Guard and hygiene:**
  - guard: 1 owned kill, 0 survivors, 0 protected overlap;
  - host token hits in evidence: 0.

## Four surfaces

[e2e-source/main_model_surfaces.py](e2e-source/main_model_surfaces.py), result in [evidence/surfaces/surfaces-result.json](evidence/surfaces/surfaces-result.json). All 12 facts passed:

- **REST:**
  - the row and its choices are listed;
  - set;
  - stored as `work_main_models`, with the fork table unchanged;
  - `/parallel` shows `fork_models.main`.
- **`ciel-runtimectl work list`:** `work_main_model = _Walkie_Fable5.2 [active]`.
- **Menu:** choices plus the Korean description.
- **Web Settings tab:**
  - desktop: [web-desktop-settings-main-model.png](evidence/surfaces/web-desktop-settings-main-model.png), the "메인 세션 모델" row above "P0 Work 포크 모델";
  - 390 px: [web-390-settings-main-model.png](evidence/surfaces/web-390-settings-main-model.png), no horizontal scroll.

# Not decided here

`inherit` Work/side forks keep the session's launch model, not a changed main model. Whether `inherit` should follow the main model setting is open for the user.
