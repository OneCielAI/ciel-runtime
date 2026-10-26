---
type: Fix Verification
title: Claude subagents and teammates follow the parent's current model on every non-Anthropic provider
description: Non-Anthropic Claude launches now set CLAUDE_CODE_SUBAGENT_MODEL=inherit with CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1, so Agent model arguments, agent definitions and old subagent_model values no longer move children to haiku/sonnet routes.
tags: [subagent, teammate, agent-teams, model-selection, claude-code, cielairouter]
timestamp: 2026-10-26T01:50:00Z
---
# Problem (observed)

In the local `ciel-Walkie` session (provider `cielairouter`, model `_Walkie_Opus55-Sol61-Fast`, no `subagent_model` set), the parent called `Agent` at 2026-10-25 23:51:20Z with `model: sonnet` or `model: haiku`.

- The children's own transcripts record `api-request.params.model` values `ciel-runtime-cielairouter-auto-claude-sonnet` and `ciel-runtime-cielairouter-auto-claude-haiku`.
- Children spawned without a model requested the parent combo `ciel-runtime-cielairouter-_walkie_opus55-sol61-fast`.
- These are the request fields Claude Code sent, not the response model labels. The case differs from the Emily teammate diagnosis of 2026-10-24 (`docs/journal/2026/10/24/diagnostics/emily-teammate-model/finding.md` in the `ciel-runtime-wt-timeout` worktree, not on this branch), where parent and child requests matched.

Cause in the launch environment (`claude_environment.py`, before this change):
- `CLAUDE_CODE_SUBAGENT_MODEL` was the parent's launch alias.
- `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` was set only when a provider had a `subagent_model`.

Claude Code's documented order ([agent teams](https://code.claude.com/docs/en/agent-teams#specify-teammates-and-models)):
- The spawn model and the agent definition's `model` come before `CLAUDE_CODE_SUBAGENT_MODEL`.
- With `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` those two sources do not apply, and `inherit` means the lead's current model.
- This requires Claude Code 2.1.257 or later. The installed version is 2.1.310.

# Change

`ciel_runtime_support/claude_environment.py`, `ClaudeEnvironmentProjection.build`:

| Provider | `CLAUDE_CODE_SUBAGENT_MODEL` | `..._FORCE` |
|---|---|---|
| any provider other than `anthropic` (with or without `subagent_model`) | `inherit` | `1` |
| routed `anthropic` with `subagent_model` | routed alias of that model (unchanged) | `1` (unchanged) |
| routed `anthropic` with `subagent_model: inherit` | `inherit` (a control value, never turned into a `ciel-runtime-anthropic-inherit` alias) | `1` |
| routed `anthropic` without `subagent_model` | parent alias (unchanged) | not set (unchanged) |
| native Anthropic | not set (unchanged) | not set |

Other details:
- **Direct data path.** `_apply_direct_data_path` leaves `inherit` alone. Before, every model key went through the upstream resolver, which falls back to the launch model.
- **Stored config is not changed.** `subagent_model` values are neither deleted nor migrated. Non-Anthropic launches ignore them.
- **No new option.** The selected provider and model in the menu decide the behavior, and launch, restart and native-switch cleanup reuse `CLAUDE_PROJECTED_ENV_KEYS`.
- **Not in scope.**
  - The `ANTHROPIC_DEFAULT_HAIKU/SONNET/OPUS_MODEL` family aliases are unchanged. Claude Code also uses them for background helper requests.
  - Running sessions keep their launch environment until they restart.
- **Docs.** `docs/Configuration.md` describes the rule.

# Verification

## Exact-file tests

Runner: [run_focused.py](evidence/run_focused.py). It is isolated: no inherited `CIEL_RUNTIME_*`/`CLAUDE*`/`ANTHROPIC*`/`CODEX*`/`OPENAI*`, a temporary home and config, and a free router port.

| Check | Result |
|---|---|
| `test_claude_environment`, `test_launch_data_path`, `test_claude_native_provider`, `test_deepseek_provider`, `test_meta_provider` (250 tests) on Python 3.10 / 3.11 / 3.13 / 3.14 | OK ([py310](evidence/focused-py310.txt), [py311](evidence/focused-py311.txt), [py313](evidence/focused-py313.txt), [py314](evidence/focused-py314.txt)) |
| The same `test_claude_environment` against the original `claude_environment.py` from `HEAD` ([run_unfixed.py](evidence/run_unfixed.py)) | 20 failures and 1 error: the non-Anthropic matrix (6 providers × configured none/empty/other), the routed default, the direct `inherit` case, and the native-switch cleanup ([log](evidence/unfixed-env-py311.txt)) |
| `test_anthropic_model_switch`, `test_opencode_provider`, `test_kimi_provider` (112) | OK ([output](evidence/focused-provider-py311.txt)) |
| Adjacent sets from the same branch: lease, plan mode, Ollama retry (70) | OK ([output](evidence/focused-adjacent-py311.txt)) |
| `ruff check .` (repo config), `git diff --check`, `compileall` | pass |

