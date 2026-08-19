"""Codex runtime HTTP router."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from .agent_router import COMMON_RUNTIME_ROUTER_CAPABILITIES, RouterCapability


CODEX_CAPACITY_ERROR_CODES = frozenset({"server_is_overloaded", "slow_down"})
# The upstream reports an oversized turn the same way it reports overload: a
# 200 response whose first events are control-only, ending in response.failed.
# Retrying it unchanged cannot succeed, so it is classified apart from capacity.
CODEX_CONTEXT_ERROR_CODES = frozenset({"context_length_exceeded"})
CODEX_RESPONSE_PREAMBLE_LIMIT = 256 * 1024
# A bare `error` frame carries no model output and precedes response.failed in
# every captured refusal, so scanning must continue past it rather than treat it
# as the start of a reply.
_CODEX_NON_OUTPUT_EVENT_TYPES = frozenset(
    {"response.created", "response.in_progress", "error"}
)


@dataclass(frozen=True)
class CodexResponsePreamble:
    payload: bytes
    capacity_error_code: str | None = None
    context_error_code: str | None = None


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
        pending_messages = [
            message
            for message in delivery.get("messages") or []
            if isinstance(message, dict)
        ]
        pending_metadata = (
            delivery.get("metadata")
            if isinstance(delivery.get("metadata"), dict)
            else {}
        )
        channel_injected = bool(
            pending_metadata.get("ciel_runtime_channel_injected")
        )
        wake_replaced = bool(
            pending_metadata.get("ciel_runtime_channel_wake_replaced")
        )
        # inject_pending appends exactly one formatted channel batch.  When it
        # first removes a wake marker, slicing at original_count yields an
        # empty list (N - 1 + 1 == N), which was the native Codex data-loss
        # defect.  Capture the batch before later projections instead.
        channel_additions = (
            pending_messages[-1:]
            if channel_injected
            else pending_messages[original_count:]
        )
        pending_count = len(pending_messages)
        delivery = self._ports.inject_tool_results(delivery)
        messages = [message for message in delivery.get("messages") or [] if isinstance(message, dict)]
        tool_additions = messages[pending_count:]
        additions = [*channel_additions, *tool_additions]
        metadata = delivery.get("metadata") if isinstance(delivery.get("metadata"), dict) else {}
        projected = dict(body)
        projected.pop("metadata", None)
        if not additions and not metadata:
            return projected, delivery
        input_items = self.input_items(body.get("input", []))
        if wake_replaced and input_items:
            input_items.pop()
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


def _codex_failure_code(event: dict[str, Any]) -> str | None:
    """Read the failure code from either shape the upstream uses.

    A refusal arrives as a bare ``error`` frame and again inside the
    ``response.failed`` frame that follows it; both carry the same code.
    """

    event_type = event.get("type")
    if event_type == "error":
        error = event.get("error")
    elif event_type == "response.failed":
        response = event.get("response")
        error = response.get("error") if isinstance(response, dict) else None
    else:
        return None
    if not isinstance(error, dict):
        return None
    return str(error.get("code") or "").strip() or None


def _codex_capacity_error_code(event: dict[str, Any]) -> str | None:
    code = _codex_failure_code(event)
    return code if code in CODEX_CAPACITY_ERROR_CODES else None


def _codex_context_error_code(event: dict[str, Any]) -> str | None:
    code = _codex_failure_code(event)
    return code if code in CODEX_CONTEXT_ERROR_CODES else None


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
        context_code = _codex_context_error_code(event)
        if context_code:
            return CodexResponsePreamble(bytes(buffered), None, context_code)
        if event.get("type") not in _CODEX_NON_OUTPUT_EVENT_TYPES:
            return CodexResponsePreamble(bytes(buffered))
        if reached_eof:
            return CodexResponsePreamble(bytes(buffered))

    return CodexResponsePreamble(bytes(buffered))


class CodexRouter:
    name = "codex"
    runtime = "codex"
    protocol = "openai_responses"
    request_paths = (
        "/backend-api/codex/*",
        "/backend-api/codex/responses",
        "/v1/responses",
        "/v1/responses/compact",
    )
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
        handle_responses_compact_post: Callable[
            [Any, str, dict[str, Any], dict[str, Any]], None
        ] = lambda *_args, **_kwargs: None,
    ) -> None:
        self._routed_enabled = routed_enabled
        self._handle_responses_post = handle_responses_post
        self._handle_responses_compact_post = handle_responses_compact_post
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
        if path in {"/v1/responses", "/v1/responses/compact"}:
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
        if path == "/v1/responses/compact":
            self._handle_responses_compact_post(handler, provider, pcfg, body)
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
