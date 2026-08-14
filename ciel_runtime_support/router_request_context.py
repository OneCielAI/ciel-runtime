"""Runtime protocol request handling and router selection bounded context."""

from __future__ import annotations

import urllib.error
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable

from .agent_router import (
    RuntimeRouter,
    missing_common_capabilities,
    router_capability_matrix,
)
from .claude_router import ClaudeRouter, ClaudeRouterServices
from .codex_router import CodexRouter
from .openai_chat_router import OpenAIChatRouter
from .openai_responses_router import (
    OpenAIResponsesServices,
    handle_openai_responses_request,
)


@dataclass(frozen=True, slots=True)
class RouterRequestPorts:
    openai_responses: OpenAIResponsesServices
    forward_backend_json: Callable[..., Any]
    forward_backend_get: Callable[..., Any]
    write_responses_error: Callable[..., Any]
    write_json: Callable[..., Any]
    upstream_error_message: Callable[..., str]
    is_client_disconnect: Callable[[BaseException], bool]


@dataclass(frozen=True, slots=True)
class RuntimeRouterPorts:
    codex_routed_enabled: Callable[..., bool]
    forward_provider_chat: Callable[..., Any]
    claude_services: ClaudeRouterServices


@dataclass(frozen=True, slots=True)
class RouterRequestContext:
    request: RouterRequestPorts
    runtime: RuntimeRouterPorts

    def handle_openai_responses_post(
        self,
        handler: BaseHTTPRequestHandler,
        cfg: dict[str, Any],
        provider: str,
        pcfg: dict[str, Any],
        body: dict[str, Any],
    ) -> None:
        handle_openai_responses_request(
            handler,
            cfg,
            provider,
            pcfg,
            body,
            self.request.openai_responses,
        )

    def handle_codex_backend_passthrough_post(
        self,
        handler: BaseHTTPRequestHandler,
        provider: str,
        pcfg: dict[str, Any],
        body: dict[str, Any],
    ) -> None:
        try:
            self.request.forward_backend_json(
                handler,
                provider,
                pcfg,
                body,
                mutate_responses=False,
            )
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="ignore")
            self.request.write_responses_error(
                handler,
                self.request.upstream_error_message(exc, raw),
                stream=False,
                status=exc.code,
                error_type=(
                    "authentication_error"
                    if exc.code == 401
                    else "permission_error"
                    if exc.code == 403
                    else "request_too_large"
                    if exc.code == 413
                    else "api_error"
                ),
            )
        except Exception as exc:
            if self.request.is_client_disconnect(exc):
                return
            self.request.write_responses_error(
                handler,
                f"{type(exc).__name__}: {exc}",
                stream=False,
            )

    def handle_codex_backend_passthrough_get(
        self,
        handler: BaseHTTPRequestHandler,
        provider: str,
        pcfg: dict[str, Any],
    ) -> None:
        try:
            self.request.forward_backend_get(handler, provider, pcfg)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="ignore")
            self.request.write_json(
                handler,
                {"error": {"message": self.request.upstream_error_message(exc, raw)}},
                status=exc.code,
            )
        except Exception as exc:
            if self.request.is_client_disconnect(exc):
                return
            self.request.write_json(
                handler,
                {"error": {"message": f"{type(exc).__name__}: {exc}"}},
                status=502,
            )

    def build_claude_router_services(self) -> ClaudeRouterServices:
        return self.runtime.claude_services

    def build_runtime_routers(self) -> tuple[RuntimeRouter, ...]:
        return (
            CodexRouter(
                routed_enabled=self.runtime.codex_routed_enabled,
                handle_responses_post=self.handle_openai_responses_post,
                handle_backend_passthrough_post=(
                    self.handle_codex_backend_passthrough_post
                ),
                handle_backend_passthrough_get=(
                    self.handle_codex_backend_passthrough_get
                ),
            ),
            OpenAIChatRouter(self.runtime.forward_provider_chat),
            ClaudeRouter(services=self.runtime.claude_services),
        )

    def capability_matrix(self) -> dict[str, dict[str, Any]]:
        return router_capability_matrix(self.build_runtime_routers())

    def capability_gaps(self) -> dict[str, list[str]]:
        return missing_common_capabilities(self.build_runtime_routers())

    def route_get(
        self,
        handler: BaseHTTPRequestHandler,
        path: str,
        provider: str,
        pcfg: dict[str, Any],
    ) -> bool:
        for router in self.build_runtime_routers():
            if router.can_handle_get(path, provider, pcfg):
                return bool(router.handle_get(handler, path, provider, pcfg))
        return False

    def route_post(
        self,
        handler: BaseHTTPRequestHandler,
        cfg: dict[str, Any],
        provider: str,
        pcfg: dict[str, Any],
        path: str,
        body: dict[str, Any],
    ) -> bool:
        for router in self.build_runtime_routers():
            if router.can_handle_post(path, provider, pcfg):
                return bool(
                    router.handle_post(handler, cfg, provider, pcfg, path, body)
                )
        return False


@dataclass(frozen=True, slots=True)
class RouterRequestCompatibilityApi:
    context: Callable[[], RouterRequestContext]

    def handle_openai_responses_post(self, *args: Any, **kwargs: Any) -> None:
        self.context().handle_openai_responses_post(*args, **kwargs)

    def handle_codex_backend_passthrough_post(
        self, *args: Any, **kwargs: Any
    ) -> None:
        self.context().handle_codex_backend_passthrough_post(*args, **kwargs)

    def handle_codex_backend_passthrough_get(
        self, *args: Any, **kwargs: Any
    ) -> None:
        self.context().handle_codex_backend_passthrough_get(*args, **kwargs)

    def build_claude_router_services(self) -> ClaudeRouterServices:
        return self.context().build_claude_router_services()

    def build_runtime_routers(self) -> tuple[RuntimeRouter, ...]:
        return self.context().build_runtime_routers()

    def capability_matrix(self) -> dict[str, dict[str, Any]]:
        return self.context().capability_matrix()

    def capability_gaps(self) -> dict[str, list[str]]:
        return self.context().capability_gaps()

    def route_get(self, *args: Any, **kwargs: Any) -> bool:
        return self.context().route_get(*args, **kwargs)

    def route_post(self, *args: Any, **kwargs: Any) -> bool:
        return self.context().route_post(*args, **kwargs)


__all__ = [
    "RouterRequestCompatibilityApi",
    "RouterRequestContext",
    "RouterRequestPorts",
    "RuntimeRouterPorts",
]
