"""CLI session restart requests: router-side queueing and client-side supervision.

The router and the CLI client are separate processes.  A restart request is a
single-slot JSON file inside the router instance directory, which both
processes share (the launcher exports ``CIEL_RUNTIME_STATE_DIR``): the router
writes it (MCP tool or ``ciel-runtime restart-session``) and the client
supervisor - the ``ciel-runtime cli`` wrapper that owns the CLI child process -
consumes it, terminates the CLI, and relaunches it with the runtime's
session-continue argument (``--continue`` for Claude Code, ``resume --last``
for Codex) so the conversation resumes instead of starting over.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Log = Callable[[str, str], None]

RUNTIME_SESSION_RESTART_SCHEMA = "ciel-runtime.runtime-session-restart/v1"
DEFAULT_RUNTIME_SESSION_RESTART_TTL_SECONDS = 300.0
RUNTIME_SESSION_RESTART_ENV_TTL = "CIEL_RUNTIME_SESSION_RESTART_TTL_SECONDS"

# Flags that already select a conversation, mirroring launch_state's session
# control list.  A restart must not append a second session selector.
SESSION_CONTROL_FLAGS = (
    "-c",
    "--continue",
    "-r",
    "--resume",
    "--session-id",
    "--fork-session",
    "--from-pr",
)

# How each runtime resumes its most recent conversation.
RESUME_COMMANDS: dict[str, tuple[str, ...]] = {
    "claude": ("--continue",),
    "codex": ("resume", "--last"),
    "agy": ("--continue",),
    "grok": ("--continue",),
    "zcode": ("--continue",),
    "muse": ("resume",),
}


def runtime_uses_resume_commands(runtime: str) -> bool:
    return str(runtime or "").strip().lower() in RESUME_COMMANDS


def runtime_session_control_present(argv: Sequence[str], runtime: str) -> bool:
    """Whether the argv already selects a conversation to continue."""

    mode = str(runtime or "").strip().lower()
    arguments = [str(item) for item in argv]
    if mode == "codex":
        if "resume" in arguments[1:]:
            return True
        for index, argument in enumerate(arguments[1:], start=1):
            if argument == "--continue":
                return True
            if argument == "-c" and index + 1 < len(arguments):
                value = arguments[index + 1]
                # Codex reads `-c key=value` as a config override, not as
                # Claude's continue flag (codex_cli's passthrough mapping).
                if "=" not in value:
                    return True
        return False
    return any(
        argument in SESSION_CONTROL_FLAGS
        or any(argument.startswith(f"{flag}=") for flag in SESSION_CONTROL_FLAGS)
        for argument in arguments
    )


def runtime_resume_command(argv: Sequence[str], runtime: str) -> list[str]:
    """Return argv with this runtime's session-continue argument ensured."""

    mode = str(runtime or "").strip().lower()
    arguments = [str(item) for item in argv]
    if not arguments or mode not in RESUME_COMMANDS:
        return arguments
    if runtime_session_control_present(arguments, mode):
        return arguments
    if mode == "claude":
        # Claude Code parses options before a bare `--`; insert ahead of it so
        # the argument stays an option even when a passthrough boundary exists.
        index = arguments.index("--") if "--" in arguments else len(arguments)
        return [*arguments[:index], "--continue", *arguments[index:]]
    if mode == "codex":
        # Codex accepts its resume subcommand after the global flags, which is
        # how ciel-runtime already launches it (`codex ... resume <session>`).
        return [*arguments, *RESUME_COMMANDS[mode]]
    return [*arguments, *RESUME_COMMANDS[mode]]


