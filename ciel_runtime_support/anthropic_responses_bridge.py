"""Remote Anthropic Messages projection for Responses-only providers."""

from __future__ import annotations

import json
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable

from .remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER
from .upstream_error_policy import UpstreamFailure, anthropic_error_type_for_status


@dataclass(frozen=True, slots=True)
class AnthropicResponsesProjectionPorts:
    to_responses: Callable[..., dict[str, Any]]
    to_anthropic: Callable[..., dict[str, Any]]
    normalize_model: Callable[[str, dict[str, Any], str], str]
    normalize_options: Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class AnthropicResponsesTransportPorts:
    endpoint: Callable[[str, dict[str, Any], str], str]
    headers: Callable[..., dict[str, str]]
    post_json: Callable[..., Any]
    open_request: Callable[..., Any]
    timeout_seconds: Callable[[dict[str, Any]], float]


@dataclass(frozen=True, slots=True)
class AnthropicResponsesOutputPorts:
    write_message: Callable[..., None]
    write_json: Callable[..., None]
    stream_response: Callable[..., None]


@dataclass(frozen=True, slots=True)
class AnthropicResponsesBridgePorts:
    projection: AnthropicResponsesProjectionPorts
    transport: AnthropicResponsesTransportPorts
    output: AnthropicResponsesOutputPorts


class AnthropicResponsesBridge:
    """Buffer one Responses call and emit an Anthropic JSON/SSE response."""

    def __init__(self, ports: AnthropicResponsesBridgePorts) -> None:
        self._ports = ports

    @staticmethod
    def _http_error_message(raw: bytes, fallback: str) -> str:
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return fallback
        if not isinstance(payload, dict):
            return fallback
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or fallback)
        return str(payload.get("message") or fallback)

    def _write_error(self, handler: Any, status: int, message: str) -> None:
        self._ports.output.write_json(
            handler,
            {
                "type": "error",
                "error": {
                    "type": anthropic_error_type_for_status(status),
                    "message": message,
                },
            },
            status=status,
        )

    def forward(
        self,
        handler: Any,
        provider: str,
        config: dict[str, Any],
        body: dict[str, Any],
        model: str,
    ) -> None:
        projection = self._ports.projection
        transport = self._ports.transport
        output = self._ports.output
        upstream_model = projection.normalize_model(provider, config, model)
        try:
            request = projection.to_responses(
                body,
                upstream_model,
                strict=True,
            )
        except (TypeError, ValueError) as exc:
            self._write_error(handler, 400, str(exc))
            return
        request["model"] = upstream_model
        client_stream = bool(body.get("stream", False))
        request["stream"] = client_stream
        request = projection.normalize_options(
            provider,
            config,
            request,
            "openai_responses",
        )
        url = transport.endpoint(provider, config, "openai_responses")
        headers = transport.headers(
            provider,
            config,
            handler.headers,
            "openai_responses",
        )
        try:
            if client_stream:
                response = transport.open_request(
                    url,
                    request,
                    headers,
                    transport.timeout_seconds(config),
                    provider,
                    config,
                    upstream_model,
                    stream=True,
                )
                try:
                    output.stream_response(handler, response, upstream_model)
                finally:
                    response.close()
                return
            payload = transport.post_json(
                url, request, headers, transport.timeout_seconds(config),
                provider, config, upstream_model,
            )
        except urllib.error.HTTPError as exc:
            raw = getattr(exc, "ciel_runtime_body", None)
            if raw is None:
                raw = exc.read()
            message = self._http_error_message(
                bytes(raw or b""),
                f"upstream HTTP {exc.code}",
            )
            self._write_error(handler, int(exc.code), message)
            return
        except UpstreamFailure as exc:
            self._write_error(handler, exc.status_code, exc.message)
            return
        if not isinstance(payload, dict):
            self._write_error(
                handler,
                502,
                "Responses upstream returned non-object JSON",
            )
            return
        try:
            if config.get(REMOTE_BRIDGE_CONFIG_MARKER) is True:
                message = projection.to_anthropic(
                    payload,
                    upstream_model,
                    strict=True,
                )
            else:
                message = projection.to_anthropic(payload, upstream_model)
        except (TypeError, ValueError) as exc:
            self._write_error(handler, 502, str(exc))
            return
        output.write_message(handler, message, False)


__all__ = [
    "AnthropicResponsesBridge",
    "AnthropicResponsesBridgePorts",
    "AnthropicResponsesOutputPorts",
    "AnthropicResponsesProjectionPorts",
    "AnthropicResponsesTransportPorts",
]
