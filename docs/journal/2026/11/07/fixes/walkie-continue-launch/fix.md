---
type: fix
title: Walkie continue launch
description: "ciel-runtime --continue in G:/ciel-Walkie failed on an expired backup lease, a wrong-cwd router probe, and an unwanted settings menu; the installed beta now reaches the Claude prompt."
tags: [launch, router-lease, prelaunch-menu, walkie]
timestamp: 2026-11-07
---

# Observed failures

1. `RuntimeError: workspace router launch already reserved by another or unknown process (pid 74380)`.
   PID 74380 is a live `ciel_runtime.py cli backup create --cwd G:\ciel-Walkie` helper. Its launch lease had already expired, but the old check refused any live holder whose identity matched. Fixed in `3e6f541` (expired leases never block).
2. The first probe after `3e6f541` ended with `router did not start (pid=107220 exit_code=1)`. The router log shows the child exited with `port 9505 is reserved by workspace g:\ciel-walkie; choose a different CIEL_RUNTIME_ROUTER_PORT for c:\users\djlov\ciel-runtime\.local\tmp\work-parallel`. That probe was launched from this worktree with `CIEL_RUNTIME_LAUNCH_CWD=G:/ciel-Walkie`; the managed router child re-derives its launch cwd from `Path.cwd()`. This was a probe-construction error, not the user's path. A real console probe started inside `G:\ciel-Walkie` did not reproduce it.
3. The real console probe (`.local/walkie_continue_probe.py`, User-scope environment only) stopped at the prelaunch settings menu after 171 s: `run_prelaunch_menu` skipped the menu only for `-p/--print`, so `--continue` waited for keyboard input.

# Fix

- `713475f`: `run_prelaunch_menu` also skips when the Claude passthrough already selects a session (`-c`, `--continue`, `-r`, `--resume`, `--session-id`, `--fork-session`, `--from-pr`, and their `--flag=value` forms), reusing `runtime_session_control_present`. `--ca-menu` still forces the menu.
- Regression tests: `tests/test_prelaunch_wiring.py` (continue skips, `--resume=` skips, forced menu still opens).
- `2a40943` first added a local helper; CI failed the `ciel_runtime.py` 4980-line architecture budget (4994). `713475f` reuses the existing helper and is exactly 4980 lines.

# Verification

- Local: prelaunch, headless-update, architecture-budget and facade-binding tests passed (27); workspace router selection tests passed (52).
- CI for `713475f`: unit, router, channel, runtime, python-minimum and quality all succeeded.
- Beta `0.2.51-beta.20261107-170304.713475f`: registry SHA-512 and 506 source files matched git head `713475fac38edca239345da005ff7b2a16d24886`; `latest=0.2.51` and `nightly=0.2.51-nightly.20261106-020333.c00c8b6` unchanged.
- Installed as immutable snapshot `C:/Users/djlov/.local/share/ciel-runtime-713475fac38edca239345da005ff7b2a16d24886` and global npm package; User `CIEL_RUNTIME_HOME` points to the snapshot; 560 package files matched; six isolated CLI commands passed.
- Real console in `G:\ciel-Walkie`, command `ciel-runtime --continue`: router spawned in 1.5 s, Claude Code reached its prompt in 108.7 s (`bypass permissions on` footer), `/exit` returned exit 0, the owned router was stopped by `router_kill_guarantee reason=claude_exit`. The user then launched their own session from the same snapshot at 11:12 and 11:14; both spawned the router normally.
- Screenshots and full screen text stay under `.local/walkie-continue-20261107-110939/` (they contain private Walkie Work items and are not committed). Before-fix menu stop: `.local/walkie-continue-20261107-103728/menu.png`.

# Open observation (not fixed here)

In the user's 11:12 session one new SSE receipt failed twice with side category `processing_error` before any side model request reached the router; the mod records no error text for that category, so the cause is not yet identified. 33 older receipts from earlier snapshots remain quarantined with `turn_error`.

# Evidence

- [Router launch lines](evidence/router-launch-lines.txt)
