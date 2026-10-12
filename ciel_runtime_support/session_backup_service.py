"""When session backups run: schedule, turn end, before a restart, at session end, on request.

Settings live in ``<CONFIG_DIR>/session-backup.json`` (targets, default targets and
``schedule``).  Every automatic backup runs ``ciel-runtime backup create`` as a child
process, one at a time per workspace (a pid lock file), so a slow upload never
blocks the router or the agent.  The last run per workspace is recorded in
``session-backup-state.json`` for the menu, ``backup status`` and the MCP tool.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from ciel_runtime_support.process_control import pid_is_running
from ciel_runtime_support.workspace_router_selection import workspace_digest

SETTINGS_FILE = "session-backup.json"
STATE_FILE = "session-backup-state.json"
DEFAULT_SCHEDULE: dict[str, Any] = {
    "enabled": False,          # periodic backups while the router runs
    "interval_minutes": 60,
    "on_turn_end": False,      # after an agent turn, at most every min_interval_minutes
    "min_interval_minutes": 15,
    "before_restart": False,   # after the CLI exits for a restart, before it starts again
    "on_session_end": False,   # after the CLI exits for good
    "include_files": True,
    "max_file_mb": 1024,       # larger work-folder files are left out (and listed in the result)
    "parallel_transfers": 8,   # chunks compressed and stored at the same time (1-16)
    "keep_last": 30,
    "keep_daily": 14,
}
SCRIPT = Path(__file__).resolve().parents[1] / "ciel_runtime.py"
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def load_settings(config_dir: Path) -> dict[str, Any]:
    try:
        settings = json.loads((Path(config_dir) / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = {}
    if not isinstance(settings, dict):
        settings = {}
    settings.setdefault("targets", {})
    settings.setdefault("default_targets", [])
    settings["schedule"] = {**DEFAULT_SCHEDULE, **(settings.get("schedule") or {})}
    return settings


def save_settings(config_dir: Path, settings: Mapping[str, Any]) -> None:
    path = Path(config_dir) / SETTINGS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_state(config_dir: Path) -> dict[str, Any]:
    try:
        state = json.loads((Path(config_dir) / STATE_FILE).read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def record_state(config_dir: Path, cwd: Path, **fields: Any) -> None:
    state = _read_state(config_dir)
    key = workspace_digest(cwd)
    state[key] = {**(state.get(key) or {}), "cwd": str(cwd), **fields}
    path = Path(config_dir) / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def workspace_state(config_dir: Path, cwd: Path) -> dict[str, Any]:
    return dict(_read_state(config_dir).get(workspace_digest(cwd)) or {})


def _lock_path(config_dir: Path, cwd: Path) -> Path:
    return Path(config_dir) / "backup-locks" / f"{workspace_digest(cwd)}.lock"


def _acquire(config_dir: Path, cwd: Path) -> bool:
    path = _lock_path(config_dir, cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                holder = int(path.read_text(encoding="utf-8").strip() or 0)
            except (OSError, ValueError):
                holder = 0
            if holder and pid_is_running(holder):
                return False
            path.unlink(missing_ok=True)
            continue
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
        return True
    return False


def _release(config_dir: Path, cwd: Path) -> None:
    _lock_path(config_dir, cwd).unlink(missing_ok=True)


def backup_command(cwd: Path, trigger: str, schedule: Mapping[str, Any], *, full: bool = False) -> list[str]:
    command = [sys.executable, str(SCRIPT), "cli", "backup", "create", "--cwd", str(cwd), "--trigger", trigger, "--json", "--prune"]
    if not schedule.get("include_files", True):
        command.append("--no-files")
    if full:
        command.append("--full")
    return command


def _record_quietly(config_dir: Path, cwd: Path, **fields: Any) -> None:
    """State bookkeeping never fails a backup (e.g. a full system disk)."""

    try:
        record_state(config_dir, cwd, **fields)
    except OSError:
        pass


def read_progress(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def run_backup(
    config_dir: Path,
    cwd: Path,
    trigger: str,
    *,
    timeout: float | None = None,
    on_progress: Callable[[dict[str, Any], float], bool | None] | None = None,
    popen: Callable[..., Any] = subprocess.Popen,
    environ: Mapping[str, str] | None = None,
    poll_seconds: float = 0.5,
    full: bool = False,
) -> dict[str, Any]:
    """Run one backup for ``cwd`` now (blocking); skipped when one is already running.

    No time limit by default: a large first backup takes as long as it takes, while
    ``on_progress(state, elapsed)`` sees files/read/uploaded about once a second and
    can return False to cancel."""

    if not _acquire(config_dir, cwd):
        return {"ok": False, "skipped": "another backup of this workspace is running"}
    started = time.time()
    _record_quietly(config_dir, cwd, last_started=started, last_trigger=trigger, running=True)
    schedule = load_settings(config_dir)["schedule"]
    progress_path = Path(config_dir) / "backup-progress" / f"{workspace_digest(cwd)}.json"
    cancelled = timed_out = False
    try:
        try:
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            progress_path.unlink(missing_ok=True)
        except OSError:
            pass
        process = popen(
            [*backup_command(cwd, trigger, schedule, full=full), "--progress-file", str(progress_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            env=dict(environ if environ is not None else os.environ), creationflags=_NO_WINDOW,
        )
        while True:
            try:
                # Read while waiting: a child that has written more than the pipe holds (4 KB on
                # Windows, measured 2026-10-11) blocks until it is read, and its summary can be larger.
                stdout, stderr = process.communicate(timeout=poll_seconds)
                break
            except subprocess.TimeoutExpired:
                pass
            elapsed = time.time() - started
            if on_progress is not None and on_progress(read_progress(progress_path), elapsed) is False:
                cancelled = True
            if timeout is not None and elapsed > timeout:
                timed_out = True
            if cancelled or timed_out:
                process.kill()
                stdout, stderr = process.communicate()
                break
        try:
            summary = json.loads(stdout)
        except ValueError:
            summary = {}
        ok = process.returncode == 0 and not cancelled and not timed_out and "error" not in summary
        if not ok:
            reason = ("cancelled" if cancelled else f"stopped after {timeout:g} s" if timed_out
                      else summary.get("error") or (stdout + stderr).strip()[-600:] or f"exited with code {process.returncode}")
            summary = {**summary, "output": reason}
    except (OSError, subprocess.SubprocessError) as error:
        ok, summary = False, {"output": f"{type(error).__name__}: {error}"}
    finally:
        _release(config_dir, cwd)
    _record_quietly(
        config_dir, cwd, running=False, last_finished=time.time(), last_ok=ok,
        last_id=summary.get("id") if ok else workspace_state(config_dir, cwd).get("last_id"),
        last_error="" if ok else str(summary.get("output") or "")[:600],
    )
    return {"ok": ok, "trigger": trigger, "seconds": round(time.time() - started, 1), **summary}


def run_backup_in_background(config_dir: Path, cwd: Path, trigger: str, log: Callable[[str, str], Any] | None = None) -> None:
    def work() -> None:
        result = run_backup(config_dir, cwd, trigger)
        if log is not None:
            level = "INFO" if result.get("ok") or result.get("skipped") else "WARN"
            log(level, f"session_backup trigger={trigger} ok={result.get('ok')} id={result.get('id') or '-'} "
                       f"skipped={result.get('skipped') or '-'} seconds={result.get('seconds')}")

    threading.Thread(target=work, name="ciel-session-backup", daemon=True).start()


def activity_fingerprint(paths: list[Path]) -> tuple[float, int, int]:
    """Newest mtime, file count and total size under the session folders (cheap change check)."""

    newest, count, size = 0.0, 0, 0
    for base in paths:
        if base.is_file():
            items = [base]
        elif base.is_dir():
            items = (Path(root) / name for root, _, files in os.walk(base) for name in files)
        else:
            continue
        for item in items:
            try:
                stat = item.stat()
            except OSError:
                continue
            newest, count, size = max(newest, stat.st_mtime), count + 1, size + stat.st_size
    return newest, count, size


class BackupScheduler:
    """Router-side periodic and turn-end backups for the router's workspace."""

    def __init__(self, config_dir: Path, cwd: Path, watched: Callable[[], list[Path]], *,
                 log: Callable[[str, str], Any] | None = None, clock: Callable[[], float] = time.time,
                 launch: Callable[[Path, Path, str], None] | None = None, tick_seconds: float = 60.0) -> None:
        self.config_dir = Path(config_dir)
        self.cwd = Path(cwd)
        self.watched = watched
        self.log = log
        self.clock = clock
        self.launch = launch or (lambda config_dir, cwd, trigger: run_backup_in_background(config_dir, cwd, trigger, self.log))
        self.tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_fingerprint: tuple[float, int, int] | None = None

    def _since_last(self) -> float:
        state = workspace_state(self.config_dir, self.cwd)
        last = float(state.get("last_started") or 0)
        return self.clock() - last if last else float("inf")

    def tick(self) -> bool:
        schedule = load_settings(self.config_dir)["schedule"]
        if not schedule.get("enabled"):
            return False
        if self._since_last() < float(schedule.get("interval_minutes") or 60) * 60:
            return False
        fingerprint = activity_fingerprint(self.watched())
        if fingerprint == self._last_fingerprint:
            return False
        self._last_fingerprint = fingerprint
        self.launch(self.config_dir, self.cwd, "scheduled")
        return True

    def on_turn_ended(self, _fields: Mapping[str, Any] | None = None) -> bool:
        schedule = load_settings(self.config_dir)["schedule"]
        if not schedule.get("on_turn_end"):
            return False
        if self._since_last() < float(schedule.get("min_interval_minutes") or 0) * 60:
            return False
        self.launch(self.config_dir, self.cwd, "turn-end")
        return True

    def _run(self) -> None:
        while not self._stop.wait(self.tick_seconds):
            try:
                self.tick()
            except Exception as error:  # noqa: BLE001 - the router must keep running
                if self.log is not None:
                    self.log("WARN", f"session_backup_schedule_failed error={type(error).__name__}: {error}")

    def start(self) -> None:
        from ciel_runtime_support import tui_observation

        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="ciel-session-backup-scheduler", daemon=True)
            self._thread.start()
            tui_observation.TURN_ENDED_LISTENERS.append(self.on_turn_ended)

    def stop(self) -> None:
        from ciel_runtime_support import tui_observation

        self._stop.set()
        self._thread = None
        if self.on_turn_ended in tui_observation.TURN_ENDED_LISTENERS:
            tui_observation.TURN_ENDED_LISTENERS.remove(self.on_turn_ended)


