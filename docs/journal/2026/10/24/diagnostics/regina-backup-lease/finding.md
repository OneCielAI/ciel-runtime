---
type: Diagnostic Finding
title: Menu session backup fails on the router launch lease
description: Regina (node96, 0.2.51-nightly 4af30c7) menu item 18 reports "workspace router launch already reserved by another or unknown process"; the backup child is blocked by the lease its own parent menu holds.
tags: [session-backup, router-port, launch-lease, regina]
timestamp: 2026-10-24T21:10:00Z
---
# Report

User screenshot, Regina Ai agent 3 on node96 (Linux native), Ciel Runtime `0.2.51-nightly.20261024-184918.4af30c7`, prelaunch menu:

- `18. Session backup [incremental · schedule off · to local · last 10-24 20:48 failed]`
- `Backup not saved: ... workspace_router_selection.py, line 253, in select_workspace_router_port _check_own_launch_lease(own_record) ... line 324, in _check_own_launch_lease raise RuntimeError("workspace router launch already reserved by another or unknown process")`

Regina's machine was not accessed; the diagnosis rests on the reproduction below.

# Mechanism (code facts at 4af30c7)

1. `runtime_paths.py:136` computes `ROUTER_PORT = select_workspace_router_port(...)` at **import time**, so every process that imports the runtime runs port selection against `workspace-router-ports.json`.
2. With no router alive for the workspace (the prelaunch menu), the menu process records a `launch_lease` for its own PID (`_launch_record`, `workspace_router_selection.py:105-116`, written at 255/286).
3. Menu backup calls `session_backup_service.run_backup`, which starts a child `ciel_runtime.py cli backup create` with the menu's environment and cwd (`session_backup_service.py:129,185-189`), so the child resolves the same workspace.
4. The child imports `runtime_paths`, finds the mapped port with no healthy router (`:252`) and calls `_check_own_launch_lease` (`:253`). The lease PID is the parent menu: alive and not the child, so `:324` raises. `expires_at` is only shape-checked, never compared to the clock, so the block lasts as long as the menu process lives.
5. The only exemption for a child is the explicit-port, `CIEL_RUNTIME_MANAGED_ROUTER=1` router child (`:395-415`). An automatic-port backup child has none.
6. When a router for the workspace answers `/health`, selection returns at `:245-250` before the lease check (code reading; not run).

The lease check was introduced in `09017fb` (2026-10-21, "preserve native compaction and expose guarded delivery recovery").

# Reproduction

[`evidence/lease_probe.py`](evidence/lease_probe.py) uses a temporary HOME/APPDATA/config/workspace with all inherited `CIEL_RUNTIME_*`/`CLAUDE*`/`ANTHROPIC*`/`CODEX*`/`OPENAI*` removed. Its only network use is read-only `GET /health` on localhost ports.

The `workspace_router_selection.py`, `runtime_paths.py` and `session_backup_service.py` it ran are identical to 4af30c7. Result ([`evidence/probe-result.json`](evidence/probe-result.json)):

| Step | Result |
|---|---|
| Parent imports `runtime_paths` | port 9463, lease PID = parent PID 67436 |
| Fresh child interpreter imports `runtime_paths` | exit 1, `RuntimeError: workspace router launch already reserved by another or unknown process` |
| Real menu path `run_backup(..., "menu")` | `ok: false`, traceback ending at `line 253` / `line 324`, the same as Regina's screen |

# Status

- ✅ Cause reproduced on the same code with the same traceback.
- ✅ Affected: any automatic-port child process the menu spawns while no router serves the workspace. The menu backup `create` path is reproduced. The `backup list` subprocesses (`session_backup_service.py:380`, `session_backup_menu.py:225`) share the import path; they were not run.
- 🟡 Whether Regina's menu had a router running for that workspace at 20:48 is not observed; the screen is the prelaunch menu.
- No code, config or remote state was changed.

# Second report (10-25 02:02 on Regina)

The second user screenshot shows the same menu on node96, `Full backup now` selected, `last 10-25 02:02 failed`, and the same traceback (`line 253` → `line 324`).

- ✅ The full-backup item takes the same path: `_backup_now(..., full=True)` → `run_backup(config_dir, cwd, "menu", ..., full=full)` (`session_backup_menu.py:480,489`), which starts the same `cli backup create` child.
- ✅ No version contains a change for this case. Across all refs, the newest commit touching `workspace_router_selection.py`, `runtime_paths.py` or `session_backup_service.py` is `0f5014d` (2026-10-21, "release verified-dead launch leases"). It is an ancestor of the published nightly `4af30c7` (npm `nightly` = `0.2.51-nightly.20261024-184918.4af30c7`). It releases leases whose owner is verified dead and keeps blocking live owners, and the parent menu is a live owner.
- Regina's own state at 02:02 was read later; see the next section.

# Regina remote state (read-only, 2026-10-25 02:09 UTC)

Access: `ssh -p 1004 sjglobal@100.64.0.25`, then `sudo -n -u regina-ai python3 -I -` running [`regina_lease_state.py`](evidence/regina_lease_state.py). Nothing was written. Output: [regina-remote-state.txt](evidence/regina-remote-state.txt).

| Time (UTC) | Observation |
|---|---|
| 02:01:40 | `ciel-runtime --continue` started; menu process `ciel_runtime.py cli --continue` PID 3774716 |
| 02:01:41 | `workspace-router-ports.json` written: workspace `/home/regina-ai` (port 8801), `launch_lease.pid` 3774716, `expires_at` 02:02:11 |
| 02:02:02.620-02:02:02.967 | `session-backup-state.json`: trigger `menu`, `last_ok False`, `last_error` = the line 253 → 324 RuntimeError |
| 02:03:27 | router `ciel_runtime.py serve` PID 3776675 started (port 8801 healthy at 02:09) |
| 02:03:31 | `claude` started |

- ✅ Installed `0.2.51-nightly.20261024-184918.4af30c7`; `workspace_router_selection.py` SHA-256 equals the 4af30c7 file.
- ✅ At 02:02 the lease holder was the live menu process, and no router for the workspace was running yet (it started 85 s later). This is the reproduced condition; the earlier 🟡 about router state is resolved for this failure.
- ✅ The lease record still names PID 3774716 after its `expires_at`, consistent with the code never comparing `expires_at` to the clock.
