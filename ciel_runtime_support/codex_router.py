"""Codex runtime HTTP router."""

from __future__ import annotations

from typing import Any, Callable

from .agent_router import COMMON_RUNTIME_ROUTER_CAPABILITIES, RouterCapability


class CodexRouter:
    name = "codex"
    runtime = "codex"
    protocol = "openai_responses"
    request_paths = ("/backend-api/codex/*", "/backend-api/codex/responses", "/v1/responses")
    capabilities = tuple(
        RouterCapability(name, description)
        for name, description in (
            ("auth_forwarding", "Native Codex auth headers are forwarded to the ChatGPT Codex backend."),
            ("sse_stream_proxy", "Responses API SSE streams are proxied without buffering the full response."),
            ("channel_context_injection", "Pending external channel messages are injected into Responses input."),
            ("pending_delivery_ack", "Injected channel cursors are committed after successful delivery."),
            ("request_observability", "Responses requests are traced and published to the runtime event bus."),
            ("upstream_error_mapping", "Upstream HTTP and client disconnect errors are mapped for Codex."),
            ("backend_passthrough", "Non-responses Codex backend endpoints are passed through."),
            ("legacy_responses", "The legacy /v1/responses path remains supported."),
        )
    )

    def __init__(
        self,
        *,
        routed_enabled: Callable[[str, dict[str, Any]], bool],
        handle_responses_post: Callable[[Any, dict[str, Any], str, dict[str, Any], dict[str, Any]], None],
        handle_backend_passthrough_post: Callable[[Any, str, dict[str, Any], dict[str, Any]], None],
        handle_backend_passthrough_get: Callable[[Any, str, dict[str, Any]], None],
    ) -> None:
        self._routed_enabled = routed_enabled
        self._handle_responses_post = handle_responses_post
        self._handle_backend_passthrough_post = handle_backend_passthrough_post
        self._handle_backend_passthrough_get = handle_backend_passthrough_get

    def can_handle_get(self, path: str, provider: str, pcfg: dict[str, Any]) -> bool:
        return self._routed_enabled(provider, pcfg) and path.startswith("/backend-api/codex/")

    def handle_get(self, handler: Any, path: str, provider: str, pcfg: dict[str, Any]) -> bool:
        if not self.can_handle_get(path, provider, pcfg):
            return False
        self._handle_backend_passthrough_get(handler, provider, pcfg)
        return True

    def can_handle_post(self, path: str, provider: str, pcfg: dict[str, Any]) -> bool:
        if path == "/v1/responses":
            return True
        return self._routed_enabled(provider, pcfg) and path.startswith("/backend-api/codex/")

    def handle_post(
        self,
        handler: Any,
        cfg: dict[str, Any],
        provider: str,
        pcfg: dict[str, Any],
        path: str,
        body: dict[str, Any],
    ) -> bool:
        if path == "/v1/responses":
            self._handle_responses_post(handler, cfg, provider, pcfg, body)
            return True
        if path == "/backend-api/codex/responses" and self._routed_enabled(provider, pcfg):
            self._handle_responses_post(handler, cfg, provider, pcfg, body)
            return True
        if self._routed_enabled(provider, pcfg) and path.startswith("/backend-api/codex/"):
            self._handle_backend_passthrough_post(handler, provider, pcfg, body)
            return True
        return False


assert all(any(capability.name == required for capability in CodexRouter.capabilities) for required in COMMON_RUNTIME_ROUTER_CAPABILITIES)


__all__ = ["CodexRouter"]