def watched_paths(cwd: Path, environ: Mapping[str, str], home: Path, asset_home: Path, config_dir: Path) -> list[Path]:
    from ciel_runtime_support.session_backup_collect import claude_project_key, default_roots

    roots = default_roots(cwd, environ=environ, home=home, asset_home=asset_home, config_dir=config_dir)
    return [roots.claude / "projects" / claude_project_key(str(cwd)), roots.codex / "sessions", roots.ciel_ws]


def router_scheduler(config_dir: Path, environ: Mapping[str, str], home: Path, asset_home: Path,
                     log: Callable[[str, str], Any]) -> BackupScheduler:
    cwd = Path(environ.get("CIEL_RUNTIME_LAUNCH_CWD") or os.getcwd())
    return BackupScheduler(config_dir, cwd, lambda: watched_paths(cwd, environ, home, asset_home, config_dir), log=log)


def after_cli_exit(config_dir: Path, cwd: Path, *, restarting: bool, log: Callable[[str, str], Any] | None = None) -> dict[str, Any] | None:
    """Back up between a CLI exit and its restart (or at session end), if enabled."""

    schedule = load_settings(config_dir)["schedule"]
    if not schedule.get("before_restart" if restarting else "on_session_end"):
        return None
    trigger = "pre-restart" if restarting else "session-end"
    print(f"Ciel Runtime: backing up the session ({trigger})...", flush=True)
    shown = [0.0]

    def show(state: dict[str, Any], elapsed: float) -> bool:
        if elapsed - shown[0] >= 5:
            shown[0] = elapsed
            print(f"  ... {state.get('files', 0)} files, {int(state.get('read') or 0) / 1048576:.0f} MB read, "
                  f"{int(state.get('uploaded') or 0) / 1048576:.0f} MB stored ({elapsed:.0f} s)", flush=True)
        return True

    # Between a CLI exit and its restart the user waits, so this one has a limit.
    result = run_backup(config_dir, cwd, trigger, timeout=600, on_progress=show)
    message = (f"Ciel Runtime: session backup {result.get('id')} saved" if result.get("ok")
               else f"Ciel Runtime: session backup not saved ({result.get('skipped') or str(result.get('output') or '')[:200]})")
    print(message, flush=True)
    if log is not None:
        log("INFO" if result.get("ok") else "WARN", f"session_backup trigger={trigger} ok={result.get('ok')} id={result.get('id') or '-'}")
    return result


