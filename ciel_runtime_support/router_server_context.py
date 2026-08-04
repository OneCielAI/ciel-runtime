"""Router HTTP presentation and server lifecycle bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .router_http import RouterHttpServices
from .router_server_runtime import RouterServerRuntime


@dataclass(frozen=True, slots=True)
class RouterHealthPresentationPorts:
    version: str
    source_fingerprint: str
    current_pid: Callable[[], int]
    current_user: Callable[[], str]
    home: Path
    config_dir: Path
    router_port: int
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
        return {
            "ok": True,
            "version": self.health.version,
            "source_fingerprint": self.health.source_fingerprint,
            "pid": self.health.current_pid(),
            "user": self.health.current_user(),
            "home": str(self.health.home),
            "config_dir": str(self.health.config_dir),
            "router_port": self.health.router_port,
            "provider": provider,
            "model": self.health.current_alias(cfg),
            "web_chat": "/ca/web/chat",
            "chat": "/ca/chat/health",
            "plan": "/ca/plan/artifacts",
            "events": "/ca/events",
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
    "RouterServerCompatibilityApi",
    "RouterServerContext",
]