The new tests cover:
- non-Anthropic `cielairouter`, `ollama-cloud`, `deepseek`, `o‍pencode`, `meta` and a test provider, each with `subagent_model` absent, empty, another model and `inherit`; the stored value stays unchanged;
- routed Anthropic default and explicit override unchanged;
- a direct launch whose resolver returns nothing for unknown ids still keeps `inherit`;
- routed and direct both carry `inherit` with `FORCE=1`, and no `ciel-runtime-cielairouter-*` alias reaches the direct environment;
- switching to native Anthropic removes both keys, and the shell renderer emits `unset CLAUDE_CODE_SUBAGENT_MODEL_FORCE`.

## Real Claude CLI 2.1.310 E2E (fake gateway)

Harness: [e2e_models.py](evidence/e2e_models.py) with [console_agent.py](evidence/console_agent.py); summary in [comparison.json](evidence/comparison.json) and [verification.okf](evidence/verification.okf).

Setup:
- The launch environment comes from `ciel_runtime.env_vars()` of this branch; nothing is set by hand. The provider config deliberately carries `subagent_model=fake-haiku`.
- A loopback fake Messages gateway returns a fixed `Agent` tool call to the parent. Each child is recognised by a per-case marker in its first user turn.
- Only the path, the model, the Agent schema keys and the marker tag are stored. No prompts or bodies are kept.
- A fresh home, config and workspace; every inherited `CIEL_RUNTIME_*`/`CLAUDE*`/`ANTHROPIC*`/`CODEX*`/`OPENAI*` variable is removed; an egress sentinel is in place.
- Named teammates run in an interactive Windows Terminal session with agent teams enabled.

Child request model at the gateway. The parent is `fake-parent-a`; with `--model` and after `/model` it is `fake-parent-b`.

| Run | plain | Agent `model=haiku` | Agent `model=sonnet` | definition `model: haiku` | `--model fake-parent-b` | named teammate | new teammate after `/model fake-parent-b` |
|---|---|---|---|---|---|---|---|
| old environment (`SUBAGENT_MODEL`=parent, no FORCE), direct [e2e-old-bbeb99](evidence/e2e-old-bbeb99/result.json) | a | **fake-haiku** | **fake-sonnet** | **fake-haiku** | **a (stale launch model)** | a | not run |
| fixed, direct [e2e-fixed-ea293e](evidence/e2e-fixed-ea293e/result.json), [e2e-switch-21da1f](evidence/e2e-switch-21da1f/result.json) | a | a | a | a | b | a | b |
| fixed, routed through this branch's router [e2e-routed-ef077c](evidence/e2e-routed-ef077c/result.json), [e2e-routedswitch-82ff03](evidence/e2e-routedswitch-82ff03/result.json) | a | a | a | a | b | a | b |

Further results:
- **Agent schema.** With FORCE on, Claude Code sends the `Agent` schema without a `model` property. An explicit `model: haiku`/`sonnet` in the tool input was accepted without an error, and the child still ran on the parent model.
- **No `inherit` on the wire.** No request carried `inherit` as its model.
- **Routed console.** The routed console ([screen text](evidence/e2e-routedswitch-82ff03/screen-latest.txt)) shows `Teammate @e2e-mate-… finished CHILD-OK-named`, then `/model ciel-runtime-cielairouter-fake-parent-b`, then `Teammate @e2e-mate-new-… finished CHILD-OK-named-switched`.
- **Screenshots.** PrintWindow captures of the actual windows: [routed switch](evidence/screenshot-routed-switched.png), [direct switch](evidence/screenshot-fixed-switched.png), [fixed named](evidence/screenshot-fixed-named.png), [old named](evidence/screenshot-old-named.png).
- **Live sessions untouched.** Live router PIDs on 9385, 9464, 9489, 9505 and 9524 were the same before and after every run. Only harness-owned processes were stopped. The sentinel saw only blocked `CONNECT`s to `downloads.claude.ai` and `github.com` (updater traffic); every model request stayed on loopback.

# Limits

- **Old baseline.** It is the generated environment with the pre-fix values applied (`SUBAGENT_MODEL`=parent alias, FORCE removed), not a launcher run of `HEAD`. The stale-model case is shown with `--model`; an interactive `/model` baseline was not run.
- **Direct runs.** They start `claude.exe` with the direct projection and do not exercise gateway discovery.
- **Routed runs.** They record the model the router forwarded, not the literal string the CLI sent to the router.
- **Superseded attempts.** Four earlier attempts are kept for audit: a harness classifier bug, a pre-screenshot matrix, and an isolated router that did not start. They are listed in `comparison.json`.
- **Fake model only.** The gateway is a fake with fixed replies, and no real model was called.

# Release and local install

