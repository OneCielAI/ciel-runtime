---
type: diagnostic
title: Beta startup stall and side MCP parity
description: Measured startup delays, the menu-wait explanation for the long-lived launcher, and the actual side MCP inheritance contract.
tags: [startup, performance, mcp, side-intake, beta]
timestamp: 2026-11-06
---

# Observed startup

`G:/ciel-walkie` ran `ciel-runtime --continue` from beta a5794b4. Python PID408 remained alive for at least1653seconds with2.234375CPU seconds, no child process and no TCP connection. The process later exited without being killed by this investigation. Its router log reached remote instruction synchronization for Claude and Codex, then recorded no later launch phase. That pattern matches the prelaunch menu waiting for a key, as the owned reproduction below shows.

Two independent deterministic delays were measured:

- `select_workspace_router_port` performs 50 serial health probes. I measured the actual 9464–9513 range on this machine at 7.54 seconds. Every closed loopback port used the full 150 ms timeout, while the live routers 9487 and 9489 answered in about 5 ms. An earlier exploration reported a 0.79 second scan, which this direct measurement contradicts. An exclusive-bind check over the same 50 ports took 0.0044 seconds and found exactly the two live listeners.
- Duplicate-install diagnostics runs two `.cmd --version` probes. Each one recursively starts the full runtime in about 9 seconds, about 18 seconds in total.

# Owned reproduction

[owned-repro](evidence/owned-repro.json) ran the installed beta `~/.local/bin/ciel-runtime.cmd --continue` in an owned ConPTY with an isolated config directory. It dumped every Python thread every 10 seconds and killed only the owned process tree afterwards.

- **Default automatic port.** The terminal stayed blank for 29.5 seconds. Two recursive `--version` Python children ran from 8.9 to 26.2 seconds.
- **Explicit port with the walkie-derived config.** Outward receivers and bridges were removed. The menu appeared at 9.1 seconds and no probe child was observed.
- **After the menu rendered.** In both runs the launcher's main thread sat in `prelaunch_terminal.py:226 read_menu_key`. It had no child, no TCP connection and flat CPU. That matches the external signature recorded for PID 408. Because PID 408 exited before a frame could be captured, its wait point is consistent with this but not proven.

An isolated `--version` run with an explicit port and diagnostics skipped takes 1.3 seconds. The roughly 29.5-second blank startup therefore breaks down as follows:

- the parent's import and its own scan, about 8.9 seconds;
- two recursive `--version` children, each paying about 1.3 seconds of startup plus its own 7.5-second scan;
- the update check and menu render, about 3 seconds.

`ciel-runtime --continue` opening the prelaunch menu is long-standing behavior, not a regression:

- **Source.** The skip predicate in `run_prelaunch_menu` has recognized only explicit skip, print mode, `CIEL_RUNTIME_SKIP_MENU` and non-TTY since the initial commit.
- **Earlier observation.** On Regina (2026-10-25) a `--continue` menu process held the launch for 85 seconds before the router started.
- **Timing of PID 408.** It started at 15:50:07 local (UTC-6). Its remote sync was logged at 15:50:16, and the user's screenshot message followed at 15:50:46. That puts the screenshot inside the measured blank window of about 29.5 seconds.

The later 27-minute lifetime matches the menu waiting for a key: the main thread blocks in `read_menu_key` (Windows `msvcrt.getwch()`), and the router and Claude start only after a launch choice.

Earlier walkie launches show gaps of 55 seconds to 15 minutes between remote sync and the next launch log line while the menu waited for input. A quiet log after `remote_instructions_unchanged` is therefore normal menu waiting, not by itself evidence of a hang.

# Side MCP contract

The primary side actor reads the parent session's connected MCP tools through `$.tool.list()`, passes the MCP tool list and every derived MCP server to its registered actor, and lets non-Work MCP calls continue through the native permission path. Work status reads and the bound `work_side_resolve` decision are allowed; direct Work mutation tools remain denied because side queue ordering is not execution approval. Read-only comparison shadows intentionally receive no tools.

`External reply MCP bindings` is not MCP inheritance. It is an advanced outbound route override that fixes an exact external source/receiver to an operator-owned MCP server/tool/argument template. A web-chat/session route with a trusted thread/reply token needs no extra MCP binding. The label needs to say this directly.

# Evidence

See [observations](evidence/observations.json).
