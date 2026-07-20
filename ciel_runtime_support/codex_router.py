"""Codex runtime HTTP router."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from .agent_router import COMMON_RUNTIME_ROUTER_CAPABILITIES, RouterCapability


CODEX_CAPACITY_ERROR_CODES = frozenset({"server_is_overloaded", "slow_down"})
CODEX_RESPONSE_PREAMBLE_LIMIT = 256 * 1024
_CODEX_NON_OUTPUT_EVENT_TYPES = frozenset({"response.created", "response.in_progress"})


@dataclass(frozen=True)
class CodexResponsePreamble:
    payload: bytes
    capacity_error_code: str | None = None


@dataclass(frozen=True, slots=True)
class CodexChannelContextPorts:
    to_anthropic: Callable[[dict[str, Any], str], dict[str, Any]]
    inject_pending: Callable[[dict[str, Any]], dict[str, Any]]
    inject_tool_results: Callable[[dict[str, Any]], dict[str, Any]]
    content_to_text: Callable[[Any], str]


class CodexChannelContextProjector:
    def __init__(self, ports: CodexChannelContextPorts) -> None:
        self._ports = ports

    @staticmethod
    def input_items(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, str):
            return [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": value}]}]
        if isinstance(value, dict):
            return [dict(value)]
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def message_item(role: str, text: str) -> dict[str, Any]:
        role = role if role in {"user", "assistant", "system", "developer"} else "user"
        text_type = "output_text" if role == "assistant" else "input_text"
        return {"type": "message", "role": role, "content": [{"type": text_type, "text": text}]}

    def project(self, body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        delivery = self._ports.to_anthropic(body, str(body.get("model") or ""))
        original_count = len(delivery.get("messages") or [])
        delivery = self._ports.inject_pending(delivery)
        delivery = self._ports.inject_tool_results(delivery)
        messages = [message for message in delivery.get("messages") or [] if isinstance(message, dict)]
        additions = messages[original_count:]
        metadata = delivery.get("metadata") if isinstance(delivery.get("metadata"), dict) else {}
        projected = dict(body)
        projected.pop("metadata", None)
        if not additions and not metadata:
            return projected, delivery
        input_items = self.input_items(body.get("input", []))
        for message in additions:
            text = self._ports.content_to_text(message.get("content"))
            if text.strip():
                input_items.append(self.message_item(str(message.get("role") or "user"), text))
        if input_items:
            projected["input"] = input_items
        return projected, delivery


def _codex_sse_event(block: bytes) -> dict[str, Any] | None:
    text = block.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    data_lines = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    candidate = "\n".join(data_lines).strip() if data_lines else text
    try:
        value = json.loads(candidate)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _codex_capacity_error_code(event: dict[str, Any]) -> str | None:
    if event.get("type") != "response.failed":
        return None
    response = event.get("response")
    if not isinstance(response, dict):
        return None
    error = response.get("error")
    if not isinstance(error, dict):
        return None
    code = str(error.get("code") or "").strip()
    return code if code in CODEX_CAPACITY_ERROR_CODES else None


def read_codex_response_preamble(
    stream: Any,
    *,
    max_bytes: int = CODEX_RESPONSE_PREAMBLE_LIMIT,
) -> CodexResponsePreamble:
    """Read control-only SSE events until output starts or a safe retry is known."""
    buffered = bytearray()
    while len(buffered) < max_bytes:
        block = bytearray()
        reached_eof = False
        while len(buffered) + len(block) < max_bytes:
            remaining = max_bytes - len(buffered) - len(block)
            line = stream.readline(remaining + 1)
            if not line:
                reached_eof = True
                break
            block.extend(line)
            if line in (b"\n", b"\r\n"):
                break
            if len(block) > remaining:
                break
        if not block:
            return CodexResponsePreamble(bytes(buffered))
        buffered.extend(block)
        if len(buffered) >= max_bytes:
            return CodexResponsePreamble(bytes(buffered))

        event = _codex_sse_event(bytes(block))
        if event is None:
            # SSE comments and keepalives carry no model output.
            meaningful = [line for line in block.splitlines() if line and not line.startswith(b":")]
            if not meaningful and not reached_eof:
                continue
            return CodexResponsePreamble(bytes(buffered))

        capacity_code = _codex_capacity_error_code(event)
        if capacity_code:
            return CodexResponsePreamble(bytes(buffered), capacity_code)
        if event.get("type") not in _CODEX_NON_OUTPUT_EVENT_TYPES:
            return CodexResponsePreamble(bytes(buffered))
        if reached_eof:
            return CodexResponsePreamble(bytes(buffered))

    return CodexResponsePreamble(bytes(buffered))


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
            ("capacity_retry", "Capacity-only failures are retried before any output or tool call is delivered."),
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


__all__ = [
    "CODEX_CAPACITY_ERROR_CODES",
    "CodexChannelContextPorts",
    "CodexChannelContextProjector",
    "CodexResponsePreamble",
    "CodexRouter",
    "read_codex_response_preamble",
]
