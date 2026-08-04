"""MCP configuration discovery and native-config projection context."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .mcp_config_reader import ClaudeMcpConfigPathPolicy
from .managed_mcp_discovery import (
    ManagedMcpDiscoveryPaths,
    ManagedMcpDiscoveryPorts,
    ManagedMcpDiscoveryService,
    NativeMcpConfigWriter,
    NativeMcpConfigWriterPorts,
)


class JsonArtifactRepository(Protocol):
    def save(self, data: Any, operation: str) -> None: ...


@dataclass(frozen=True, slots=True)
class McpConfigurationFilePorts:
    read_items: Callable[..., list[Any]]
    names_from_mapping: Callable[..., Any]
    servers_from_mapping: Callable[..., Any]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class McpConfigurationPaths:
    home: Path
    web_tools: Path
    proxy: Path
    native_config: Path


@dataclass(frozen=True, slots=True)
class McpConfigurationRuntimePorts:
    resolve_executable: Callable[[str], str]
    parse_bool: Callable[..., bool]
    artifact_repository: Callable[[Path], JsonArtifactRepository]
    discover_channel_specs: Callable[..., list[str]]
    is_channel_tagged: Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class McpConfigurationContext:
    files: McpConfigurationFilePorts
    paths: McpConfigurationPaths
    runtime: McpConfigurationRuntimePorts
    native_channel_names: frozenset[str]

    def read_server_names(self, path: Path, cwd: Path) -> list[str]:
        return self.files.read_items(
            path,
            cwd,
            self.files.names_from_mapping,
            str,
            self.files.log,
        )

    def read_servers(
        self, path: Path, cwd: Path
    ) -> list[tuple[str, dict[str, Any]]]:
        return self.files.read_items(
            path,
            cwd,
            self.files.servers_from_mapping,
            lambda item: item[0],
            self.files.log,
        )

    def server_is_stdio(self, server: dict[str, Any]) -> bool:
        if not isinstance(server, dict):
            return False
        server_type = str(server.get("type") or "").strip().lower()
        if server_type and server_type not in ("stdio", "command"):
            return False
        command = self.runtime.resolve_executable(
            str(server.get("command") or "").strip()
        )
        if not command:
            return False
        raw_args = server.get("args", [])
        args = (
            [str(item) for item in raw_args if item is not None]
            if isinstance(raw_args, list)
            else []
        )
        return "mcp-proxy" not in args

    @staticmethod
    def server_is_streamable_http(server: dict[str, Any]) -> bool:
        if not isinstance(server, dict):
            return False
        server_type = str(
            server.get("type") or server.get("transport") or ""
        ).strip().lower()
        if server_type not in {"http", "streamable-http"}:
            return False
        url = str(server.get("url") or server.get("endpoint") or "").strip()
        return url.startswith(("http://", "https://"))

    def server_force_proxy(self, server: dict[str, Any]) -> bool:
        if not isinstance(server, dict):
            return False
        return self.runtime.parse_bool(
            server.get(
                "ciel_runtime_mcp_proxy",
                server.get(
                    "ciel_runtime_force_mcp_proxy",
                    server.get("force_mcp_proxy", False),
                ),
            ),
            False,
        )

    def server_disables_proxy_notifications(self, server: dict[str, Any]) -> bool:
        if not isinstance(server, dict):
            return False
        return self.runtime.parse_bool(
            server.get(
                "ciel_runtime_disable_notification_stream",
                server.get("ciel_runtime_disable_mcp_notifications", False),
            ),
            False,
        )

    @staticmethod
    def safe_proxy_name(name: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
        return safe[:80] or "server"

    def config_paths(
        self,
        passthrough: list[str] | None = None,
        cwd: Path | None = None,
        home: Path | None = None,
    ) -> list[Path]:
        return ClaudeMcpConfigPathPolicy.paths(
            passthrough or [], cwd or Path.cwd(), home or self.paths.home
        )

    def existing_config_paths(
        self,
        passthrough: list[str] | None = None,
        cwd: Path | None = None,
        home: Path | None = None,
    ) -> list[Path]:
        return ClaudeMcpConfigPathPolicy.existing_paths(
            passthrough or [], cwd or Path.cwd(), home or self.paths.home
        )

    def discover_user_servers(
        self,
        passthrough: list[str] | None = None,
        cwd: Path | None = None,
        home: Path | None = None,
    ) -> dict[str, dict[str, Any]]:
        working_directory = cwd or Path.cwd()
        servers: dict[str, dict[str, Any]] = {}
        for path in self.existing_config_paths(
            passthrough, working_directory, home
        ):
            for name, server in self.read_servers(path, working_directory):
                servers.setdefault(name, server)
        return servers

    def read_generated_servers(
        self, path: Path, cwd: Path
    ) -> dict[str, dict[str, Any]]:
        if not path.exists() or not path.is_file():
            return {}
        servers: dict[str, dict[str, Any]] = {}
        for name, server in self.read_servers(path, cwd):
            if name.strip().lower() in self.native_channel_names:
                continue
            servers.setdefault(name, server)
        return servers

    def discover_managed_servers(
        self, cwd: Path | None = None
    ) -> dict[str, dict[str, Any]]:
        service = ManagedMcpDiscoveryService(
            paths=ManagedMcpDiscoveryPaths(
                web_tools=self.paths.web_tools,
                proxy=self.paths.proxy,
            ),
            ports=ManagedMcpDiscoveryPorts(
                read_generated=self.read_generated_servers,
                load_json=lambda path: json.loads(path.read_text(encoding="utf-8")),
                log=self.files.log,
            ),
            native_channel_names=frozenset(
                name.casefold() for name in self.native_channel_names
            ),
        )
        return service.discover(cwd or Path.cwd())

    def write_native_config(
        self,
        passthrough: list[str] | None = None,
        cwd: Path | None = None,
        home: Path | None = None,
    ) -> Path | None:
        writer = NativeMcpConfigWriter(
            self.paths.native_config,
            NativeMcpConfigWriterPorts(
                self.discover_user_servers,
                self.discover_managed_servers,
                lambda path, data, operation: self.runtime.artifact_repository(
                    path
                ).save(data, operation),
                self.files.log,
            ),
        )
        return writer.write(passthrough, cwd, home)

    def auto_discovered_channel_specs(
        self,
        passthrough: list[str] | None = None,
        cwd: Path | None = None,
        home: Path | None = None,
    ) -> list[str]:
        working_directory = cwd or Path.cwd()
        return self.runtime.discover_channel_specs(
            self.config_paths(passthrough, working_directory, home),
            working_directory,
            self.read_server_names,
            self.runtime.is_channel_tagged,
        )


@dataclass(frozen=True, slots=True)
class McpConfigurationCompatibilityApi:
    context: Callable[[], McpConfigurationContext]

    def read_server_names(self, *args: Any, **kwargs: Any) -> list[str]:
        return self.context().read_server_names(*args, **kwargs)

    def read_servers(self, *args: Any, **kwargs: Any) -> list[Any]:
        return self.context().read_servers(*args, **kwargs)

    def server_is_stdio(self, server: dict[str, Any]) -> bool:
        return self.context().server_is_stdio(server)

    def server_is_streamable_http(self, server: dict[str, Any]) -> bool:
        return self.context().server_is_streamable_http(server)

    def server_force_proxy(self, server: dict[str, Any]) -> bool:
        return self.context().server_force_proxy(server)

    def server_disables_proxy_notifications(self, server: dict[str, Any]) -> bool:
        return self.context().server_disables_proxy_notifications(server)

    def safe_proxy_name(self, name: str) -> str:
        return self.context().safe_proxy_name(name)

    def config_paths(self, *args: Any, **kwargs: Any) -> list[Path]:
        return self.context().config_paths(*args, **kwargs)

    def existing_config_paths(self, *args: Any, **kwargs: Any) -> list[Path]:
        return self.context().existing_config_paths(*args, **kwargs)

    def discover_user_servers(self, *args: Any, **kwargs: Any) -> dict[str, dict[str, Any]]:
        return self.context().discover_user_servers(*args, **kwargs)

    def read_generated_servers(self, *args: Any, **kwargs: Any) -> dict[str, dict[str, Any]]:
        return self.context().read_generated_servers(*args, **kwargs)

    def discover_managed_servers(self, *args: Any, **kwargs: Any) -> dict[str, dict[str, Any]]:
        return self.context().discover_managed_servers(*args, **kwargs)

    def write_native_config(self, *args: Any, **kwargs: Any) -> Path | None:
        return self.context().write_native_config(*args, **kwargs)

    def auto_discovered_channel_specs(self, *args: Any, **kwargs: Any) -> list[str]:
        return self.context().auto_discovered_channel_specs(*args, **kwargs)


__all__ = [
    "McpConfigurationCompatibilityApi",
    "McpConfigurationContext",
    "McpConfigurationFilePorts",
    "McpConfigurationPaths",
    "McpConfigurationRuntimePorts",
]
