---
type: implementation
title: Launch phase trace and launcher lease lifecycle
description: Secret-free launch phase start/end events with elapsed time and result, slow-phase console progress, and release of the launcher's own startup lease after a pre-child failure.
tags: [startup, observability, lease, launch]
timestamp: 2026-11-06
---

# Change

Follows the startup stall diagnostic at `docs/journal/2026/11/06/diagnostics/startup-stall/startup-and-side-mcp.md`. That diagnostic found the long-lived launcher's router log stopping after remote instruction sync, with no later phase record.

## Phase events (router.log)

Every traced phase writes a start line and an end line. Fields are only the runtime, phase, elapsed ms and result. A failure records only `error_type`, never the exception text, URLs or credentials.

```
launch_phase_start runtime=<rt> phase=<phase>
launch_phase_end runtime=<rt> phase=<phase> elapsed_ms=<n> result=<ok|status|error error_type=X>
```

| Phase | Where | Runtimes |
|---|---|---|
| `remote_instructions` (`reason=`) | `RemoteInstructionSynchronizer.sync` | each instruction runtime |
| `remote_memory` (`reason=`) | `RemoteMemorySynchronizer.sync` | launch runtime |
| `remote_provision` (`reason=`) | `RemoteProvisioner.provision` | `all` |
| `install_diagnostics`, `self_update_check`, `cli_update_check` | `runtime_launch` | claude, codex, codex-app-server, agy |
| `router_start` | `runtime_launch` | all four |
| `mcp_projection` (result `prepared`/`none`) | `runtime_launch` | claude, codex, codex-app-server |
| `child_spawn` (`transport=`, result `exit_<rc>`) | `runtime_launch` | all four, every transport |

A phase that takes 1 s or more also prints one console line (`Ciel Runtime startup: <phase> completed in N.Ns.`). `SynchronizedLaunch` does the same for remote assets as a whole.

## Lease lifecycle

- `runtime_paths.release_current_launch_lease()` delegates to the selection module's release. It removes the lease only when the lease PID is this process and its recorded birth identity matches. The port reservation is kept. An unknown identity changes nothing.
- `run_claude`, `run_codex`, `run_codex_app_server` and `run_agy` are wrapped by `_launch_lease_guarded`. On an exception before any child started, the outermost guard releases the lease. A `ContextVar` makes one menu dispatch count as a single launch. A failure after `child_spawn` keeps the lease, because the child or the managed router may still be alive.
- If prepare or remote-asset sync fails (including the provisioning `SystemExit(2)`), `SynchronizedLaunch` releases the lease before the delegate runs.
- A successful launch keeps its lease for the launcher's lifetime. A second launch of the same workspace still fails within the 8 s bounded registry lock. The message now names the holder PID and the workspace and says nothing was stopped. No process is killed.

# Evidence

- [focused-tests.txt](evidence/focused-tests.txt): 19 isolated files, 0 failures, ruff clean.
- [multi-process-lease.txt](evidence/multi-process-lease.txt): real `runtime_paths` import-time leases in separate processes with an isolated config and workspace:
  1. The holder takes the lease.
  2. A second launch is refused in about 0.14 s with the clear message, and the holder stays alive.
  3. A launcher fails before any child. It logs its phases and `launch_lease_release ... result=released`.
  4. The next launch takes the same port while that failed launcher is still alive.
- During the 2026-11-06 runs the live router 9524 kept pid 2664 and stayed healthy.

# Open

- Resolved at integration:
  - `ciel_runtime.py agy_launch_services` now passes `router_log`, and a test asserts the production assembly uses it.
  - `model_cache` is traced for claude, codex and codex-app-server.
  - The import-time port selection is replayed once per launch as `launch_phase_end ... phase=runtime_paths.router_port_select`. It runs before any logger exists, so it has no start line.
  - See `docs/journal/2026/11/06/diagnostics/startup-stall/fix-verification.md`.
- The same process re-running a launch after a released pre-child failure does not take the lease again. I did not check whether any in-process retry path exists.
- A real launch of the patched tree through `ciel-runtime.cmd --continue` logged the pre-menu phases (port selection 50 ms, install diagnostics 6 ms). A real Claude child launch was exercised by the native side E2E, which used its own harness rather than the launcher.
