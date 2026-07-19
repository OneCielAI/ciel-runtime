"""Discover and start HTTP MCP channel transports from configuration files."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ChannelMcpDiscoveryPorts:
    environment: Mapping[str, str]
    config_paths: Callable[..., list[Path]]
    path_key: Callable[[Path], str]
    read_config: Callable[[Path, Path], list[dict[str, Any]]]
    dedupe: Callable[[Iterable[str]], list[str]]
    native_router_names: frozenset[str]
    public_name: Callable[[str], str]
    start_connection: Callable[[dict[str, Any]], dict[str, Any]]
    log: Callable[[str, str], None]


class ChannelMcpDiscoveryService:
    def __init__(self, ports: ChannelMcpDiscoveryPorts) -> None:
        self.ports = ports

    def runtime_headers(self, server: dict[str, Any]) -> dict[str, str]:
        headers = server.get("headers")
        raw_headers = headers if isinstance(headers, dict) else {}
        projected = {
            str(key): str(value)
            for key, value in raw_headers.items()
            if str(key).strip() and value is not None
        }
        env_headers = server.get("env_http_headers")
        if isinstance(env_headers, dict):
            for raw_header, raw_environment_name in env_headers.items():
                header = str(raw_header or "").strip()
                environment_name = str(raw_environment_name or "").strip()
                value = self.ports.environment.get(environment_name)
                if header and environment_name and value:
                    projected[header] = value
        token = str(server.get("bearer_token") or server.get("token") or "").strip()
        token_environment = str(
            server.get("bearer_token_env_var") or server.get("token_env_var") or ""
        ).strip()
        if not token and token_environment:
            token = str(self.ports.environment.get(token_environment) or "").strip()
        if token and not any(key.lower() == "authorization" for key in projected):
            projected["Authorization"] = f"Bearer {token}"
        return projected

    def servers_from_mapping(self, mapping: Any) -> list[dict[str, Any]]:
        if not isinstance(mapping, dict):
            return []
        found: list[dict[str, Any]] = []
        for key in ("mcpServers", "servers"):
            servers = mapping.get(key)
            if not isinstance(servers, dict):
                continue
            for raw_name, raw_server in servers.items():
                projected = self._project_server(raw_name, raw_server)
                if projected is not None:
                    found.append(projected)
        return found

    def _project_server(
        self, raw_name: Any, raw_server: Any
    ) -> dict[str, Any] | None:
        name = str(raw_name or "").strip()
        if not name or not isinstance(raw_server, dict):
            return None
        url = str(raw_server.get("url") or raw_server.get("endpoint") or "").strip()
        if not url.startswith(("http://", "https://")):
            return None
        server_type = str(raw_server.get("type") or "").strip().lower()
        if server_type and server_type not in ("sse", "http", "streamable-http"):
            return None
        transport = (
            "streamable-http" if server_type in ("http", "streamable-http") else "sse"
        )
        requires_session = raw_server.get(
            "streamable_requires_session",
            raw_server.get(
                "require_session", raw_server.get("mcp_session_required", True)
            ),
        )
        return {
            "name": f"mcp-{name}",
            "url": url,
            "type": server_type or transport,
            "transport": transport,
            "headers": self.runtime_headers(raw_server),
            "channel": name,
            "sender_id": name,
            "recipient": "all",
            "mcp": True,
            "streamable_requires_session": requires_session,
        }

    def external_names(
        self,
        passthrough: list[str] | None = None,
        cwd: Path | None = None,
        home: Path | None = None,
        extra_config_paths: list[Path | str] | None = None,
    ) -> list[str]:
        cwd = cwd or Path.cwd()
        names: list[str] = []
        for path in self._paths(
            passthrough, cwd, home, extra_config_paths, include_default=True
        ):
            for server in self.ports.read_config(path, cwd):
                name = str(server.get("channel") or "").strip()
                if name and name.lower() not in self.ports.native_router_names:
                    names.append(name)
        return self.ports.dedupe(names)

    def auto_start(
        self,
        passthrough: list[str] | None = None,
        cwd: Path | None = None,
        home: Path | None = None,
        extra_config_paths: list[Path | str] | None = None,
        allowed_server_names: Iterable[str] | None = None,
        include_default_paths: bool = True,
    ) -> list[dict[str, Any]]:
        cwd = cwd or Path.cwd()
        allowed = (
            {
                self.ports.public_name(str(name or "").strip())
                for name in allowed_server_names
                if str(name or "").strip()
            }
            if allowed_server_names is not None
            else None
        )
        started: list[dict[str, Any]] = []
        for path in self._paths(
            passthrough,
            cwd,
            home,
            extra_config_paths,
            include_default=include_default_paths,
        ):
            for server in self.ports.read_config(path, cwd):
                public_name = self.ports.public_name(str(server.get("name") or ""))
                if allowed is not None and public_name not in allowed:
                    continue
                try:
                    status = self.ports.start_connection(server)
                    started.append(status)
                    self.ports.log(
                        "INFO",
                        f"channel_sse_auto_started name={status.get('name')} "
                        f"url={status.get('url')}",
                    )
                except Exception as exc:
                    self.ports.log(
                        "WARN",
                        f"channel_sse_auto_start_failed path={path} "
                        f"error={type(exc).__name__}: {exc}",
                    )
        return started

    def _paths(
        self,
        passthrough: list[str] | None,
        cwd: Path,
        home: Path | None,
        extra_config_paths: list[Path | str] | None,
        *,
        include_default: bool,
    ) -> list[Path]:
        paths = [Path(path).expanduser() for path in extra_config_paths or []]
        if include_default:
            paths.extend(self.ports.config_paths(passthrough, cwd, home))
        unique: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = self.ports.path_key(path)
            if key not in seen and path.is_file():
                seen.add(key)
                unique.append(path)
        return unique
