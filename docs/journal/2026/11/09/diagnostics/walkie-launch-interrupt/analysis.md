---
type: Incident Analysis
title: Walkie Claude relaunch stopped at the readiness check
description: The codex SyntaxWarning on a Claude launch is a one-time compile warning; the launch itself ended with KeyboardInterrupt.
timestamp: 2026-11-09T17:45:00Z
tags: [walkie, launch, python-3.14, diagnostics]
---

# Observation

`G:\ciel-Walkie> ciel-runtime --continue` on beta snapshot `503be09` printed:

```
...\ciel_runtime_support\codex_app_server_session.py:417: SyntaxWarning: 'return' in a 'finally' block
  return _RemoteRun(1)
Traceback (most recent call last):
  ...
  File "...\ciel_runtime_support\runtime_launch.py", line 572, in run_claude
    blockers = launch_readiness_errors(cfg)
```

The pasted traceback ends at that frame; the exception line was not in the paste.

# Findings

| Item | Evidence |
|---|---|
| The warning comes from compiling a module, not from running Codex | `codex_app_server_session.py` ends its `finally:` block with `return _RemoteRun(1)`. Python 3.14 reports this at compile time (PEP 765). `ciel_runtime.py:134` imports the module on every launch, Claude included. |
| It printed once, when the `.pyc` was first written | `__pycache__\codex_app_server_session.cpython-314.pyc` is timestamped 11:29:02 local, the same second as the launch. Later clean imports of the same snapshot printed nothing on stderr. |
| The launch ended with KeyboardInterrupt | Walkie router log `9505-aae7db361c7f\router.log`: `11:29:03 launch_phase_start runtime=claude phase=remote_instructions` … `11:29:10 launch_lease_release runtime=claude cause=KeyboardInterrupt result=released`. |
| The readiness check has no blocker now | Clean environment (every `CIEL_RUNTIME_*`, `CLAUDE*`, `ANTHROPIC*`, `CODEX*`, `OPENAI*` unset), cwd `G:\ciel-Walkie`, snapshot `503be09`: provider `cielairouter`, Claude launch enabled, `launch_readiness_errors(cfg) == []`, about 1.1 s per run including a 0.65 s import (two runs). |
| Previous Walkie session | Ended normally at 11:28:54 (`child_spawn ... elapsed_ms=7288288 result=exit_0`). |
| Network calls inside the readiness check | With `urllib.request.urlopen` timed in a clean run, the check makes two calls: the Walkie router `/health` (0.15 s timeout) and an authenticated `GET https://ciel-router-01.ezonebot.com/v1/models` (10 s timeout, then one 30 s retry on timeout; `providers/cielairouter.py:233-239`). Nothing is printed while it waits. |
| Catalog GET duration | Five clean runs now: 0.15–1.69 s. The adapter comment records 15.9–25.4 s on 2026-10-27. The 12:00 relaunch spent about 2 s from `self_update_check` end to `router_start`, which covers this check. The gateway is remote (5.252.53.183), so there is no local record of the 11:29 response time. |
| Console Ctrl+C senders in ciel-runtime | `GenerateConsoleCtrlEvent` / `CTRL_C_EVENT`: zero matches in the 13c6f4a snapshot (previous session) and in the 503be09 snapshot. `windows_console_guard.py` only restores console modes. |
| Relaunch | Walkie was relaunched at 12:00:30 on 503be09 and is running (router 9505, client file `53236.json`). |

# Not confirmed

- Whether the KeyboardInterrupt came from a Ctrl+C keypress. No ciel-runtime code sends one, and keystrokes are not logged.
- How long the catalog GET took at 11:29. By the code, it is the only call in the check that can wait 7 s, but its duration then was not recorded.

# Side effect of an earlier, invalid reproduction

An earlier reproduction ran `ciel-runtime --continue` from this agent's own shell, which still carried the live session's `CIEL_RUNTIME_*` variables. It failed on the router 9524 version check and does not represent the Walkie launch. Before failing, the inherited `CIEL_RUNTIME_PROVIDER` re-selected `cielairouter` for workspace `bd5f642b3583`, which was already its provider (the adapter's selection updates are empty). It also cleared the gateway model cache (`model-list-cache.json` and `model-registry.json` were rewritten at 11:30:37). The global `current_provider` is still `nvidia-hosted`. The Walkie workspace config was not written by it: its last write is 10:25:29, before the launch.
