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

# The crash itself (dump analysis)

There were 5 `python314.dll` access violations in 14 days (Windows event 1000):

- 11-15 07:20 and 00:22, both the Walkie router 9505 at offset `0x20885`;
- 11-10 21:18 at `0x26177`;
- 11-02 at `0xc19a4`, twice.

WER kept a minidump of the 07:20 crash: `%LOCALAPPDATA%\CrashDumps\python.exe.85640.dmp`. It was analysed with cdb (Windows Kits 10) and the official Python 3.14.2 symbols (`python.org/ftp/python/3.14.2/amd64/core_pdb.msi`):

- Crash site: `python314!_PyEval_EvalFrameDefault+0x17a5`, at `generated_cases.c.h:5861`.
  - That line is `TARGET(FOR_ITER_LIST)` → `_ITER_CHECK_LIST`: `Py_TYPE(iter_o) != &PyListIter_Type`.
  - Python's own source at tag v3.14.2 was checked.
- The iterator stack slot was NULL: `cmp qword ptr [rdx+8],rcx` with `rdx=0`.
  - Python code cannot put NULL there. Only a CPython defect or native memory corruption can.
- Which Python function was running is not known: the frame (`0x1cb3ef34040`) lies in heap memory, which the minidump does not hold.
- Every import outside tests is stdlib (AST scan: 0 third-party modules), so no third-party native extension runs in the router.
- Installed: Python 3.14.2 (2025-12-05). Latest 3.14: 3.14.10. Searches of the CPython issues and the 3.14 changelog found no fix that matches; whether a later 3.14 release fixes this is not established.

# Follow-up (user decisions 2026-11-15)

**Fault record:** `ciel_runtime_support/fault_log.py`. The router enables `faulthandler` with all threads, writing to `router-fault.log` beside `router.pid`.

- Each start appends a header line.
- The next start prints `router_previous_fault <fault> (run started …); innermost: <3 frames>` into router.log.
- Wired only in the production router composition. The `RouterServerEffects.enable_fault_log` default is a no-op, so tests never enable faulthandler.
- Caveat: an SEH exception that native code later handles (for example inside a ctypes call) is also written, so a record is a lead rather than proof of death.
- `tests/test_fault_log.py`: 3 passed on Python 3.11 and on 3.14.10. One test is a real `ctypes.string_at(0)` access violation in a child process: the record has the `poll_board` frame, and the next start reports it once.

**Python upgrade:** this machine got 3.14.10 alongside 3.14.2, at `%LOCALAPPDATA%\Python\ciel-pythoncore-3.14.10-64` (pymanager 26.4, verified index signature, `--target`).

- New Ciel launches use it through the user variable `CIEL_RUNTIME_PYTHON`, which `npm-bin/run-ciel-runtime.js` reads before `py -3`.
- The in-place `py install --update 3.14` was not used, because every live router runs `pythoncore-3.14-64\python.exe` with `python314.dll` loaded.