- Source commit `d15822ff6ff66bb4750e64c575661ccfd3253401` was pushed to `nightly` after diagnosis commit `abffba6`.
- CI [40269838107](https://github.com/OneCielAI/ciel-runtime/actions/runs/40269838107) passed all 6 jobs; npm Publish [40269837320](https://github.com/OneCielAI/ciel-runtime/actions/runs/40269837320) succeeded ([CI watch](evidence/ci-watch.txt), [publish watch](evidence/publish-watch.txt)).
- npm nightly = `0.2.51-nightly.20261026-044702.d15822f`; the registry gitHead was checked against that exact source commit before installing.
- [deploy_local.py](evidence/deploy_local.py) installed the exact version npm-globally and replaced 455 snapshot/bin files individually with `os.replace`, without `install.ps1` or restarting a session ([output](evidence/deploy-output.txt), [report](evidence/deployment.json)).
- `claude_environment.py` SHA-256 is `8824ab05d5ac564a21971cdbdb850faae072b0022dc3d04f190ed9931116adaa` in the source, npm tarball, npm-global package and installed snapshot.
- Installed snapshot tests: 305 run, OK with 1 skipped ([output](evidence/installed-tests.txt)). The skipped test assumes the repo/npm layout (`ciel-runtime-menu.py` alongside `ciel_runtime.py`), while the snapshot keeps it in `~/.local/bin`. That test passes separately against npm-global ([output](evidence/installed-npm-menu-test.txt)); npm-global and installed-bin menu bytes match. The first installed run failed only that layout check (FileNotFoundError, 305 run); the runner now makes that distinction explicit.
- Live router PIDs stayed the same: 9385=6032, 9464=54764, 9489=67368, 9505=81624, 9524=81772. Running sessions still have their old launch environment. No remote machine was updated.

# Installed-code CLI E2E

Harness: [e2e_models_installed.py](evidence/e2e_models_installed.py) with [console_agent.py](evidence/console_agent.py). It imports the modules from the installed root `~/.local/share/ciel-runtime` (runtime `0.2.51-nightly.20261026-044702.d15822f`), not from the repo, with the same isolation, fake gateway and egress sentinel as above. [installed-model-env.json](evidence/installed-model-env.json) shows the installed `claude_environment.py` projecting `CLAUDE_CODE_SUBAGENT_MODEL=inherit` and `..._FORCE=1`.

Child request model at the gateway, Claude Code 2.1.310, direct data path:

| plain | Agent `model=haiku` | Agent `model=sonnet` | definition `model: haiku` | `--model fake-parent-b` | named teammate | new teammate after `/model fake-parent-b` |
|---|---|---|---|---|---|---|
| a | a | a | a | b | a | b |

- Evidence: [installed-fixed-1fe97e/result.json](evidence/installed-fixed-1fe97e/result.json) (all seven cases) and [installed-modelarg-e89346/result.json](evidence/installed-modelarg-e89346/result.json) (`--model` alone). Every child request carried the parent's model; for haiku and sonnet the gateway had served the parent an `Agent` call with that `model`.
- The `Agent` schema had no `model` property in any request, and no request carried `inherit` as its model.
- The five `-p` runs exited 0 with the parent's marker on stdout. Their `child_ok=false` only means the child's marker is not in the parent's printed answer; child requests are counted at the gateway.
- Console text ([screen-*.txt](evidence/installed-fixed-1fe97e/screen-latest.txt)) shows `Teammate @e2e-mate-09b41a47 finished` / `CHILD-OK-named`, then `/model fake-parent-b`, then `Teammate @e2e-mate-new-09b41a47 finished` / `CHILD-OK-named-switched`. PrintWindow captures [named.png](evidence/installed-fixed-1fe97e/named.png) and [named-switched.png](evidence/installed-fixed-1fe97e/named-switched.png) (1129×635) exist with recorded SHA-256 values; their pixels were not inspected visually in this session.
- Egress: no non-loopback TCP connection from the harness tree; the sentinel saw only blocked `CONNECT`s to `downloads.claude.ai:443` and `github.com:443`. All harness-owned PIDs were gone afterwards, the installed files were unchanged, and raw configs and workspaces were deleted ([sanitization.json](evidence/installed-fixed-1fe97e/sanitization.json)).
- Superseded attempts, kept for audit: `installed-fixed-779348` (the switched-teammate command never reached the console) and `installed-fixed-d5f266` (fake gateway connection resets; the console helper then gained explicit exit recording).

## Live router observation (cause not established)

- The two final runs saw the same live routers before and after.
- During the window of attempt `installed-fixed-d5f266` (started 2026-10-26 00:08:49 local), the router on 9464 changed from PID 54764 (`a7e28f9`) to PID 51556 (`d15822f`, the newly installed nightly). `%APPDATA%/ciel-runtime/workspace-router-ports.json` lists 9464 for the `c:\users\djlov\ciel-code` workspace with a launch lease held by PID 51556, expiring 00:13:57 local, so a non-isolated ciel-runtime launch happened in that workspace in that window.
- The harness runs with its own `HOME`, `APPDATA`, `CIEL_RUNTIME_CONFIG_DIR` and router port and only `taskkill`s PIDs it started. That does not prove what caused the 9464 relaunch, which remains unexplained.
- Port 9466 (PID 77836, `144bebc`) answered before and after attempt 779348 and was gone by attempt d5f266. Who owned it was not established.
