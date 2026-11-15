---
type: change-record
title: OpenCode Zen becomes OpenCode Console; both OpenCode plans move to /inference with the live model lists
description: ciel-runtime keeps two OpenCode providers, renamed and re-pointed to the official 2026-11-15 layout, with the Go default model changed to kimi-k3 and both offline model lists rebuilt from the live catalogs.
timestamp: 2026-11-15T19:55:00Z
tags: [providers, opencode, console, go, catalog, migration]
---

# User decisions (2026-11-15)

- Keep two providers and follow the new layout ("둘로 유지하고 새방식으로 맞춰").
- The new OpenCode Go default model is `kimi-k3`.
- Scope is the ciel-runtime provider list and model lists. Background-model fields are out of scope. In ciel-runtime, `haiku_model` is read only for the zai providers, and non-anthropic subagents are forced to `inherit` (`claude_environment.py`).

# Official sources

## Console (formerly Zen) — https://opencode.ai/docs/zen/

- The page title is now "Models" and it sits under Console docs.
- The service is "OpenCode Console", pay-as-you-go: credits are added and each request is charged.
- Endpoints are `https://opencode.ai/inference/<api>/v1/...`.
- Free models are for the OpenCode client only. Other clients should use paid models or Go.

## Go — https://opencode.ai/docs/go/

- Plans: Go $10/month, Go Plus $40/month, Go Teams $20 per member per month.
- Endpoints are `https://opencode.ai/inference/go/<api>/v1/...`.
- A client must send its own User-Agent and a stable `x-opencode-session` per conversation. Claude Code and Codex native session headers are recognised.

## Live catalogs

[evidence/console-models.json](evidence/console-models.json) and [evidence/go-models.json](evidence/go-models.json):

- **Console:** 86 chat models.
- **Go:** 30 models.
- Each model has exactly one `api` entry, which gives its native protocol: `messages`, `responses`, `chat-completions` or `generate-content`.
- **Old and new addresses serve the same list:** the old addresses `/zen/v1/models` and `/zen/go/v1/models` returned exactly the same byte counts (104,821 and 35,795) as `/inference/...`.

# Measured (Go key from this machine, never printed)

[evidence/go-path-probe.txt](evidence/go-path-probe.txt):

- **All path shapes work:** these all returned 200 "pong":
  - old `/zen/go/v1/messages` and `/zen/go/v1/chat/completions`;
  - new `/inference/go/v1/messages`, `/v1/chat/completions` and `/v1/responses`;
  - the documented per-protocol paths `/inference/go/anthropic/v1/messages`, `/openai/v1/chat/completions` and `/openai/v1/responses`.
- **The old default model is gone:** `qwen3.6-plus` returns 400 "Model qwen3.6-plus isn't included in OpenCode Go". It was the old Go default and this machine's stored Go model.
- **The Go page is out of date:** `mimo-v2.5` and `mimo-v2.5-pro`, which the Go page still lists, return 410 ModelDeprecated ("Use mimo-v2.6-flash / mimo-v2.6-pro instead"). So the live catalog, not the document, is the source of the model list.

# Change

- **Names and addresses:**
  - `opencode` is labelled "OpenCode Console" in the descriptor, the API-key name and the launch error. The alias `opencode-console` is added; `zen` and `opencode-zen` are kept.
  - Default base URLs: `https://opencode.ai/inference` (Console) and `https://opencode.ai/inference/go` (Go).