def mcp_session_backup(config_dir: Path, cwd: Path, args: Mapping[str, Any]) -> dict[str, Any]:
    """The ``session_backup`` router tool: create, list or status (restore is local-only)."""

    action = str(args.get("action") or "status")
    if action == "create":
        # A backup can take far longer than a tool call may wait: start it and report through status.
        if workspace_state(config_dir, cwd).get("running"):
            return {"ok": True, "started": False, "note": "a backup of this workspace is already running", "last": workspace_state(config_dir, cwd)}
        run_backup_in_background(config_dir, cwd, "mcp")
        return {"ok": True, "started": True, "note": "backup started; call session_backup status for progress and the result"}
    if action == "list":
        result = subprocess.run([sys.executable, str(SCRIPT), "cli", "backup", "list", "--cwd", str(cwd), "--json"],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=300, creationflags=_NO_WINDOW)
        try:
            rows = json.loads(result.stdout)
        except ValueError:
            return {"ok": False, "output": (result.stdout + result.stderr).strip()[-600:]}
        return {"ok": True, "snapshots": rows[-int(args.get("limit") or 20):]}
    if action == "status":
        settings = load_settings(config_dir)
        last = workspace_state(config_dir, cwd)
        progress = read_progress(Path(config_dir) / "backup-progress" / f"{workspace_digest(cwd)}.json") if last.get("running") else {}
        return {"ok": True, "schedule": settings["schedule"], "default_targets": settings["default_targets"] or ["local"],
                "targets": sorted(settings["targets"]), "last": last, **({"progress": progress} if progress else {})}
    return {"ok": False, "error": f"unsupported action {action!r}; use create, list or status"}


__all__ = [
    "BackupScheduler",
    "DEFAULT_SCHEDULE",
    "SETTINGS_FILE",
    "activity_fingerprint",
    "after_cli_exit",
    "backup_command",
    "load_settings",
    "mcp_session_backup",
    "record_state",
    "router_scheduler",
    "read_progress",
    "run_backup",
    "run_backup_in_background",
    "save_settings",
    "start_router_scheduler",
    "stop_router_scheduler",
    "watched_paths",
    "workspace_state",
]


_ROUTER_SCHEDULER: BackupScheduler | None = None


def start_router_scheduler(config_dir: Path, environ: Mapping[str, str], home: Path, asset_home: Path,
                           log: Callable[[str, str], Any]) -> None:
    """Started with the router's services; idle until the schedule or turn-end option is on."""

    global _ROUTER_SCHEDULER
    if _ROUTER_SCHEDULER is None:
        _ROUTER_SCHEDULER = router_scheduler(config_dir, environ, home, asset_home, log)
        _ROUTER_SCHEDULER.start()


def stop_router_scheduler() -> None:
    global _ROUTER_SCHEDULER
    if _ROUTER_SCHEDULER is not None:
        _ROUTER_SCHEDULER.stop()
        _ROUTER_SCHEDULER = None
