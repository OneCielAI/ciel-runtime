"""Discovery of MCP servers restored from runtime-managed artifacts."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ManagedMcpDiscoveryPaths:
    web_tools: Path
    proxy: Path


@dataclass(frozen=True, slots=True)
class ManagedMcpDiscoveryPorts:
    read_generated: Callable[[Path, Path], dict[str, dict[str, Any]]]
    load_json: Callable[[Path], Any]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ManagedMcpDiscoveryService:
    paths: ManagedMcpDiscoveryPaths
    ports: ManagedMcpDiscoveryPorts
    native_channel_names: frozenset[str]

    def discover(self, cwd: Path) -> dict[str, dict[str, Any]]:
        servers = self.ports.read_generated(self.paths.web_tools, cwd)
        if not self.paths.proxy.exists() or not self.paths.proxy.is_file():
            return servers
        try:
            proxy_data = self.ports.load_json(self.paths.proxy)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
            self.ports.log(
                "WARN",
                f"managed_mcp_proxy_config_read_failed path={self.paths.proxy} "
                f"error={type(exc).__name__}: {exc}",
            )
            return servers
        proxy_servers = (
            proxy_data.get("mcpServers")
            if isinstance(proxy_data, dict)
            else None
        )
        if not isinstance(proxy_servers, dict):
            return servers
        for raw_name, raw_entry in proxy_servers.items():
            name = str(raw_name or "").strip()
            if (
                not name
                or name.casefold() in self.native_channel_names
                or not isinstance(raw_entry, dict)
            ):
                continue
            restored = self._restore_proxy_entry(name, raw_entry)
            if restored is None:
                continue
            restored_name, server = restored
            if restored_name.casefold() not in self.native_channel_names:
                servers.setdefault(restored_name, server)
        return servers

    def _restore_proxy_entry(
        self, name: str, entry: dict[str, Any]
    ) -> tuple[str, dict[str, Any]] | None:
        args = entry.get("args")
        if not isinstance(args, list):
            return name, dict(entry)
        arguments = [str(item) for item in args]
        if "mcp-proxy" not in arguments or "--server-config" not in arguments:
            return name, dict(entry)
        try:
            config_path = Path(
                arguments[arguments.index("--server-config") + 1]
            ).expanduser()
            wrapped_name = (
                arguments[arguments.index("--server-name") + 1].strip()
                if "--server-name" in arguments
                and arguments.index("--server-name") + 1 < len(arguments)
                else name
            )
            wrapped_server = self.ports.load_json(config_path)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            self.ports.log(
                "WARN",
                f"managed_mcp_wrapped_config_read_failed server={name} "
                f"error={type(exc).__name__}: {exc}",
            )
            return None
        if not wrapped_name or not isinstance(wrapped_server, dict):
            return None
        restored = dict(wrapped_server)
        restored.pop("ciel_runtime_disable_notification_stream", None)
        return wrapped_name, restored
