"""Remote Chat Completions projection for non-Chat provider protocols."""

from __future__ import annotations

import json
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable

from .remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER
from .upstream_error_policy import UpstreamFailure, UpstreamStreamReadError


@dataclass(frozen=True, slots=True)
class OpenAIChatCompatibilityProjection:
    to_anthropic: Callable[..., dict[str, Any]]
    to_chat: Callable[..., dict[str, Any]]
    anthropic_to_responses: Callable[..., dict[str, Any]]
    responses_to_anthropic: Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class OpenAIChatCompatibilityRouting:
    collect_anthropic: Callable[..., dict[str, Any]]
    collect_ollama: Callable[..., dict[str, Any]]
    normalize_model: Callable[[str, dict[str, Any], str], str]
    normalize_options: Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class OpenAIChatCompatibilityTransport:
    endpoint: Callable[[str, dict[str, Any], str], str]
    headers: Callable[..., dict[str, str]]
    post_json: Callable[..., Any]
    timeout_seconds: Callable[[dict[str, Any]], float]


@dataclass(frozen=True, slots=True)
class OpenAIChatCompatibilityOutput:
    write_json: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OpenAIChatCompatibilityPorts:
    projection: OpenAIChatCompatibilityProjection
    routing: OpenAIChatCompatibilityRouting
    transport: OpenAIChatCompatibilityTransport
    output: OpenAIChatCompatibilityOutput