@dataclass(frozen=True, slots=True)
class RuntimeSessionRestartRequest:
    id: str
    source: str
    reason: str
    runtime: str
    target_pid: int
    resume: bool
    requested_at: float
    expires_at: float

    @classmethod
    def from_mapping(cls, value: Any) -> "RuntimeSessionRestartRequest | None":
        if not isinstance(value, Mapping):
            return None
        try:
            request = cls(
                id=str(value.get("id") or "").strip(),
                source=str(value.get("source") or "").strip(),
                reason=str(value.get("reason") or "").strip(),
                runtime=str(value.get("runtime") or "").strip().lower(),
                target_pid=int(value.get("target_pid") or 0),
                resume=bool(value.get("resume", True)),
                requested_at=float(value.get("requested_at") or 0.0),
                expires_at=float(value.get("expires_at") or 0.0),
            )
        except (TypeError, ValueError):
            return None
        return request if request.id and request.requested_at > 0 else None

    def expired(self, now: float) -> bool:
        return self.expires_at > 0 and now >= self.expires_at

    def applies_to(self, client_pid: int) -> bool:
        return self.target_pid <= 0 or self.target_pid == int(client_pid or 0)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": RUNTIME_SESSION_RESTART_SCHEMA,
            "id": self.id,
            "source": self.source,
            "reason": self.reason,
            "runtime": self.runtime,
            "target_pid": self.target_pid,
            "resume": self.resume,
            "requested_at": self.requested_at,
            "expires_at": self.expires_at,
        }

    def summary(self) -> str:
        return (
            f"id={self.id} source={self.source or '-'} "
            f"runtime={self.runtime or 'any'} "
            f"target_pid={self.target_pid or '-'} "
            f"resume={str(self.resume).lower()} "
            f"reason={self.reason or '-'}"
        )


class RuntimeSessionRestartRepository:
    """Single-slot restart request file shared by the router and its clients."""

    def __init__(
        self,
        path: Path,
        log: Log,
        *,
        ttl_seconds: float | Callable[[], float] | None = None,
        clock: Callable[[], float] = time.time,
        lock: Any = None,
    ) -> None:
        self.path = path
        self.log = log
        self.clock = clock
        self._ttl = ttl_seconds
        self._lock = lock

    def ttl_seconds(self) -> float:
        value = self._ttl() if callable(self._ttl) else self._ttl
        try:
            resolved = float(
                value
                if value is not None
                else os.environ.get(RUNTIME_SESSION_RESTART_ENV_TTL)
                or DEFAULT_RUNTIME_SESSION_RESTART_TTL_SECONDS
            )
        except (TypeError, ValueError):
            resolved = DEFAULT_RUNTIME_SESSION_RESTART_TTL_SECONDS
        return max(5.0, resolved)

    def _guard(self) -> Any:
        if self._lock is None:
            return _NullLock()
        return self._lock

    def queue(
        self,
        *,
        source: str,
        reason: str = "",
        runtime: str = "",
        target_pid: int = 0,
        resume: bool = True,
    ) -> RuntimeSessionRestartRequest:
        now = self.clock()
        request = RuntimeSessionRestartRequest(
            id=uuid.uuid4().hex,
            source=str(source or "mcp").strip() or "mcp",
            reason=str(reason or "").strip()[:1000],
            runtime=str(runtime or "").strip().lower(),
            target_pid=max(0, int(target_pid or 0)),
            resume=bool(resume),
            requested_at=now,
            expires_at=now + self.ttl_seconds(),
        )
        payload = request.to_mapping()
        with self._guard():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(
                f"{self.path.name}.{os.getpid()}.{time.time_ns()}.tmp"
            )
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            temporary.replace(self.path)
        self.log("INFO", f"runtime_session_restart_queued {request.summary()}")
        return request

    def read(self) -> RuntimeSessionRestartRequest | None:
        with self._guard():
            try:
                text = self.path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return None
            except OSError as exc:
                self.log(
                    "WARN",
                    f"runtime_session_restart_read_failed "
                    f"error={type(exc).__name__}: {exc}",
                )
                return None
        try:
            data = json.loads(text)
        except (ValueError, TypeError, json.JSONDecodeError):
            data = None
        request = RuntimeSessionRestartRequest.from_mapping(data)
        if request is None:
            self.log("WARN", "runtime_session_restart_invalid_payload")
            self.clear()
            return None
        if request.expired(self.clock()):
            self.log("INFO", f"runtime_session_restart_expired {request.summary()}")
            self.clear(request.id)
            return None
        return request

    def clear(self, request_id: str | None = None) -> bool:
        with self._guard():
            try:
                if request_id:
                    try:
                        data = json.loads(self.path.read_text(encoding="utf-8"))
                    except (OSError, ValueError, TypeError, json.JSONDecodeError):
                        # An unreadable slot holds nothing worth preserving.
                        data = None
                    current = RuntimeSessionRestartRequest.from_mapping(data)
                    if current is not None and current.id != request_id:
                        return False
                self.path.unlink()
                return True
            except FileNotFoundError:
                return False
            except OSError as exc:
                self.log(
                    "WARN",
                    f"runtime_session_restart_clear_failed "
                    f"error={type(exc).__name__}: {exc}",
                )
                return False

    def claim(self, client_pid: int) -> RuntimeSessionRestartRequest | None:
        """Claim a pending request for this client process, if one applies."""

        request = self.read()
        if request is None:
            return None
        if not request.applies_to(client_pid):
            return None
        if not self.clear(request.id):
            # Another client consumed it between read and clear.
            return None
        self.log(
            "INFO",
            f"runtime_session_restart_claimed client_pid={int(client_pid or 0)} "
            f"{request.summary()}",
        )
        return request


