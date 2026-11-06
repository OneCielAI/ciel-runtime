---
type: verification
title: Startup latency fix and side MCP parity verification
description: Before/after time-to-menu, phase trace, lease lifecycle, side MCP parity on two native MCP servers, and the renamed reply-route row.
tags: [startup, performance, mcp, side-intake, beta]
timestamp: 2026-11-06
---

# Changes

- **Router port selection** (`workspace_router_selection.py`)
  - `probe_router_health` checks for an exclusive bind first. A port that can be bound has no listener, so the probe returns there without sending HTTP.
  - Bound ports are probed 4 at a time.
  - Selection is unchanged in substance: it keeps the complete-scan, deterministic-order, fail-closed and lease/handoff rules. A probe exception counts as occupied.
- **Install diagnostics** (`install_diagnostics.py`)
  - Known npm and `~/.local` wrappers are resolved from files. They use the same home precedence as each wrapper:
    - `.cmd`: `CIEL_RUNTIME_HOME_OVERRIDE`, then the HKCU `REG_SZ` home if it contains `ciel_runtime.py`, then env `CIEL_RUNTIME_HOME`, then the default.
    - `.ps1`: the same order, with `REG_SZ` or `REG_EXPAND_SZ` accepted.
    - Extensionless sh: `CIEL_RUNTIME_HOME`, then the default.
  - Only unknown launchers keep the bounded `--version` fallback. Rows that resolve to the same install are merged.
  - The warning keys on distinct versions.
- **Launch phase trace** (`runtime_launch.py`, `remote_*.py`, `runtime_paths.py`)
  - Secret-free `launch_phase_start` / `launch_phase_end` lines carry elapsed time and result for remote assets, install diagnostics, the self and CLI update checks, router start, model cache, MCP projection and the child boundary.
  - The import-time port selection is replayed once per launch as an end line, because it runs before any logger exists.
  - A console line is printed only for phases of 1 second or more.
- **Lease lifecycle**
  - On a failure before any child starts, the launch releases only its own lease (matching PID and birth token).
  - Unknown or foreign identities are left alone.
  - A second launch of the same workspace fails fast and names the holder PID. Nothing is stopped.
- **Side MCP parity** (`side-intake.js`, `register.js`)
  - At each side event the actor re-reads the main session's MCP tools and computes a digest.
  - If the digest changed, a fresh actor is registered with the new tools. A running actor is not cancelled.
  - The parity counts and digest are shown in the pane and in `/btw_with_mcp status`.
- **Reply-route row**
  - It is renamed `Advanced external-event reply route override (JSON)`.
  - The description says it is not MCP inheritance. Correlated web chat replies automatically, while ordinary external SSE/CloudEvent replies need this override.

# Before / after time to first menu frame

The harness ran `~/.local/bin/ciel-runtime.cmd --continue` in an owned ConPTY with an isolated config and the automatic port. It polled the screen for the menu footer every 100 ms.

| Build | Runs (s) | Recursive `--version` children |
|---|---|---|
| Installed beta a5794b4 | 26.50, 26.50, 26.64 | yes |
| Patched tree | 3.03, 4.50, 4.52 | 0 |

- **Patched tree** was launched through the same wrapper with `CIEL_RUNTIME_HOME_OVERRIDE`. A source tree performs no npm self-update check, so the installed-package result after publish is recorded separately.
- **Phase lines** from the traced run, at INFO level with an owned state directory:

| Phase | Elapsed | Result |
|---|---|---|
| `runtime_paths.router_port_select` | 50 ms | selected |
| `install_diagnostics` | 6 ms | |
| `remote_*` | 1–2 ms | disabled |
| `self_update_check` | 0 ms | |

- **Real 9464–9513 scan, read-only:** 7.551 s serial on HEAD, 0.0122 s with the new probe and pool. Both found the same live routers, 9487 and 9489.
- **Real wrappers, read-only:** the AppData npm wrapper and `~/.local/bin/ciel-runtime.cmd` both resolved to a5794b4. No subprocess was started, no warning was printed, and resolution took 0.0137 s.

Evidence: [before](evidence/benchmark-before.json), [after](evidence/benchmark-after-source.json).

# Side MCP parity (native)

[side-mcp-parity-native](evidence/side-mcp-parity-native.json) used native Claude Code 2.1.310 with two owned MCP servers.

- **Main session:** 32 MCP tools on `ciel-runtime-router` and `owned-mail`.
- **Side agent:** received every non-Work tool from main, plus the read-only `work_status` and `work_parallel_status`. While main was busy it called `owned-mail__read_message`.
- **Outcome:**
  - the whole 25-node board was sent
  - pending 0
  - resolved before main ended
  - the reply was submitted
- **Pane:** shows `Primary side MCP parity: 32 tools · 2 servers · digest 1d262bf9b7aa81bf`. See the [screenshot](evidence/screens/side-mcp-parity-native.png).

# Reply-route row

The real prelaunch menu, driven to Work & SSE settings, shows `Advanced external-event reply route override (JSON): []` with its description. See the [screenshot](evidence/screens/reply-route-label.png).

# Gates

- **Isolated groups:** router 1537 OK, channel 604 OK (80 skipped), runtime 458 OK (19 skipped).
- **Unit group:** the first run had 1 failure out of 3352, in `test_event_receiver_recovery` (event-1 admitted twice). That area is untouched by this change. The test passed 6 of 6 times in isolation, and the full unit group was rerun.
- **Focused set (18 files):** 451 OK.
- **Other checks:** ruff, `node --check` on both hooks, and `git diff --check` all pass.
- **Live routers:** 9487, 9489 and 9524 kept PIDs 95740, 34792 and 2664.

# Known limits

- **Pre-router log lines on a new workspace.** `RouterFileLogger` creates only the config directory. On a workspace whose router-instance directory does not exist yet, lines written before the router starts are dropped. This predates the change; existing workspaces such as walkie have the directory.
- **The menu on `--continue`.** `ciel-runtime --continue` still opens the prelaunch menu, as it has since the initial commit. The PID 408 lifetime matches the menu waiting for a key.
