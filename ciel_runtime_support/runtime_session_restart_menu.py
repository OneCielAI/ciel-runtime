"""Safe session restart on the operator surfaces: status projection and menu panel.

The web console and REST (work_http_controller ``runtime.restart`` and
``POST /ca/work/api/v1/runtime/restart``) and the prelaunch menu read the
same projection and queue through the same RuntimeSessionRestartService.queue
the MCP tool ``restart_session``, the ``/restart-session`` slash command and
``ciel-runtimectl restart-session`` use. Nothing here restarts a process: a
queued request is picked up by the session's own launcher, which waits for
the running turn, snapshots the worktrees and relaunches with ``--continue``.

Holds (restart_hold): from the moment a launcher claims a restart request,
inputs for that session wait for the relaunched session. A hold shows when it
started, the request and the launcher pid. ``inputs_since_hold`` is the
number of inputs that arrived in this router instance's input log after the
hold began (by id range); it is None when that cannot be read, never a guess.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .restart_hold import listing as hold_listing
from .runtime_session_restart import (
    RESTART_REQUEST_FILE_NAME,
    RuntimeSessionRestartRequest,
    RuntimeSessionRestartService,
    RuntimeSessionRestartServicePorts,
)

MENU_SOURCE = "menu"


def pending_request(instance_dir: Path, *, now: float | None = None) -> dict[str, Any] | None:
    """The queued, not yet claimed request of one instance; read-only (an expired one is not cleared)."""
    try:
        data = json.loads((Path(instance_dir) / RESTART_REQUEST_FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    request = RuntimeSessionRestartRequest.from_mapping(data)
    if request is None or request.expired(time.time() if now is None else now):
        return None
    return {key: value for key, value in request.to_mapping().items() if key != "schema"}


def holds(instance_dir: Path, count_since: Callable[[float], int] | None = None,
          *, now: float | None = None) -> list[dict[str, Any]]:
    rows = []
    for row in hold_listing(Path(instance_dir), now=now):
        count = None
        if count_since is not None:
            try:
                count = max(0, int(count_since(float(row["started_at"]))))
            except Exception:  # noqa: BLE001 - unknown stays unknown
                count = None
        rows.append({**row, "inputs_since_hold": count})
    return rows


def instance_status(service: RuntimeSessionRestartService, instance_dir: Path,
                    count_since: Callable[[float], int] | None = None) -> dict[str, Any]:
    """This router instance: live clients, the queued request, input holds."""
    return {"clients": service.clients(instance_dir), "pending_request": pending_request(instance_dir),
            "holds": holds(instance_dir, count_since)}


def menu_service() -> RuntimeSessionRestartService:
    """The service the prelaunch menu queues through (same paths as the router's)."""
    from .process_control import pid_is_running
    from .runtime_paths import CONFIG_DIR, ROUTER_INSTANCE_DIR
    from .test_isolation_guard import unisolated_test_process
    from .workspace_router_selection import workspace_digest, workspace_identity

    return RuntimeSessionRestartService(RuntimeSessionRestartServicePorts(
        ROUTER_INSTANCE_DIR, CONFIG_DIR / "router-instances", pid_is_running, lambda _level, _message: None,
        lambda path: workspace_digest(workspace_identity(path)), unisolated_test=unisolated_test_process))


def _when(value: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


def summary(service: RuntimeSessionRestartService) -> str:
    try:
        instances = service.instances()
    except OSError:
        return "unavailable"
    clients = sum(len(record["clients"]) for record in instances)
    held = sum(len(holds(record["path"])) for record in instances)
    return f"{clients} live session{'s' if clients != 1 else ''}" + (f" · {held} holding input" if held else "")


def panel_rows(service: RuntimeSessionRestartService) -> tuple[list[str], list[str]]:
    rows = ["Restarts a live session safely: it waits for the running turn, snapshots its worktrees,",
            "  holds new inputs until the relaunch, then resumes the conversation with --continue."]
    values = ["__info__", "__info__"]
    try:
        instances = service.instances()
    except OSError as exc:
        instances = []
        rows.append(f"Live sessions unavailable: {type(exc).__name__}")
        values.append("__info__")
    if not instances:
        rows.append("No live ciel-runtime CLI session.")
        values.append("__info__")
    for record in instances:
        pending = pending_request(record["path"])
        hold_rows = holds(record["path"])
        for client in record["clients"]:
            rows.append(f"Restart pid {client['pid']}  workspace={client['workspace'] or '-'}  "
                        f"started={client['started_at'] or '-'}")
            values.append(f"restart:{record['instance']}:{client['pid']}")
        if pending:
            rows.append(f"  queued request {str(pending['id'])[:12]} for pid {pending['target_pid'] or '-'} "
                        f"(source {pending['source'] or '-'})")
            values.append("__info__")
        for hold in hold_rows:
            rows.append(f"  holding inputs since {_when(hold['started_at'])} · pid {hold['pid']} · "
                        f"request {hold['request_id']}")
            values.append("__info__")
    rows.append("Back")
    values.append("back")
    return rows, values


def apply(service: RuntimeSessionRestartService, value: str,
          prompt: Callable[[str, str], str]) -> list[str]:
    """Queue the chosen session's restart after the person confirms it."""
    if not value.startswith("restart:"):
        return []
    _, instance, pid_text = value.split(":", 2)
    try:
        pid = int(pid_text)
    except ValueError:
        return ["Restart: invalid selection."]
    record = next((row for row in service.instances() if row["instance"] == instance), None)
    client = next((row for row in (record or {}).get("clients", []) if row["pid"] == pid), None)
    if record is None or client is None:
        return [f"Restart: pid {pid} is no longer a live session; nothing was queued."]
    confirm = prompt(f"Restart CLI session pid {pid} (workspace {client['workspace'] or '-'})? "
                     "It restarts when its turn ends. yes/no", "no")
    if not confirm.strip().lower().startswith("y"):
        return ["Restart cancelled; nothing was queued."]
    reason = " ".join(prompt("Reason (optional)", "").split())[:200]
    resume = not prompt("Resume the conversation with --continue? yes/no", "yes").strip().lower().startswith("n")
    result = service.queue(source=MENU_SOURCE, reason=reason, client_pid=pid, resume=resume,
                           instance_dir=record["path"])
    if not result.get("ok"):
        return [f"Restart not queued: {result.get('detail') or 'refused'}"]
    request = result.get("request") or {}
    return [f"Restart queued: request {str(request.get('id') or '')[:12]} for pid {pid} "
            f"(resume={'yes' if request.get('resume') else 'no'}). It starts when the running turn ends."]


__all__ = ["MENU_SOURCE", "apply", "holds", "instance_status", "menu_service", "panel_rows", "pending_request",
           "summary"]