@dataclass(slots=True)
class RuntimeSessionRestartControl:
    """Process-local record of a restart performed by the CLI transport."""

    poll: Callable[[], RuntimeSessionRestartRequest | None]
    request: RuntimeSessionRestartRequest | None = None

    @property
    def requested(self) -> bool:
        return self.request is not None

    def mark(self, request: RuntimeSessionRestartRequest) -> None:
        self.request = request

    def reset(self) -> None:
        self.request = None

    def incoming(self) -> RuntimeSessionRestartRequest | None:
        """Poll for a new request while the CLI child runs.

        The returned request is remembered, so the transports keep polling
        only until the first request arrives and relaunch exactly once.
        """

        if self.request is not None:
            return None
        request = self.poll()
        if request is not None:
            self.request = request
        return request


@dataclass(frozen=True, slots=True)
class SessionRestartPorts:
    """Launch-service port that mints one restart control per CLI launch."""

    control: Callable[[], RuntimeSessionRestartControl] = lambda: RuntimeSessionRestartControl(
        poll=lambda: None
    )

    def new_control(self) -> RuntimeSessionRestartControl:
        return self.control()


def session_restart_clients(
    clients_dir: Path,
    *,
    is_running: Callable[[int], bool],
) -> list[dict[str, Any]]:
    """Live CLI client records (launcher pid, workspace, start time)."""

    records: list[dict[str, Any]] = []
    try:
        paths = sorted(clients_dir.glob("*.json"))
    except OSError:
        return records
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        try:
            pid = int(data.get("pid") or path.stem or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0 or not is_running(pid):
            continue
        records.append({
            "pid": pid,
            "workspace": str(data.get("workspace") or ""),
            "started_at": str(data.get("started_at") or ""),
            "router_port": int(data.get("router_port") or 0),
            "instance": clients_dir.parent.name,
        })
    return sorted(records, key=lambda item: (item["started_at"], item["pid"]))


def session_restart_instances(
    instances_root: Path,
    *,
    is_running: Callable[[int], bool],
    clients_dir_name: str = "router-clients",
) -> list[dict[str, Any]]:
    """Every router instance under ``instances_root`` with a live CLI client."""

    instances: list[dict[str, Any]] = []
    try:
        directories = sorted(path for path in instances_root.iterdir() if path.is_dir())
    except OSError:
        return instances
    for directory in directories:
        clients = session_restart_clients(
            directory / clients_dir_name, is_running=is_running
        )
        if clients:
            instances.append({
                "instance": directory.name,
                "path": directory,
                "clients": clients,
            })
    return instances


def queue_session_restart(
    *,
    clients_dir: Path,
    request_path: Path,
    source: str,
    is_running: Callable[[int], bool],
    log: Log,
    reason: str = "",
    runtime: str = "",
    client_pid: int = 0,
    resume: bool = True,
    lock: Any = None,
) -> dict[str, Any]:
    """Write a restart request for one instance's active client."""

    clients = session_restart_clients(clients_dir, is_running=is_running)
    target = int(client_pid or 0)
    if target > 0:
        known = {record["pid"] for record in clients}
        if known and target not in known:
            return {
                "ok": False,
                "queued": False,
                "clients": clients,
                "detail": (
                    f"client pid {target} is not an active client of this router instance"
                ),
            }
    elif clients:
        target = clients[-1]["pid"]
    else:
        return {
            "ok": False,
            "queued": False,
            "clients": [],
            "detail": (
                "no active ciel-runtime CLI client is registered for this router instance"
            ),
        }
    repository = RuntimeSessionRestartRepository(request_path, log, lock=lock)
    request = repository.queue(
        source=source,
        reason=reason,
        runtime=runtime,
        target_pid=target,
        resume=resume,
    )
    return {
        "ok": True,
        "queued": True,
        "request": request.to_mapping(),
        "target": next(
            (record for record in clients if record["pid"] == target), {"pid": target}
        ),
        "clients": clients,
    }


RESTART_REQUEST_FILE_NAME = "runtime-session-restart.json"
RESTART_CLIENTS_DIR_NAME = "router-clients"


@dataclass(frozen=True, slots=True)
class RuntimeSessionRestartServicePorts:
    instance_dir: Path
    instances_root: Path
    is_running: Callable[[int], bool]
    log: Log
    workspace_digest: Callable[[str], str]
    clock: Callable[[], float] = time.time
    unisolated_test: Callable[[], bool] = lambda: False


class RuntimeSessionRestartService:
    """Router-side entry point: client discovery, queueing, and the CLI command."""

    def __init__(self, ports: RuntimeSessionRestartServicePorts) -> None:
        self._ports = ports
        self._lock = threading.Lock()
        self._control: RuntimeSessionRestartControl | None = None

    @staticmethod
    def instance_paths(instance_dir: Path) -> tuple[Path, Path]:
        directory = Path(instance_dir)
        return directory / RESTART_CLIENTS_DIR_NAME, directory / RESTART_REQUEST_FILE_NAME

    def request_path(self, instance_dir: Path | None = None) -> Path:
        return self.instance_paths(instance_dir or self._ports.instance_dir)[1]

    def repository(self, instance_dir: Path | None = None) -> RuntimeSessionRestartRepository:
        directory = Path(instance_dir) if instance_dir else self._ports.instance_dir
        return RuntimeSessionRestartRepository(
            self.request_path(directory),
            self._ports.log,
            clock=self._ports.clock,
            lock=self._lock,
        )

    def control(self) -> RuntimeSessionRestartControl:
        """The control shared by the CLI transports of this process."""

        if self._control is None:
            self._control = RuntimeSessionRestartControl(poll=self.claim)
        return self._control

    def claim(self) -> RuntimeSessionRestartRequest | None:
        return self.repository().claim(os.getpid())

    def clients(self, instance_dir: Path | None = None) -> list[dict[str, Any]]:
        clients_dir = self.instance_paths(instance_dir or self._ports.instance_dir)[0]
        return session_restart_clients(clients_dir, is_running=self._ports.is_running)

    def instances(self) -> list[dict[str, Any]]:
        return session_restart_instances(
            self._ports.instances_root,
            is_running=self._ports.is_running,
            clients_dir_name=RESTART_CLIENTS_DIR_NAME,
        )

    def queue(
        self,
        *,
        source: str,
        reason: str = "",
        runtime: str = "",
        client_pid: int = 0,
        resume: bool = True,
        instance_dir: Path | None = None,
    ) -> dict[str, Any]:
        if self._ports.unisolated_test():
            # A restart terminates a live CLI; a loose test runner must never
            # reach the developer's session (observed 2026-09-18 for the
            # router/client lifecycle paths).
            self._ports.log(
                "WARN",
                f"runtime_session_restart_skipped_unisolated_test source={source}",
            )
            return {
                "ok": False,
                "queued": False,
                "clients": [],
                "detail": "refusing to restart a CLI session from an unisolated test process",
            }
        clients_dir, request_path = self.instance_paths(
            instance_dir or self._ports.instance_dir
        )
        return queue_session_restart(
            clients_dir=clients_dir,
            request_path=request_path,
            source=source,
            reason=reason,
            runtime=runtime,
            client_pid=client_pid,
            resume=resume,
            is_running=self._ports.is_running,
            log=self._ports.log,
            lock=self._lock,
        )

    def queue_tool(
        self,
        *,
        reason: str = "",
        runtime: str = "",
        client_pid: int = 0,
        resume: bool = True,
    ) -> dict[str, Any]:
        """MCP tool entry point (``restart_session``)."""

        return self.queue(
            source="ciel-runtime-router-tool",
            reason=reason,
            runtime=runtime,
            client_pid=client_pid,
            resume=resume,
        )

    def command(self, args: Any) -> None:
        """`ciel-runtime restart-session` entry point."""

        session_restart_command(
            args,
            SessionRestartCliPorts(
                instances=self.instances,
                queue=self.queue,
                workspace_digest=self._ports.workspace_digest,
            ),
        )


def local_runtime_session_restart(
    ports: Callable[[], RuntimeSessionRestartServicePorts],
) -> Callable[[], RuntimeSessionRestartService]:
    """Memoize one service per process without binding paths at import time."""

    services: list[RuntimeSessionRestartService] = []

    def service() -> RuntimeSessionRestartService:
        if not services:
            services.append(RuntimeSessionRestartService(ports()))
        return services[0]

    return service


@dataclass(frozen=True, slots=True)
class SessionRestartCliPorts:
    instances: Callable[[], list[dict[str, Any]]]
    queue: Callable[..., dict[str, Any]]
    workspace_digest: Callable[[str], str]
    output: Callable[[str], None] = print


def session_restart_command(args: Any, ports: SessionRestartCliPorts) -> None:
    """`ciel-runtime restart-session`: queue a restart from outside the session."""

    output = ports.output
    reason = str(getattr(args, "reason", "") or "").strip()
    runtime = str(getattr(args, "runtime", "") or "").strip()
    workspace = str(getattr(args, "workspace", "") or "").strip()
    try:
        pid = int(getattr(args, "pid", 0) or 0)
    except (TypeError, ValueError):
        pid = 0
    resume = not bool(getattr(args, "no_resume", False))
    instances = ports.instances()
    clients = [client for record in instances for client in record["clients"]]
    if not clients:
        output(
            "ciel-runtime restart-session: no active CLI session is running "
            "through ciel-runtime"
        )
        raise SystemExit(2)
    if pid > 0:
        matches = [
            record
            for record in instances
            if any(client["pid"] == pid for client in record["clients"])
        ]
        if not matches:
            output(
                f"ciel-runtime restart-session: client pid {pid} is not an active CLI session"
            )
            raise SystemExit(2)
        target_instance = matches[-1]
    elif workspace:
        suffix = f"-{ports.workspace_digest(workspace)}"
        matches = [
            record for record in instances if record["instance"].endswith(suffix)
        ]
        if not matches:
            output(
                "ciel-runtime restart-session: no active CLI session for workspace "
                f"{workspace}"
            )
            raise SystemExit(2)
        target_instance = matches[-1]
    elif len(clients) == 1:
        target_instance = instances[-1]
    else:
        output("ciel-runtime restart-session: multiple CLI sessions are active; select one:")
        for client in clients:
            output(
                f"  --pid {client['pid']}  workspace={client['workspace'] or '-'} "
                f"router_port={client['router_port'] or '-'} "
                f"started={client['started_at'] or '-'}"
            )
        raise SystemExit(2)
    result = ports.queue(
        source="cli",
        reason=reason,
        runtime=runtime,
        client_pid=pid,
        resume=resume,
        instance_dir=target_instance["path"],
    )
    if not result.get("ok"):
        output(f"ciel-runtime restart-session: {result.get('detail')}")
        raise SystemExit(2)
    target = result.get("target") or {}
    request = result.get("request") or {}
    output(
        "ciel-runtime restart-session: queued "
        f"request_id={request.get('id')} client_pid={target.get('pid')} "
        f"workspace={target.get('workspace') or '-'} "
        f"resume={str(bool(request.get('resume'))).lower()}"
    )


class _NullLock:
    def __enter__(self) -> "_NullLock":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


__all__ = [
    "DEFAULT_RUNTIME_SESSION_RESTART_TTL_SECONDS",
    "RESTART_CLIENTS_DIR_NAME",
    "RESTART_REQUEST_FILE_NAME",
    "RESUME_COMMANDS",
    "RUNTIME_SESSION_RESTART_ENV_TTL",
    "RUNTIME_SESSION_RESTART_SCHEMA",
    "SESSION_CONTROL_FLAGS",
    "RuntimeSessionRestartControl",
    "RuntimeSessionRestartRepository",
    "RuntimeSessionRestartRequest",
    "RuntimeSessionRestartService",
    "RuntimeSessionRestartServicePorts",
    "SessionRestartCliPorts",
    "SessionRestartPorts",
    "local_runtime_session_restart",
    "queue_session_restart",
    "runtime_resume_command",
    "runtime_session_control_present",
    "runtime_uses_resume_commands",
    "session_restart_clients",
    "session_restart_command",
    "session_restart_instances",
]
