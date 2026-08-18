"""Workspace-scoped MCP desired state, runtime projection, and launch leases."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid
from typing import Any, Callable


_SERVER_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_RUNTIMES = frozenset({"claude", "codex", "codex-app-server"})


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item).strip()]


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(item)
        for key, item in value.items()
        if str(key).strip() and item is not None
    }


def workspace_mcp_servers(config: dict[str, Any], runtime: str | None = None) -> dict[str, dict[str, Any]]:
    root = config.get("workspace_mcp")
    raw_servers = root.get("servers") if isinstance(root, dict) else None
    if not isinstance(raw_servers, dict):
        return {}
    selected: dict[str, dict[str, Any]] = {}
    target_runtime = "codex" if runtime == "codex-app-server" else str(runtime or "")
    for raw_name, raw in raw_servers.items():
        name = str(raw_name or "").strip()
        if not _SERVER_ID.fullmatch(name) or not isinstance(raw, dict):
            continue
        if not bool(raw.get("enabled", True)):
            continue
        runtimes = [item.lower() for item in _string_list(raw.get("runtimes") or ["claude", "codex"])]
        if target_runtime and target_runtime not in runtimes:
            continue
        transport = str(raw.get("transport") or "stdio").strip().lower()
        if transport not in {"stdio", "streamable_http"}:
            continue
        item = {
            "enabled": True,
            "transport": transport,
            "runtimes": runtimes,
            "protocol": str(raw.get("protocol") or "auto").strip().lower(),
            "required": bool(raw.get("required", False)),
        }
        if transport == "stdio":
            command = str(raw.get("command") or "").strip()
            if not command:
                continue
            item.update(
                command=command,
                args=_string_list(raw.get("args")),
                cwd=str(raw.get("cwd") or "").strip(),
                env=_string_map(raw.get("env")),
            )
        else:
            url = str(raw.get("url") or "").strip()
            if not url:
                continue
            item.update(
                url=url,
                headers=_string_map(raw.get("headers") or raw.get("http_headers")),
                env_http_headers=_string_map(raw.get("env_http_headers")),
                bearer_token_env_var=str(raw.get("bearer_token_env_var") or "").strip(),
            )
        for key in ("startup_timeout_sec", "tool_timeout_sec"):
            try:
                number = float(raw.get(key) or 0)
            except (TypeError, ValueError):
                number = 0
            if number > 0:
                item[key] = number
        selected[name] = item
    return selected


def _toml_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _toml_array(values: list[str]) -> str:
    return "[" + ",".join(_toml_string(value) for value in values) + "]"


def project_claude_mcp(servers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for name, item in servers.items():
        if item["transport"] == "stdio":
            server: dict[str, Any] = {
                "type": "stdio",
                "command": item["command"],
                "args": list(item.get("args") or []),
            }
            if item.get("cwd"):
                server["cwd"] = item["cwd"]
            if item.get("env"):
                server["env"] = dict(item["env"])
        else:
            server = {"type": "http", "url": item["url"]}
            if item.get("headers"):
                server["headers"] = dict(item["headers"])
        projected[name] = server
    return {"mcpServers": projected}


def project_codex_mcp_args(servers: dict[str, dict[str, Any]]) -> list[str]:
    args: list[str] = []

    def add(key: str, value: str) -> None:
        args.extend(["-c", f"{key}={value}"])

    for name, item in servers.items():
        prefix = f"mcp_servers.{name}"
        add(f"{prefix}.enabled", "true")
        if item["transport"] == "stdio":
            add(f"{prefix}.command", _toml_string(item["command"]))
            add(f"{prefix}.args", _toml_array(list(item.get("args") or [])))
            if item.get("cwd"):
                add(f"{prefix}.cwd", _toml_string(item["cwd"]))
            for key, value in dict(item.get("env") or {}).items():
                add(f"{prefix}.env.{key}", _toml_string(value))
        else:
            add(f"{prefix}.url", _toml_string(item["url"]))
            for key, value in dict(item.get("headers") or {}).items():
                add(f"{prefix}.http_headers.{key}", _toml_string(value))
            for key, value in dict(item.get("env_http_headers") or {}).items():
                add(f"{prefix}.env_http_headers.{key}", _toml_string(value))
            if item.get("bearer_token_env_var"):
                add(
                    f"{prefix}.bearer_token_env_var",
                    _toml_string(item["bearer_token_env_var"]),
                )
        if item.get("required"):
            add(f"{prefix}.required", "true")
        for key in ("startup_timeout_sec", "tool_timeout_sec"):
            if item.get(key):
                add(f"{prefix}.{key}", str(item[key]))
    return args


@dataclass(frozen=True, slots=True)
class WorkspaceMcpLaunch:
    launch_id: str = ""
    runtime: str = ""
    directory: Path | None = None
    manifest_path: Path | None = None
    child_record_path: Path | None = None
    claude_config_paths: tuple[Path, ...] = ()
    codex_args: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return bool(self.launch_id and self.directory)


@dataclass(frozen=True, slots=True)
class WorkspaceMcpLaunchPorts:
    pid_running: Callable[[int], bool]
    command_line: Callable[[int], str]
    terminate_tree: Callable[..., bool]
    log: Callable[[str, str], None]
    current_pid: Callable[[], int] = os.getpid


class WorkspaceMcpLaunchService:
    def __init__(self, launches_dir: Path, workspace: str, ports: WorkspaceMcpLaunchPorts) -> None:
        self.launches_dir = launches_dir
        self.workspace = workspace
        self.ports = ports

    def prepare(self, runtime: str, config: dict[str, Any]) -> WorkspaceMcpLaunch:
        self.recover_stale()
        servers = workspace_mcp_servers(config, runtime)
        if not servers:
            return WorkspaceMcpLaunch()
        launch_id = uuid.uuid4().hex
        directory = self.launches_dir / launch_id
        directory.mkdir(parents=True, exist_ok=False)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        claude_paths: tuple[Path, ...] = ()
        codex_args: tuple[str, ...] = ()
        projected_digest = ""
        if runtime == "claude":
            path = directory / "claude-mcp.json"
            payload = project_claude_mcp(servers)
            _atomic_json(path, payload)
            claude_paths = (path,)
            projected_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            codex_args = tuple(project_codex_mcp_args(servers))
            payload = {"args": list(codex_args)}
            path = directory / "codex-mcp.json"
            _atomic_json(path, payload)
            projected_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = directory / "manifest.json"
        _atomic_json(
            manifest,
            {
                "schema": "ciel-runtime.workspace-mcp-launch/v1",
                "launch_id": launch_id,
                "workspace": self.workspace,
                "runtime": runtime,
                "owner_pid": self.ports.current_pid(),
                "created_at": time.time(),
                "server_names": sorted(servers),
                "projection_sha256": projected_digest,
                "state": "prepared",
            },
        )
        self.ports.log(
            "INFO",
            f"workspace_mcp_launch_prepared launch_id={launch_id} runtime={runtime} "
            f"servers={','.join(sorted(servers))}",
        )
        return WorkspaceMcpLaunch(
            launch_id=launch_id,
            runtime=runtime,
            directory=directory,
            manifest_path=manifest,
            child_record_path=directory / "runtime-child.json",
            claude_config_paths=claude_paths,
            codex_args=codex_args,
        )

    def finish(self, launch: WorkspaceMcpLaunch) -> None:
        if not launch.active or launch.directory is None:
            return
        self._remove_launch_dir(launch.directory, "normal_exit")

    def recover_stale(self) -> None:
        if not self.launches_dir.exists():
            return
        for directory in sorted(self.launches_dir.iterdir()):
            if not directory.is_dir():
                continue
            manifest = directory / "manifest.json"
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                owner_pid = int(data.get("owner_pid") or 0)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                self._remove_launch_dir(directory, "invalid_manifest")
                continue
            if owner_pid > 0 and self.ports.pid_running(owner_pid):
                continue
            self._recover_child(directory / "runtime-child.json", directory.name)
            self._remove_launch_dir(directory, "stale_owner")

    def _recover_child(self, record: Path, launch_id: str) -> None:
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
            pid = int(data.get("pid") or 0)
            expected = [str(item) for item in data.get("cmd") or []]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        if pid <= 0 or not self.ports.pid_running(pid) or not expected:
            return
        actual = self.ports.command_line(pid).lower()
        executable = Path(expected[0]).name.lower()
        runtime_marker = "claude" if "claude" in executable else "codex" if "codex" in executable else ""
        projection_markers = self._projection_markers(record.parent, expected, runtime_marker)
        identity_matches = (
            bool(actual)
            and executable in actual
            and bool(runtime_marker)
            and runtime_marker in actual
            and bool(projection_markers)
            and all(marker in actual for marker in projection_markers)
        )
        if not identity_matches:
            self.ports.log(
                "WARN",
                f"workspace_mcp_orphan_skipped_identity_mismatch launch_id={launch_id} pid={pid}",
            )
            return
        stopped = bool(self.ports.terminate_tree(pid, "orphaned workspace MCP runtime", quiet=True))
        self.ports.log(
            "INFO",
            f"workspace_mcp_orphan_recovered launch_id={launch_id} pid={pid} stopped={str(stopped).lower()}",
        )

    @staticmethod
    def _projection_markers(directory: Path, expected: list[str], runtime: str) -> list[str]:
        if runtime == "claude":
            marker = str(directory / "claude-mcp.json").lower()
            return [marker] if any(str(item).lower() == marker for item in expected) else []
        if runtime == "codex":
            markers = {
                str(item).split("=", 1)[0].lower()
                for item in expected
                if str(item).lower().startswith("mcp_servers.") and "=" in str(item)
            }
            return sorted(markers)
        return []

    def _remove_launch_dir(self, directory: Path, reason: str) -> None:
        try:
            root = self.launches_dir.resolve(strict=False)
            target = directory.resolve(strict=False)
            if target.parent != root:
                raise ValueError("launch directory escaped the workspace MCP state root")
            shutil.rmtree(target)
            self.ports.log(
                "INFO",
                f"workspace_mcp_launch_removed launch_id={directory.name} reason={reason}",
            )
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            self.ports.log(
                "WARN",
                f"workspace_mcp_launch_remove_failed path={directory} reason={reason} "
                f"error={type(exc).__name__}: {exc}",
            )


class WorkspaceMcpMenuService:
    def __init__(
        self,
        load_config: Callable[[], dict[str, Any]],
        save_config: Callable[[dict[str, Any]], None],
        workspace: str,
    ) -> None:
        self.load_config = load_config
        self.save_config = save_config
        self.workspace = workspace

    def panel_rows(self, config: dict[str, Any]) -> tuple[list[str], list[str]]:
        root = config.get("workspace_mcp")
        raw = root.get("servers") if isinstance(root, dict) else {}
        rows = [f"Workspace  [{self.workspace}]", "Add stdio MCP...", "Add Streamable HTTP MCP..."]
        values = ["__info__", "add-stdio", "add-http"]
        if isinstance(raw, dict):
            for name in sorted(raw):
                item = raw.get(name)
                if not isinstance(item, dict):
                    continue
                enabled = bool(item.get("enabled", True))
                transport = str(item.get("transport") or "stdio")
                runtimes = "/".join(_string_list(item.get("runtimes") or ["claude", "codex"]))
                rows.append(f"{name}  [{'on' if enabled else 'off'} · {transport} · {runtimes}]")
                values.append(f"toggle:{name}")
        rows.extend(["Remove MCP module...", "Back"])
        values.extend(["remove", "back"])
        return rows, values

    def update(self, action: str, value: Any = None) -> list[str]:
        config = self.load_config()
        root = config.setdefault("workspace_mcp", {"servers": {}})
        if not isinstance(root, dict):
            root = {"servers": {}}
            config["workspace_mcp"] = root
        servers = root.setdefault("servers", {})
        if not isinstance(servers, dict):
            servers = {}
            root["servers"] = servers
        if action.startswith("toggle:"):
            name = action.split(":", 1)[1]
            item = servers.get(name)
            if not isinstance(item, dict):
                return [f"MCP module not found: {name}"]
            item["enabled"] = not bool(item.get("enabled", True))
            self.save_config(config)
            return [f"Workspace MCP {name}: {'on' if item['enabled'] else 'off'}"]
        if action == "remove":
            name = str(value or "").strip()
            if name not in servers:
                return [f"MCP module not found: {name}"]
            del servers[name]
            self.save_config(config)
            return [f"Removed workspace MCP: {name}"]
        if action in {"add-stdio", "add-http"}:
            item = dict(value or {})
            name = str(item.pop("name", "")).strip()
            if not _SERVER_ID.fullmatch(name):
                raise ValueError("MCP id must use 1-80 letters, numbers, '_' or '-'")
            item["transport"] = "stdio" if action == "add-stdio" else "streamable_http"
            item["enabled"] = True
            runtimes = [runtime for runtime in _string_list(item.get("runtimes")) if runtime in _RUNTIMES]
            item["runtimes"] = sorted(set("codex" if runtime == "codex-app-server" else runtime for runtime in runtimes)) or ["claude", "codex"]
            item["protocol"] = str(item.get("protocol") or "auto")
            servers[name] = item
            self.save_config(config)
            return [f"Saved workspace MCP: {name}"]
        return []


__all__ = [
    "WorkspaceMcpLaunch",
    "WorkspaceMcpLaunchPorts",
    "WorkspaceMcpLaunchService",
    "WorkspaceMcpMenuService",
    "project_claude_mcp",
    "project_codex_mcp_args",
    "workspace_mcp_servers",
]
