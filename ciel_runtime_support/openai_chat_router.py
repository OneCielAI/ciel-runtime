"""OpenAI Chat Completions runtime HTTP router."""

from __future__ import annotations

from typing import Any, Callable

from .agent_router import COMMON_RUNTIME_ROUTER_CAPABILITIES, RouterCapability
from .remote_bridge import is_remote_bridge_request


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
            ("protocol_translation", "Remote non-Chat models are collected and projected onto Chat Completions."),
            ("channel_context_injection", "Native Chat Completions message context is preserved."),
            ("pending_delivery_ack", "Requests without pending channel delivery require no acknowledgement."),
            ("request_observability", "Requests use the shared runtime HTTP request/error boundary."),
            ("upstream_error_mapping", "Upstream HTTP errors use the shared router error response."),
        )
    )

    def __init__(
        self,
        forward: Callable[..., None],
        select_protocol: Callable[..., str] = lambda *_args: "openai_chat",
        write_json: Callable[..., Any] = lambda *_args, **_kwargs: None,
        requires_streaming: Callable[..., bool] = lambda *_args: False,
        forward_compatible: Callable[..., Any] | None = None,
    ) -> None:
        self._forward = forward
        self._select_protocol = select_protocol
        self._write_json = write_json
        self._requires_streaming = requires_streaming
        self._forward_compatible = forward_compatible

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
        model = str(body.get("model") or config.get("current_model") or "")
        selected_protocol = self._select_protocol(
            provider, config, "openai_chat", model
        )
        if selected_protocol != "openai_chat":
            if (
                is_remote_bridge_request(handler)
                and self._forward_compatible is not None
            ):
                self._forward_compatible(
                    handler,
                    provider,
                    config,
                    body,
                    selected_protocol,
                )
                return True
            self._write_json(
                handler,
                {
                    "error": {
                        "message": (
                            f"Provider '{provider}' does not support the "
                            "OpenAI Chat Completions wire protocol for this model"
                        ),
                        "type": "unsupported_feature",
                        "param": "model",
                        "code": "unsupported_feature",
                    }
                },
                status=501,
            )
            return True
        if self._requires_streaming(provider, config) and not bool(
            body.get("stream", False)
        ):
            self._write_json(
                handler,
                {
                    "error": {
                        "message": (
                            f"Provider '{provider}' requires streaming; use "
                            "/v1/responses for a non-streaming compatible response"
                        ),
                        "type": "unsupported_feature",
                        "param": "stream",
                        "code": "unsupported_feature",
                    }
                },
                status=501,
            )
            return True
        self._forward(handler, provider, config, body)
        return True


assert all(
    any(capability.name == required for capability in OpenAIChatRouter.capabilities)
    for required in COMMON_RUNTIME_ROUTER_CAPABILITIES
)


__all__ = ["OpenAIChatRouter"]
