---
type: fix-record
title: A crashed router restarts inside Claude Code's MCP reconnect window
description: G:\ciel-Walkie lost its router MCP twice on 2026-11-15 because the launcher restarted a crashed router after 84-85 s; the down confirmation counted probes instead of time.
timestamp: 2026-11-15T15:30:00Z
tags: [router, supervisor, crash, mcp, windows]
---

# Measured failure (G:\ciel-Walkie, router 9505)

| Event | 00:22 crash | 07:20 crash |
| --- | --- | --- |
| Router python.exe crash (Windows event 1000)<br>`python314.dll` access violation `0xc0000005`, offset `0x20885` | 00:22:26 | 07:20:20 |
| Launcher `router_lifetime_restart reason=router_down_active_client` | 00:23:51 (85 s) | 07:21:44 (84 s) |
| Router back up | 00:23:58 | 07:21:52 |
| `ciel-runtime-router` MCP reconnected | 00:29:24, by hand | never (0 `/ca/mcp` requests since) |

At 13:20:45Z the main turn's request failed with `API Error: Connection refused`. The transcript recorded "The following MCP servers have disconnected: ciel-runtime-router". Work and the side actor stopped from then on, because every side event needs the router MCP tools.

Claude Code docs, "Automatic reconnection": a dropped remote server is retried "up to five times with exponential backoff, starting at a one-second delay and doubling it each time". After that it is marked failed until `/mcp`. That is a window of about 31 s, so an 84 s restart always loses the MCP.

# Root cause of the delay

`RouterClientSupervisor.ensure_running` confirmed "down" with `ceil(15 s / 0.5 s) = 30` counted probes. On this machine a refused loopback connect takes 2.04 s (measured), so each probe cost about 2.6 s:

- 0.5 s wait;
- 2.04 s refused `/health`;
- 0.06 s `netstat`.

30 probes ≈ 78 s, plus the 5 s supervision interval, gives the observed 84 s.

# Change

- **Wall-clock window:** the confirmation window is now `CIEL_RUNTIME_ROUTER_DOWN_CONFIRM_SECONDS` (default 15) of wall-clock time, with at least two probes.
- **Dead-process fast path:** new port `process_alive` reads the managed `router.pid` and checks it with `pid_is_running`. When that process is gone, the router is restarted at once (`reason=router_process_exited`). A busy worker cannot hold a listener for a process that no longer exists.
  - Unknown state (no pid record) keeps the window.
  - A live pid keeps the window too, for example while Windows Error Reporting still holds a crashing process.

# Evidence

- `tests/test_router_client_lifecycle.py`: 13 passed. New cases:
  - an exited process restarts in under 1 s with a 60 s window configured;
  - the window is wall-clock time (3 s window with 0.9 s probes ends in 3–4.9 s, not 5.4 s or more);
  - an unknown process state still keeps a transient health miss.
- Real-process check: a real `http.server` listener on a free port, killed with `taskkill /F`, checked with the real refused-connect, pid and netstat probes. Results:
  - restart decision **2.91 s** after the kill;
  - with unknown process state, 17.58 s (15 s window).

  The total gap stays under the ~31 s reconnect window: at most a 5 s interval, plus about 3 s, plus a 1.7 s spawn.

# Not fixed

The `python314.dll` access violation itself (offsets `0x20885` twice on 2026-11-15, `0x26177` on 2026-11-10) is not diagnosed. No crash dump exists, so its cause is unknown.
