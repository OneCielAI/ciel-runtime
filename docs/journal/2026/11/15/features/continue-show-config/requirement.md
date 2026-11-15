---
type: feature-record
title: Show the settings menu with --continue --show_config
description: --continue and --resume skip the prelaunch settings menu; --show_config (or --show-config) opens it anyway and is consumed by the launcher.
timestamp: 2026-11-15T02:20:00Z
tags: [launcher, prelaunch, menu, continue, cli]
---

# Requirement

> 변경된 내용에 --continue 일때는 환경설정메뉴를 패스하는데 , --continue  --show_config  일때는 환경설정을보여게해줘

Recorded 2026-11-15.

# Behavior

| Command | Settings menu |
|---|---|
| `ciel-runtime --continue` | skipped (unchanged) |
| `ciel-runtime --continue --show_config` | shown first, then launch continues |
| `ciel-runtime --continue --show-config` | shown (same flag, dash spelling) |
| `ciel-runtime --continue --ca-menu` | shown (existing flag; same mechanism) |

`--show_config` sets the existing `force_menu` in `cli_dispatch`, which `run_prelaunch_menu` already honors ahead of the session-control skip. Ciel consumes the flag, so it is never passed to Claude, Codex or the other runtime CLIs. A non-TTY launch still never shows a menu.

# Verification

- Unit tests in `tests/test_prelaunch_wiring.py`:
  - `run_cli(["--continue", flag])` for both spellings launches Claude with `["--continue"]` (the flag is stripped) and `force_menu=True`.
  - `--continue` alone launches with `force_menu=False`.
- Launcher, runtime and usage suites: 154 + 47 tests OK.
- Real ConPTY runs of `ciel_runtime.py cli`. Each used a fresh temporary config dir, every inherited `CIEL_RUNTIME_*` removed and update checks off. The menu was closed with Esc, so no runtime CLI started ([driver](/evidence/show_config_conpty.py)):
  - [`--continue --show_config`](/evidence/showcfg-on-screen.txt): the `Ciel Runtime v0.2.51` menu rendered (provider, …, 실행, Work · SSE 설정, …) and Esc returned 0.
  - [`--continue --show-config`](/evidence/showcfg-dash-screen.txt) and [`--continue --ca-menu`](/evidence/showcfg-off-ca-screen.txt): the menu rendered.
  - [`--continue`](/evidence/showcfg-off-screen.txt): no menu; the launcher went straight to launch readiness. The empty temporary config has no provider key, so it stopped with "NVIDIA hosted requires an NVIDIA API key".