- **Model lists:** `providers/opencode_catalog.py` is rebuilt from the two live catalogs, with exact ids and native protocols. Changes against the previous snapshot:
  - **Console removed:**
    - deepseek-v4-flash, deepseek-v4-flash-vision-exp;
    - gemini-3-flash, gemini-3.1-pro;
    - ling-3.0-flash-fin-free, mimo-v2.5-free, muse-spark-1.3-contributor-free, nemotron-3-ultra-free;
    - qwen3.7-max, qwen3.7-plus, union-alpha.
  - **Console added:**
    - Claude: claude-fable-5-2, claude-opus-5-5, claude-sonnet-5-5, claude-haiku-5-5;
    - GPT: gpt-6-sol, gpt-6.1-sol, gpt-6-luna;
    - Grok: grok-4.7;
    - Gemini: gemini-4-argon, gemini-4-carbon, gemini-4-hydrogen;
    - Qwen: qwen3.8-max, qwen3.8-flash;
    - DeepSeek: deepseek-v4.1-flash;
    - Muse: muse-ember-1.0, muse-wonder-1.0;
    - Mistral: mistral-large-4;
    - free models: beam-free, flint-40b-free, iceman-free, inkling-2-small-free, longcat-2.5-free, glm-5.3-flash-free, deepseek-v4.1-flash-free, minimax-m3.1-flash-free, mimo-v2.6-flash-free, nemotron-3.5-super-vl-free.
  - **Go removed:**
    - deepseek-v4-flash, deepseek-v4-flash-vision-exp;
    - glm-5.1, grok-4.6, kimi-k2.6;
    - mimo-v2.5, mimo-v2.5-pro;
    - minimax-m2.5, minimax-m2.7;
    - qwen3.6-plus, qwen3.7-max, union-alpha.
  - **Go added:**
    - claude-haiku-5-5, gemini-4-argon, gpt-6-luna, grok-4.7;
    - hy4, kimi-k3.1-pro, mimo-v2.6-flash, mimo-v2.6-pro, minimax-m3.1-flash, step-5-preview;
    - free models: beam-free, iceman-contributor-free, longcat-2.5-free.
  - **Kept models:** no protocol changed for any model that stayed.
  - **Ox-alpha models:** the managed `x-preview-f-free` and `ox-alpha-free` models and their route overrides are no longer defaults; neither live catalog lists them.
- **Go defaults:** the default model is `kimi-k3`, and the offline choices are the Go catalog.
- **Migration `opencode_console_go_inference_20261115`** applies to both providers:
  - a stored base URL equal to the old default moves to the new default; a custom URL is kept;
  - models that left the catalog are removed from `custom_models` and `model_endpoints`, and new catalog models are appended; the user's own models are kept;
  - a current model that left the catalog becomes the plan default: `claude-sonnet-4-6` for Console, `kimi-k3` for Go.
- **Docs:** README, `docs/Providers.md` (provider table plus a new OpenCode Console/Go section) and `docs/Module-Map.md`.

# Verification

- **Windows, affected suites:** 225 passed.
- **Windows, sweep of every test file that touches providers, migrations or the default config:** 574 passed, 42 skipped, 1 failed. The failure is `test_actual_menu_selection_saves_compact_toggle`:
  - it fails the same way on unchanged HEAD under pytest capture ("redirected stdin is pseudofile");
  - it passes with `-s`;
  - it is unrelated to this change.
- **Linux** (Docker `python:3.12-bookworm`, opencode, catalog, contract-matrix, key-rotation and update-check suites): 180 passed.
- **Real E2E** ([evidence/e2e-old-config.txt](evidence/e2e-old-config.txt)): an isolated config in the pre-change shape (old Zen URLs, Go model `qwen3.6-plus`, `ox-alpha-free` route) with the real Go key, run through an owned router on port 19781.
  - **Go**, `ciel-runtime test 180 smoke`:
    - upstream `https://opencode.ai/inference/go`, model `ciel-runtime-opencode-go-kimi-k3`;
    - text OK and tool_use OK; Compatibility OK.
  - **Stored config afterwards:**
    - Go: `https://opencode.ai/inference/go`, `kimi-k3`, 30 choices, no `qwen3.6-plus`, routes `{}`;
    - Console: `https://opencode.ai/inference`, `claude-sonnet-4-6`;
    - migration marker set.
  - **Console:**
    - `ciel-runtime provider opencode` prints "Provider set to opencode (OpenCode Console)";
    - both endpoint probes reach `https://opencode.ai/inference/v1/...`;
    - the free-model request answers 401 "Invalid credential", because this machine has no Console key. A keyed Console request is therefore **not verified**.

# Found, not changed

`ciel-runtime provider list`, documented in `cli_usage.py`, answers "Unknown provider: list".
