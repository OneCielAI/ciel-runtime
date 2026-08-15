"""Router HTTP presentation and server lifecycle bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .router_http import RouterHttpServices
from .router_server_runtime import RouterServerRuntime


@dataclass(frozen=True, slots=True)
class RouterHealthRuntimePorts:
    current_pid: Callable[[], int]
    active_client_pids: Callable[[], list[int]]


@dataclass(frozen=True, slots=True)
class RouterHealthPresentationPorts:
    version: str
    source_fingerprint: str
    runtime: RouterHealthRuntimePorts
    current_user: Callable[[], str]
    home: Path
    config_dir: Path
    workspace: str
    router_port: int
    instance_id: str
    current_alias: Callable[[dict[str, Any]], str]


@dataclass(frozen=True, slots=True)
class RouterServerContext:
    health: RouterHealthPresentationPorts
    http_services: RouterHttpServices
    server_runtime: RouterServerRuntime

    def health_payload(
        self,
        cfg: dict[str, Any],
        provider: str,
        pcfg: dict[str, Any],
    ) -> dict[str, Any]:
        del pcfg
        active_clients = self.health.runtime.active_client_pids()
        return {
            "ok": True,
            "version": self.health.version,
            "source_fingerprint": self.health.source_fingerprint,
            "pid": self.health.runtime.current_pid(),
            "user": self.health.current_user(),
            "home": str(self.health.home),
            "config_dir": str(self.health.config_dir),
            "workspace": self.health.workspace,
            "router_port": self.health.router_port,
            "instance_id": self.health.instance_id,
            "active_client_count": len(active_clients),
            "active_client_pids": active_clients,
            "provider": provider,
            "model": self.health.current_alias(cfg),
            "web_chat": "/ca/web/chat",
            "web_chat_api": "/ca/web/chat/api",
            "speech": "/ca/speech/health",
            "chat": "/ca/chat/health",
            "plan": "/ca/plan/artifacts",
            "events": "/ca/events",
            "external_event_receivers": "/ca/events/receivers",
            "tui": "/ca/tui",
            "tui_status": "/ca/tui/status",
            "tui_stream": "/ca/tui/stream",
        }

    def build_http_services(self) -> RouterHttpServices:
        return self.http_services

    def serve(self, _: Any) -> None:
        self.server_runtime.run()


@dataclass(frozen=True, slots=True)
class RouterServerCompatibilityApi:
    context: Callable[[], RouterServerContext]

    def health_payload(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.context().health_payload(*args, **kwargs)

    def build_http_services(self) -> RouterHttpServices:
        return self.context().build_http_services()

    def serve(self, args: Any) -> None:
        self.context().serve(args)


__all__ = [
    "RouterHealthPresentationPorts",
    "RouterHealthRuntimePorts",
    "RouterServerCompatibilityApi",
    "RouterServerContext",
]
