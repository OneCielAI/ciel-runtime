"""OpenAI Chat Completions runtime HTTP router."""

from __future__ import annotations

from typing import Any, Callable

from .agent_router import COMMON_RUNTIME_ROUTER_CAPABILITIES, RouterCapability


class OpenAIChatRouter:
    name = "openai-chat"
    runtime = "openai-compatible-cli"
    protocol = "openai_chat"
    request_paths = ("/v1/chat/completions",)
    capabilities = tuple(
        RouterCapability(name, description)
        for name, description in (
            ("auth_forwarding", "Provider authentication replaces the local CLI placeholder key."),
            ("sse_stream_proxy", "Chat Completions SSE bytes are streamed without protocol conversion."),
            ("channel_context_injection", "Native Chat Completions message context is preserved."),
            ("pending_delivery_ack", "Requests without pending channel delivery require no acknowledgement."),
            ("request_observability", "Requests use the shared runtime HTTP request/error boundary."),
            ("upstream_error_mapping", "Upstream HTTP errors use the shared router error response."),
        )
    )

    def __init__(self, forward: Callable[..., None]) -> None:
        self._forward = forward

    def can_handle_get(self, path: str, provider: str, config: dict[str, Any]) -> bool:
        del path, provider, config
        return False

    def handle_get(self, handler: Any, path: str, provider: str, config: dict[str, Any]) -> bool:
        del handler, path, provider, config
        return False

    def can_handle_post(self, path: str, provider: str, config: dict[str, Any]) -> bool:
        del provider, config
        return path in self.request_paths

    def handle_post(
        self,
        handler: Any,
        config_root: dict[str, Any],
        provider: str,
        config: dict[str, Any],
        path: str,
        body: dict[str, Any],
    ) -> bool:
        del config_root
        if path not in self.request_paths:
            return False
        self._forward(handler, provider, config, body)
        return True


assert all(
    any(capability.name == required for capability in OpenAIChatRouter.capabilities)
    for required in COMMON_RUNTIME_ROUTER_CAPABILITIES
)


__all__ = ["OpenAIChatRouter"]
