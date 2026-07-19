"""Ownership repository and lifecycle for MCP channel notification streams."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ChannelProxyOwnershipRepository:
    def __init__(
        self,
        proxy_config: Path,
        disable_notifications: Callable[[dict[str, Any]], bool],
        log: Callable[[str, str], None],
    ) -> None:
        self.proxy_config = proxy_config
        self.disable_notifications = disable_notifications
        self.log = log

    def owned_names(self) -> set[str]:
        try:
            data = json.loads(self.proxy_config.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return set()
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
            self.log(
                "WARN",
                f"proxy_owned_channel_config_read_failed path={self.proxy_config} "
                f"error={type(exc).__name__}: {exc}",
            )
            return set()
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if not isinstance(servers, dict):
            return set()
        owned: set[str] = set()
        for name, entry in servers.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("args"), list):
                continue
            arguments = [str(argument) for argument in entry["args"]]
            if "mcp-proxy" not in arguments or "--server-name" not in arguments:
                continue
            try:
                wrapped_name = arguments[arguments.index("--server-name") + 1].strip()
            except IndexError:
                wrapped_name = str(name).strip()
            if self.server_config_disables_notifications(arguments):
                continue
            if wrapped_name:
                owned.add(wrapped_name)
        return owned

    def server_config_disables_notifications(self, arguments: list[str]) -> bool:
        try:
            path = Path(arguments[arguments.index("--server-config") + 1])
        except (ValueError, IndexError):
            return False
        try:
            server = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
            self.log(
                "WARN",
                f"proxy_server_config_read_failed path={path} "
                f"error={type(exc).__name__}: {exc}",
            )
            return False
        return (
            self.disable_notifications(server) if isinstance(server, dict) else False
        )


@dataclass(frozen=True, slots=True)
class ChannelRouterLifecyclePorts:
    delivery_enabled: Callable[..., bool]
    launch_specs: Callable[[dict[str, Any], list[str]], list[str]]
    server_names: Callable[[Iterable[str]], list[str]]
    owned_names: Callable[[], set[str]]
    public_name: Callable[[str], str]
    ensure_probe: Callable[..., bool]
    source_paths: Callable[[Iterable[str]], list[Path]]
    auto_start: Callable[..., list[dict[str, Any]]]
    log: Callable[[str, str], None]


class ChannelRouterLifecycle:
    def __init__(
        self,
        native_router_names: frozenset[str],
        ports: ChannelRouterLifecyclePorts,
    ) -> None:
        self.native_router_names = native_router_names
        self.ports = ports

    def managed_names(self, config: dict[str, Any]) -> list[str]:
        names = self.ports.server_names(self.ports.launch_specs(config, []))
        names = [name for name in names if name.lower() not in self.native_router_names]
        owned = self.ports.owned_names()
        if not owned:
            return names
        owned_public = {self.ports.public_name(name) for name in owned}
        kept = [
            name for name in names if self.ports.public_name(name) not in owned_public
        ]
        if len(kept) != len(names):
            dropped = sorted(set(names) - set(kept))
            self.ports.log(
                "INFO",
                "router_channel_sse_skipped_proxy_owned "
                f"servers={','.join(dropped) or '-'}",
            )
        return kept

    def start(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.ports.delivery_enabled(True, [], config):
            return []
        names = self.managed_names(config)
        if not names:
            self.ports.log(
                "INFO", "router_channel_sse_skipped reason=no_external_channel_specs"
            )
            return []
        source_paths: list[Path] = []
        try:
            self.ports.ensure_probe(config, [])
            source_paths = self.ports.source_paths(
                [f"server:{name}" for name in names]
            )
        except Exception as exc:
            self.ports.log(
                "WARN",
                f"router_channel_probe_cache_failed "
                f"error={type(exc).__name__}: {exc}",
            )
        started = self.ports.auto_start(
            [],
            extra_config_paths=source_paths,
            allowed_server_names=list(names),
        )
        self.ports.log(
            "INFO",
            f"router_channel_sse_started count={len(started)} servers="
            + (
                ",".join(str(item.get("name") or "") for item in started) or "-"
            ),
        )
        return started
