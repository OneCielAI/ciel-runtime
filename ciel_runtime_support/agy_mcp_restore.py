"""Restore missing managed MCP servers into AGY's native JSON configuration."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .mcp_inventory import McpInventoryService


@dataclass(frozen=True, slots=True)
class AgyMcpRestorePorts:
    discover_managed: Callable[[Path], dict[str, dict[str, Any]]]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class AgyMcpRestoreService:
    ports: AgyMcpRestorePorts

    def restore(
        self,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> list[str]:
        environment = os.environ if env is None else env
        working_directory = (cwd or Path.cwd()).resolve()
        home = self._home(environment)
        global_path = home / ".gemini" / "antigravity-cli" / "mcp_config.json"
        workspace_path = working_directory / ".agents" / "mcp_config.json"
        try:
            before = self._signature(global_path)
            global_data = self._load(global_path)
            workspace_data = self._load(workspace_path)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self.ports.log(
                "WARN",
                "agy_mcp_restore_config_read_failed "
                f"error={type(exc).__name__}: {exc}",
            )
            return []

        global_servers = self._servers(global_data)
        reserved = set(self._servers(workspace_data))
        projected: dict[str, dict[str, Any]] = {}
        for name, server in self.ports.discover_managed(working_directory).items():
            normalized = self._project_server(server)
            if normalized is None:
                self.ports.log(
                    "WARN", f"agy_mcp_restore_skipped_unsupported server={name}"
                )
                continue
            projected[name] = normalized
        merged = McpInventoryService.merge(
            global_servers, projected, reserved=reserved
        )
        if not merged.added:
            return []
        restored = dict(global_data)
        restored["mcpServers"] = merged.servers
        try:
            self._atomic_replace(global_path, restored, before)
        except (OSError, UnicodeError, TypeError, ValueError, RuntimeError) as exc:
            self.ports.log(
                "WARN",
                "agy_mcp_restore_write_failed "
                f"path={global_path} error={type(exc).__name__}: {exc}",
            )
            return []
        names = list(merged.added)
        self.ports.log(
            "INFO",
            "agy_mcp_config_restored "
            f"path={global_path} servers={','.join(sorted(names))}",
        )
        return names

    @staticmethod
    def _home(env: Mapping[str, str]) -> Path:
        configured = str(env.get("HOME") or env.get("USERPROFILE") or "").strip()
        return Path(configured).expanduser() if configured else Path.home()

    @staticmethod
    def _signature(path: Path) -> tuple[int, int, int] | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return stat.st_mtime_ns, stat.st_size, getattr(stat, "st_ino", 0)

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"AGY MCP config must be a JSON object: {path}")
        servers = value.get("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError(f"AGY mcpServers must be a JSON object: {path}")
        return value

    @staticmethod
    def _servers(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        value = data.get("mcpServers", {})
        return {
            str(name): dict(server)
            for name, server in value.items()
            if str(name).strip() and isinstance(server, dict)
        }

    @staticmethod
    def _project_server(server: dict[str, Any]) -> dict[str, Any] | None:
        command = str(server.get("command") or "").strip()
        url = str(
            server.get("serverUrl")
            or server.get("serverURL")
            or server.get("url")
            or server.get("endpoint")
            or ""
        ).strip()
        args = server.get("args")
        arguments = [str(item) for item in args] if isinstance(args, list) else []
        if "mcp-proxy" in arguments and "--server-config" in arguments:
            return None
        if command:
            projected: dict[str, Any] = {"command": command}
        elif url.startswith(("http://", "https://")):
            projected = {"serverUrl": url}
        else:
            return None
        aliases = {
            "http_headers": "headers",
            "env_http_headers": "headers",
            "type": "transport",
        }
        supported = (
            "args", "env", "headers", "transport", "authProviderType",
        )
        normalized = dict(server)
        for source, target in aliases.items():
            if target not in normalized and source in normalized:
                normalized[target] = normalized[source]
        for key in supported:
            value = normalized.get(key)
            if isinstance(value, (str, bool, int, float, list, dict)):
                projected[key] = value
        return projected

    @classmethod
    def _atomic_replace(
        cls,
        path: Path,
        value: dict[str, Any],
        expected: tuple[int, int, int] | None,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if cls._signature(path) != expected:
            raise RuntimeError("AGY config changed while MCP restoration was running")
        temporary = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            temporary.write_text(
                json.dumps(value, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            if cls._signature(path) != expected:
                raise RuntimeError("AGY config changed while MCP restoration was running")
            temporary.replace(path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


__all__ = ["AgyMcpRestorePorts", "AgyMcpRestoreService"]
