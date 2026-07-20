"""Configuration service materializing MCP servers through local proxy commands."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class McpProxyConfigPaths:
    output: Path
    server_directory: Path
    entrypoint: Path


@dataclass(frozen=True, slots=True)
class McpProxyConfigPorts:
    config_paths: Callable[[list[str], Path, Path | None], list[Path]]
    read_servers: Callable[[Path, Path], list[tuple[str, dict[str, Any]]]]
    is_streamable_http: Callable[[dict[str, Any]], bool]
    force_proxy: Callable[[dict[str, Any]], bool]
    is_stdio: Callable[[dict[str, Any]], bool]
    safe_name: Callable[[str], str]
    save_json: Callable[[Path, dict[str, Any], str], None]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class McpProxyConfigService:
    paths: McpProxyConfigPaths
    ports: McpProxyConfigPorts

    def write(
        self,
        passthrough: list[str],
        *,
        extra_config_paths: list[Path | str] | None = None,
        force_proxy_server_names: set[str] | None = None,
        disable_proxy_notification_stream_names: set[str] | None = None,
        cwd: Path | None = None,
        home: Path | None = None,
    ) -> Path | None:
        working_directory = cwd or Path.cwd()
        forced_names = set(force_proxy_server_names or ())
        disabled_stream_names = set(disable_proxy_notification_stream_names or ())
        extra = [Path(item).expanduser() for item in (extra_config_paths or [])]
        config_paths = [*extra, *self.ports.config_paths(passthrough, working_directory, home)]
        servers: dict[str, Any] = {}
        for path in config_paths:
            if not path.exists() or not path.is_file():
                continue
            for name, server in self.ports.read_servers(path, working_directory):
                if name in servers:
                    self.ports.log(
                        "INFO",
                        f"mcp_proxy_config_duplicate_overwritten server={name} source={path}",
                    )
                streamable = self.ports.is_streamable_http(server)
                forced_streamable = streamable and (
                    name in forced_names or self.ports.force_proxy(server)
                )
                if self.ports.is_stdio(server) or forced_streamable:
                    servers[name] = self._proxy_server(
                        name,
                        server,
                        disable_notification_stream=streamable and name in disabled_stream_names,
                    )
                else:
                    servers[name] = server
        if not servers:
            return None
        self.ports.save_json(
            self.paths.output,
            {"mcpServers": servers},
            "mcp_proxy_config",
        )
        self.ports.log("INFO", f"mcp_proxy_config_written servers={','.join(sorted(servers))}")
        return self.paths.output

    def _proxy_server(
        self,
        name: str,
        server: dict[str, Any],
        *,
        disable_notification_stream: bool,
    ) -> dict[str, Any]:
        self.paths.server_directory.mkdir(parents=True, exist_ok=True)
        server_path = self.paths.server_directory / f"{self.ports.safe_name(name)}.json"
        saved_server = dict(server)
        if disable_notification_stream:
            saved_server["ciel_runtime_disable_notification_stream"] = True
        self.ports.save_json(server_path, saved_server, f"mcp_proxy_server:{name}")
        return {
            "command": sys.executable,
            "args": [
                str(self.paths.entrypoint),
                "mcp-proxy",
                "--server-name",
                name,
                "--server-config",
                str(server_path),
            ],
        }


__all__ = ["McpProxyConfigPaths", "McpProxyConfigPorts", "McpProxyConfigService"]