class OpenAIChatCompatibilityBridge:
    """Collect one non-Chat provider call and emit Chat JSON or SSE."""

    _COLLECTED_PROTOCOLS = frozenset(
        {"anthropic_messages", "ollama_chat"}
    )
    _RESPONSES_CONTROLS = (
        "metadata",
        "prompt_cache_key",
        "prompt_cache_retention",
        "safety_identifier",
        "service_tier",
        "store",
        "user",
    )

    def __init__(self, ports: OpenAIChatCompatibilityPorts) -> None:
        self._ports = ports

    def _error(
        self,
        handler: Any,
        status: int,
        message: str,
        *,
        code: str = "upstream_error",
        param: str | None = None,
    ) -> None:
        error: dict[str, Any] = {
            "message": message,
            "type": "invalid_request_error" if status < 500 else "api_error",
            "code": code,
        }
        if param is not None:
            error["param"] = param
        self._ports.output.write_json(handler, {"error": error}, status=status)

    @staticmethod
    def _response_started(handler: Any) -> bool:
        return isinstance(
            getattr(handler, "_ciel_runtime_response_status", None), int
        )

    @classmethod
    def _abort_started_response(cls, handler: Any) -> bool:
        if not cls._response_started(handler):
            return False
        handler.close_connection = True
        return True

    @staticmethod
    def _http_error_message(exc: urllib.error.HTTPError) -> str:
        raw = getattr(exc, "ciel_runtime_body", None)
        if raw is None:
            raw = exc.read()
        try:
            payload = json.loads(bytes(raw or b"").decode("utf-8", errors="replace"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return f"upstream HTTP {exc.code}"
        if not isinstance(payload, dict):
            return f"upstream HTTP {exc.code}"
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or f"upstream HTTP {exc.code}")
        return str(payload.get("message") or f"upstream HTTP {exc.code}")

    def _collect_responses(
        self,
        handler: Any,
        provider: str,
        config: dict[str, Any],
        anthropic_body: dict[str, Any],
        model: str,
        chat_body: dict[str, Any],
    ) -> dict[str, Any]:
        projection = self._ports.projection
        routing = self._ports.routing
        transport = self._ports.transport
        upstream_model = routing.normalize_model(provider, config, model)
        request = projection.anthropic_to_responses(
            anthropic_body,
            upstream_model,
        )
        for key in self._RESPONSES_CONTROLS:
            if chat_body.get(key) is not None:
                request[key] = chat_body[key]
        request["model"] = upstream_model
        request["stream"] = False
        request = routing.normalize_options(
            provider,
            config,
            request,
            "openai_responses",
        )
        payload = transport.post_json(
            transport.endpoint(provider, config, "openai_responses"),
            request,
            transport.headers(
                provider,
                config,
                handler.headers,
                "openai_responses",
            ),
            transport.timeout_seconds(config),
            provider,
            config,
            upstream_model,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Responses upstream returned non-object JSON")
        if config.get(REMOTE_BRIDGE_CONFIG_MARKER) is True:
            return projection.responses_to_anthropic(
                payload,
                upstream_model,
                strict=True,
            )
        return projection.responses_to_anthropic(payload, upstream_model)

    @staticmethod
    def _write_stream(
        handler: Any,
        response: dict[str, Any],
        *,
        include_usage: bool,
    ) -> None:
        response_id = str(response.get("id") or "chatcmpl_ciel")
        model = str(response.get("model") or "model")
        created = int(response.get("created") or 0)
        choices = response.get("choices") if isinstance(response.get("choices"), list) else []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}

        def emit(delta: dict[str, Any], finish_reason: str | None = None) -> None:
            payload = {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": delta,
                        "logprobs": None,
                        "finish_reason": finish_reason,
                    }
                ],
            }
            handler.wfile.write(
                f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
            )

        handler.send_response(200)
        handler._ciel_runtime_response_status = 200
        handler.send_header("content-type", "text/event-stream")
        handler.send_header("cache-control", "no-cache")
        handler.send_header("connection", "close")
        handler.end_headers()
        emit({"role": "assistant", "content": ""})
        reasoning_delta = {
            key: message[key]
            for key in (
                "reasoning_content",
                "reasoning_opaque",
                "reasoning_signature",
            )
            if message.get(key) is not None
        }
        if reasoning_delta:
            emit(reasoning_delta)
        if message.get("content"):
            emit({"content": str(message["content"])})
        for index, tool_call in enumerate(message.get("tool_calls") or []):
            emit(
                {
                    "tool_calls": [
                        {
                            "index": index,
                            "id": str(tool_call.get("id") or ""),
                            "type": "function",
                            "function": dict(tool_call.get("function") or {}),
                        }
                    ]
                }
            )
        emit({}, str(choice.get("finish_reason") or "stop"))
        if include_usage and isinstance(response.get("usage"), dict):
            usage_chunk = {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [],
                "usage": response["usage"],
            }
            handler.wfile.write(
                f"data: {json.dumps(usage_chunk, ensure_ascii=False)}\n\n".encode()
            )
        handler.wfile.write(b"data: [DONE]\n\n")
        handler.wfile.flush()

    def forward(
        self,
        handler: Any,
        provider: str,
        config: dict[str, Any],
        body: dict[str, Any],
        protocol: str,
    ) -> None:
        projection_body = body
        if protocol == "openai_responses":
            projection_body = dict(body)
            for key in self._RESPONSES_CONTROLS:
                projection_body.pop(key, None)
        elif any(
            isinstance(tool, dict)
            and isinstance(tool.get("function"), dict)
            and tool["function"].get("strict") is True
            for tool in (body.get("tools") or [])
        ):
            self._error(
                handler,
                400,
                "strict function tools require a Responses-capable selected model",
                code="unsupported_parameter",
                param="tools",
            )
            return
        try:
            anthropic_body = self._ports.projection.to_anthropic(
                projection_body,
                int(config.get("max_output_tokens") or 4096),
            )
        except (TypeError, ValueError) as exc:
            self._error(handler, 400, str(exc), code="invalid_request", param="messages")
            return
        if protocol == "openai_responses" and anthropic_body.get("stop_sequences"):
            self._error(
                handler,
                400,
                "stop is not supported when the selected model uses the Responses API",
                code="unsupported_parameter",
                param="stop",
            )
            return
        if protocol != "openai_responses":
            for tool in anthropic_body.get("tools") or []:
                if isinstance(tool, dict):
                    tool.pop("_ciel_openai_strict", None)

        try:
            model = str(body.get("model") or config.get("current_model") or "")
            if protocol == "openai_responses":
                message = self._collect_responses(
                    handler,
                    provider,
                    config,
                    anthropic_body,
                    model,
                    body,
                )
            elif protocol in self._COLLECTED_PROTOCOLS:
                collector = (
                    self._ports.routing.collect_anthropic
                    if protocol == "anthropic_messages"
                    else self._ports.routing.collect_ollama
                )
                message = collector(
                    handler,
                    provider,
                    config,
                    anthropic_body,
                )
            else:
                self._error(
                    handler,
                    501,
                    f"Provider '{provider}' cannot be projected from {protocol} to Chat Completions",
                    code="unsupported_feature",
                    param="model",
                )
                return
            response = self._ports.projection.to_chat(message, model)
            if bool(body.get("stream", False)):
                stream_options = (
                    body.get("stream_options")
                    if isinstance(body.get("stream_options"), dict)
                    else {}
                )
                self._write_stream(
                    handler,
                    response,
                    include_usage=bool(stream_options.get("include_usage")),
                )
            else:
                self._ports.output.write_json(handler, response, status=200)
        except (TypeError, ValueError) as exc:
            if self._abort_started_response(handler):
                return
            self._error(handler, 502, str(exc), code="upstream_error")
        except urllib.error.HTTPError as exc:
            if self._abort_started_response(handler):
                return
            self._error(
                handler,
                int(exc.code),
                self._http_error_message(exc),
                code="upstream_error",
            )
        except UpstreamFailure as exc:
            if self._abort_started_response(handler):
                return
            self._error(
                handler,
                exc.status_code,
                exc.message,
                code=exc.category,
            )
        except UpstreamStreamReadError as exc:
            if self._abort_started_response(handler):
                return
            self._error(handler, exc.status_code, str(exc), code="upstream_stream_truncated")
        except Exception as exc:
            if self._abort_started_response(handler):
                return
            self._error(
                handler,
                502,
                f"{type(exc).__name__}: {exc}",
                code="upstream_error",
            )


__all__ = [
    "OpenAIChatCompatibilityBridge",
    "OpenAIChatCompatibilityOutput",
    "OpenAIChatCompatibilityPorts",
    "OpenAIChatCompatibilityProjection",
    "OpenAIChatCompatibilityRouting",
    "OpenAIChatCompatibilityTransport",
]
