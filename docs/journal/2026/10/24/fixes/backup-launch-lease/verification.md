---
type: Fix Verification
title: Menu session backup no longer fails on its own parent's router launch lease
description: A direct child of the live launch-lease holder (verified by parent PID and process identity) reuses the workspace's mapped router port without stealing or being refused by the lease, so menu item 18 backups succeed before a router is running.
tags: [session-backup, router-port, launch-lease, regina]
timestamp: 2026-10-25T02:40:00Z
---
# Problem (observed)

Regina (.25, `0.2.51-nightly.20261024-184918.4af30c7`) menu item 18 failed at 10-24 20:48 and 10-25 02:02 UTC with `RuntimeError: workspace router launch already reserved by another or unknown process` (`workspace_router_selection.py` line 253 → 324). At 02:02 the lease belonged to the live menu process and the workspace router started only at 02:03:27. Details: [/2026/10/24/diagnostics/regina-backup-lease/finding.md](/2026/10/24/diagnostics/regina-backup-lease/finding.md).

`runtime_paths` selects the router port at import. The menu holds the launch lease from start-up until a router runs. The backup child (`ciel_runtime.py cli backup create`) imports `runtime_paths`, finds no healthy router, and `_check_own_launch_lease` refuses it because the lease PID is alive and is not the child. The only exemption covered the managed router child.

# Change

`workspace_router_selection.py`:

- New `_lease_held_by_parent(record)`: true only when the lease PID equals `os.getppid()` and that PID's current creation identity equals the recorded `identity`, so a reused PID does not qualify.
- Automatic port path: after the health scan and before the availability/lease check, a child of the live holder returns the mapped port and writes nothing. This also covers the case where the parent is already binding the port.
- Explicit port path: such a child counts as inherited, like the managed router child. It skips the lease check and keeps the parent's record.
- Processes that are not the holder's child are still refused, and so are holders whose identity does not match. Dead-owner release (`0f5014d`) is unchanged.

# Verification

| Check | Result |
|---|---|
| `test_workspace_router_selection` (31 tests), Python 3.11 / 3.13 / 3.14 | OK ([py311](evidence/selection-tests-py311.txt), [py313](evidence/selection-tests-py313.txt), [py314](evidence/selection-tests-py314.txt)) |
| Same file against the unfixed code | the 3 new positive tests fail: 2 ERROR (lease RuntimeError), 1 FAIL ([log](evidence/selection-tests-unfixed-py311.txt)) |
| 15-file isolated regression set (selection, runtime paths, router lifecycle/startup, session backup ×5, workspace state, architecture contracts, …) | 472 run, OK, 42 pre-existing retired-MCP skips, on 3.11 and 3.14 ([py311](evidence/regression-py311.txt), [py314](evidence/regression-py314.txt)) |
| `ruff check .` (repo config, ruff 0.15.22 as in CI), `compileall`, `git diff --check` | pass |
| Real `run_backup(..., "menu")` from a lease-holding parent, isolated ([backup_probe.py](evidence/backup_probe.py)) | child import exit 0 with the parent's port; incremental snapshot `20261025T022041Z-144867` and full snapshot `20261025T022050Z-29831f` saved; lease still the parent's ([result](evidence/probe-after-fix/backup-probe-result.json)). The diagnosis probe on the unfixed code had child exit 1 and `ok: false`. |

The new tests include real processes:

- a real child interpreter of the lease-holding test process gets the same port and the registry is left byte-identical;
- with a live non-parent process holding the lease, the same child is still refused.

# Real menu E2E (Windows Terminal, `py -3 ciel_runtime.py cli --ca-menu`)

[e2e_backup_menu.py](evidence/e2e_backup_menu.py) and [e2e_backup_agent.py](evidence/e2e_backup_agent.py) run the real menu with:

- a scratch HOME, config and workspace;
- inherited `CIEL_RUNTIME_*`/`CLAUDE*`/`ANTHROPIC*`/`CODEX*`/`OPENAI*` removed;
- automatic port selection, as on Regina;
- egress pointed at a closed loopback port.

The agent presses only Down/Enter to reach 18 → "Back up this session now".

| Run | Code | Menu result | Capture |
|---|---|---|---|
| [run-unfixed](evidence/run-unfixed/result.json) | `1f1d48c` (current nightly) | `Backup not saved: … line 253 … line 324 … RuntimeError: workspace router launch already reserved by another or unknown process`, matching Regina's screen | [03-result.png](evidence/run-unfixed/shots/03-result.png) |
| [run-fixed](evidence/run-fixed/result.json) | this fix | `Saved snapshot 20261025T023612Z-a37a92 to local.`; item 18 shows `last … ok` | [03-result.png](evidence/run-fixed/shots/03-result.png) |

In both runs:

- the lease belonged to the menu `python.exe` (child of `py.exe`);
- no router process was started;
- live routers 9385/9464/9489/9505/9524 kept their PIDs.

A first fixed run produced 160×28 title-bar captures because its window was minimized. The rerun restores the window without activating it before each capture ([restore_title.ps1](evidence/restore_title.ps1)).

# Limits

- The real-menu E2E ran on Windows. On Linux (Regina) the identity comes from `/proc/<pid>/stat` field 22. The selection unit tests cover the logic, but the Linux path was not executed in this verification.
- Only direct children qualify. A helper started through an intermediate process (a grandchild of the menu) is still refused while no router runs. The backup `create` and `list` subprocesses are direct children (`session_backup_service.py` `Popen` with `sys.executable`).
