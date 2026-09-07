"""Native OpenAI Responses passthrough for compatible model providers."""

from __future__ import annotations

import io
import json
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.client import IncompleteRead
from typing import Any, Callable, Mapping

from .responses_usage_observer import ResponsesUsageObserver
from .responses_cache_diagnostics import (
    cache_trace,
    request_cache_profile,
    usage_with_cache_profile,
)
from .responses_input_compatibility import repair_replayed_response_items
from .responses_custom_tool_bridge import (
    ResponsesCustomToolStreamProjector,
    project_response_payload,
    tool_definitions,
)
from .remote_bridge import is_remote_bridge_request
from .upstream_dump import dump_upstream_request
from .upstream_error_policy import UpstreamStreamReadError


@dataclass(frozen=True, slots=True)
class ProviderResponsesPassthroughPorts:
    project_channel_context: Callable[
        [dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]
    ]
    begin_channel_delivery: Callable[[Any, dict[str, Any]], None]
    normalize_model: Callable[[str, dict[str, Any], str], str]
    normalize_request: Callable[
        [str, dict[str, Any], Mapping[str, Any]], Mapping[str, Any]
    ]
    upstream_base: Callable[[str, dict[str, Any]], str]
    join_url: Callable[[str, str], str]
    headers: Callable[[str, dict[str, Any], Any], dict[str, str]]
    urlopen: Callable[..., Any]
    timeout_seconds: Callable[[dict[str, Any]], float]
    copy_response_headers: Callable[[Any, Any], None]
    record_usage: Callable[[str, str, dict[str, Any]], None] = (
        lambda _provider, _model, _usage: None
    )
    log: Callable[[str, str], Any] = lambda _level, _message: None
    request_max_bytes: Callable[[str, dict[str, Any]], int | None] = (
        lambda _provider, _config: None
    )
    estimate_tokens: Callable[[Any], int] = lambda _body: 0
    compact_responses: Callable[..., dict[str, Any]] = (
        lambda body, _budget, **_kwargs: body
    )
    finalize_body: Callable[[dict[str, Any]], dict[str, Any]] = lambda body: body
    endpoint: Callable[[str, dict[str, Any], str], str] | None = None


class ProviderResponsesPassthrough:
    """Forward Responses without collapsing typed items into another protocol."""

    def __init__(self, ports: ProviderResponsesPassthroughPorts) -> None:
        self._ports = ports

    def _endpoint(
        self, provider: str, config: dict[str, Any], operation: str
    ) -> str:
        if self._ports.endpoint is not None:
            if operation == "openai_responses_compact":
                return (
                    self._ports.endpoint(provider, config, "openai_responses")
                    .rstrip("/")
                    + "/compact"
                )
            return self._ports.endpoint(provider, config, operation)
        path = (
            "/v1/responses/compact"
            if operation == "openai_responses_compact"
            else "/v1/responses"
        )
        return self._ports.join_url(self._ports.upstream_base(provider, config), path)

    def _request_headers(
        self,
        provider: str,
        config: dict[str, Any],
        inbound_headers: Any,
        body: Mapping[str, Any],
    ) -> dict[str, str]:
        headers = self._ports.headers(provider, config, inbound_headers)
        if not config.get("responses_session_cache_requires_previous_response_id"):
            return headers
        if str(body.get("previous_response_id") or "").strip():
            return headers
        filtered = {
            name: value
            for name, value in headers.items()
            if str(name).casefold() != "x-dashscope-session-cache"
        }
        if len(filtered) != len(headers):
            self._ports.log(
                "INFO",
                "provider_responses_session_cache_deferred "
                f"provider={provider} reason=missing_previous_response_id",
            )
        return filtered

    @staticmethod
    def _response_headers(headers: Any, *, transformed: bool) -> Any:
        if not transformed:
            return headers
        try:
            return {
                key: value
                for key, value in headers.items()
                if str(key).casefold() != "content-length"
            }
        except (AttributeError, TypeError):
            return headers

    def forward_compact(
        self,
        handler: Any,
        provider: str,
        config: dict[str, Any],
        body: dict[str, Any],
    ) -> None:
        """Forward the opaque Responses compaction contract without rewriting it."""

        upstream_body = dict(body)
        upstream_body["model"] = self._ports.normalize_model(
            provider, config, str(body.get("model") or "")
        )
        if not is_remote_bridge_request(handler):
            upstream_body = self._ports.finalize_body(upstream_body)
        data = self._encode(upstream_body)
        url = self._endpoint(provider, config, "openai_responses_compact")
        request_headers = self._request_headers(
            provider, config, handler.headers, upstream_body
        )
        dump_upstream_request(
            url, data, self._ports.log, headers=request_headers
        )
        request = urllib.request.Request(
            url,
            data=data,
            headers=request_headers,
            method="POST",
        )
        with self._ports.urlopen(
            request,
            timeout=self._ports.timeout_seconds(config),
            provider=provider,
            pcfg=config,
        ) as response:
            handler.send_response(getattr(response, "status", 200))
            self._ports.copy_response_headers(handler, response.headers)
            handler.end_headers()
            while chunk := response.read(65_536):
                handler.wfile.write(chunk)
                handler.wfile.flush()

    @staticmethod
    def _encode(body: dict[str, Any]) -> bytes:
        return json.dumps(
            body,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    def _fit_provider_request(
        self,
        url: str,
        provider: str,
        config: dict[str, Any],
        body: dict[str, Any],
        *,
        remote_bridge: bool = False,
    ) -> tuple[dict[str, Any], bytes]:
        data = self._encode(body)
        configured_limit = self._ports.request_max_bytes(provider, config)
        if configured_limit is None:
            return body, data
        hard_limit = max(1, int(configured_limit))
        target = max(1, (hard_limit * 9) // 10)
        if remote_bridge:
            if len(data) <= hard_limit:
                return body, data
            message = (
                "Remote Bridge provider request body exceeds the configured maximum "
                "and was not compacted: "
                f"{len(data)} bytes; maximum is {hard_limit} bytes"
            )
            payload = json.dumps(
                {"error": {"type": "request_too_large", "message": message}},
                separators=(",", ":"),
            ).encode("utf-8")
            raise urllib.error.HTTPError(
                url,
                413,
                "Payload Too Large",
                {"content-type": "application/json"},
                io.BytesIO(payload),
            )
        if len(data) <= target:
            return body, data

        original_bytes = len(data)
        current = body
        current_tokens = max(1, int(self._ports.estimate_tokens(current)))
        best_body, best_data = current, data
        for attempt in range(1, 5):
            ratio = min(0.9, target / max(1, len(best_data)))
            budget = max(8192, int(current_tokens * ratio * 0.95))
            compacted = self._ports.compact_responses(
                current,
                budget,
                provider=provider,
                model=str(current.get("model") or ""),
                remote_bridge=remote_bridge,
                stable_prefix_checkpoint_items=config.get(
                    "responses_cache_checkpoint_items", 0
                ),
            )
            if not remote_bridge:
                compacted = self._ports.finalize_body(compacted)
            compacted_data = self._encode(compacted)
            self._ports.log(
                "WARN",
                "provider_responses_wire_compact "
                f"provider={provider} model={current.get('model')} "
                f"attempt={attempt}/4 bytes={len(data)}->{len(compacted_data)} "
                f"target={target} hard_limit={hard_limit} budget={budget}",
            )
            if len(compacted_data) >= len(best_data):
                break
            best_body, best_data = compacted, compacted_data
            if len(best_data) <= target:
                return best_body, best_data
            current = compacted
            current_tokens = max(1, int(self._ports.estimate_tokens(current)))

        if len(best_data) <= hard_limit:
            return best_body, best_data
        message = (
            "Provider request body remains too large after bounded context compaction: "
            f"{original_bytes} -> {len(best_data)} bytes; maximum is {hard_limit} bytes"
        )
        payload = json.dumps(
            {"error": {"type": "request_too_large", "message": message}},
            separators=(",", ":"),
        ).encode("utf-8")
        raise urllib.error.HTTPError(
            url,
            413,
            "Payload Too Large",
            {"content-type": "application/json"},
            io.BytesIO(payload),
        )

    @staticmethod
    def _stream_truncation_retries(config: dict[str, Any]) -> int:
        """Return the bounded native Responses replay count.

        This is deliberately separate from ``gateway_retries``.  A native
        Responses stream can only be replayed safely while no bytes have been
        committed to the Codex client, and a deterministic provider rejection
        must never enter this loop.
        """

        try:
            configured = int(config.get("responses_stream_truncation_retries", 0))
        except (TypeError, ValueError):
            configured = 0
        return max(0, min(2, configured))

    def _forward_buffered_stream(
        self,
        handler: Any,
        request: urllib.request.Request,
        provider: str,
        config: dict[str, Any],
        upstream_body: dict[str, Any],
        response_tools: Mapping[str, Mapping[str, Any]],
        cache_profile: Mapping[str, Any],
    ) -> None:
        """Validate a native Responses stream before exposing it downstream."""

        retries = self._stream_truncation_retries(config)
        max_attempts = retries + 1
        model = str(upstream_body.get("model") or "")
        for attempt in range(1, max_attempts + 1):
            with self._ports.urlopen(
                request,
                timeout=self._ports.timeout_seconds(config),
                provider=provider,
                pcfg=config,
            ) as response, tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024) as spool:
                usage = ResponsesUsageObserver()
                received_bytes = 0
                read_error: BaseException | None = None
                try:
                    while chunk := response.read(65_536):
                        received_bytes += len(chunk)
                        usage.feed(chunk)
                        spool.write(chunk)
                except (IncompleteRead, OSError) as exc:
                    partial = bytes(getattr(exc, "partial", b"") or b"")
                    if partial:
                        received_bytes += len(partial)
                        usage.feed(partial)
                        spool.write(partial)
                    read_error = exc

                observed = usage.finish()
                stream_expected = bool(upstream_body.get("stream", True))
                terminal_missing = stream_expected and usage.terminal_event is None
                if read_error is not None and not terminal_missing:
                    self._ports.log(
                        "WARN",
                        "provider_responses_length_mismatch_after_terminal "
                        f"provider={provider} model={model} "
                        f"terminal={usage.terminal_event} bytes={received_bytes}",
                    )
                if read_error is not None or terminal_missing:
                    failure = read_error or EOFError(
                        "upstream Responses stream ended without a terminal event"
                    )
                    if attempt < max_attempts:
                        self._ports.log(
                            "WARN",
                            "provider_responses_stream_retry "
                            f"provider={provider} model={model} "
                            f"attempt={attempt}/{retries} bytes={received_bytes} "
                            f"error={type(failure).__name__}",
                        )
                        continue
                    self._ports.log(
                        "ERROR",
                        "provider_responses_stream_truncated "
                        f"provider={provider} model={model} bytes={received_bytes} "
                        f"attempts={attempt}",
                    )
                    raise UpstreamStreamReadError(
                        provider,
                        model,
                        failure,
                        attempts=attempt,
                        downstream_started=False,
                        response_id=usage.response_id,
                        received_bytes=received_bytes,
                    ) from failure

                handler.send_response(getattr(response, "status", 200))
                self._ports.copy_response_headers(
                    handler,
                    self._response_headers(
                        response.headers, transformed=bool(response_tools)
                    ),
                )
                handler.end_headers()
                spool.seek(0)
                projector = (
                    ResponsesCustomToolStreamProjector(response_tools)
                    if response_tools
                    else None
                )
                while chunk := spool.read(65_536):
                    output = projector.feed(chunk) if projector is not None else chunk
                    if output:
                        handler.wfile.write(output)
                        handler.wfile.flush()
                if projector is not None:
                    tail = projector.finish()
                    if tail:
                        handler.wfile.write(tail)
                        handler.wfile.flush()
                if observed:
                    observation = usage_with_cache_profile(observed, cache_profile)
                    self._ports.record_usage(provider, model, observation)
                    self._ports.log(*cache_trace(provider, model, observation))
                return

    def forward(
        self,
        handler: Any,
        provider: str,
        config: dict[str, Any],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        remote_bridge = is_remote_bridge_request(handler)
        upstream_body = dict(
            body if remote_bridge else repair_replayed_response_items(body)
        )
        response_tools = (
            tool_definitions(upstream_body)
            if config.get("responses_custom_tools_as_functions")
            else {}
        )
        upstream_body["model"] = self._ports.normalize_model(
            provider, config, str(body.get("model") or "")
        )
        upstream_body = dict(
            self._ports.normalize_request(provider, config, upstream_body)
        )
        if remote_bridge:
            delivery_body = {}
        else:
            upstream_body, delivery_body = self._ports.project_channel_context(
                upstream_body
            )
            upstream_body = self._ports.finalize_body(upstream_body)
            self._ports.begin_channel_delivery(handler, delivery_body)
        url = self._endpoint(provider, config, "openai_responses")
        upstream_body, data = self._fit_provider_request(
            url,
            provider,
            config,
            upstream_body,
            remote_bridge=remote_bridge,
        )
        cache_profile = request_cache_profile(upstream_body, len(data))
        request_headers = self._request_headers(
            provider, config, handler.headers, upstream_body
        )
        dump_upstream_request(
            url, data, self._ports.log, headers=request_headers
        )
        request = urllib.request.Request(
            url,
            data=data,
            headers=request_headers,
            method="POST",
        )
        if not remote_bridge and self._stream_truncation_retries(config):
            self._forward_buffered_stream(
                handler,
                request,
                provider,
                config,
                upstream_body,
                response_tools,
                cache_profile,
            )
            return delivery_body
        with self._ports.urlopen(
            request,
            timeout=self._ports.timeout_seconds(config),
            provider=provider,
            pcfg=config,
        ) as response:
            usage = ResponsesUsageObserver()
            received_bytes = 0
            handler.send_response(getattr(response, "status", 200))
            self._ports.copy_response_headers(
                handler,
                self._response_headers(
                    response.headers, transformed=bool(response_tools)
                ),
            )
            handler.end_headers()
            projector = (
                ResponsesCustomToolStreamProjector(response_tools)
                if response_tools and bool(upstream_body.get("stream", True))
                else None
            )
            response_body = bytearray()
            try:
                while chunk := response.read(65_536):
                    received_bytes += len(chunk)
                    usage.feed(chunk)
                    if response_tools and projector is None:
                        response_body.extend(chunk)
                        continue
                    output = projector.feed(chunk) if projector is not None else chunk
                    if output:
                        handler.wfile.write(output)
                        handler.wfile.flush()
            except IncompleteRead as exc:
                partial = bytes(exc.partial or b"")
                if partial:
                    received_bytes += len(partial)
                    usage.feed(partial)
                    if response_tools and projector is None:
                        response_body.extend(partial)
                    else:
                        output = (
                            projector.feed(partial)
                            if projector is not None
                            else partial
                        )
                        if output:
                            handler.wfile.write(output)
                            handler.wfile.flush()
                usage.finish()
                if usage.terminal_event is None:
                    self._ports.log(
                        "ERROR",
                        "provider_responses_stream_truncated "
                        f"provider={provider} model={upstream_body.get('model')} "
                        f"bytes={received_bytes}",
                    )
                    raise UpstreamStreamReadError(
                        provider,
                        str(upstream_body.get("model") or ""),
                        exc,
                        attempts=1,
                        downstream_started=True,
                        response_id=usage.response_id,
                        received_bytes=received_bytes,
                    ) from exc
                self._ports.log(
                    "WARN",
                    "provider_responses_length_mismatch_after_terminal "
                    f"provider={provider} model={upstream_body.get('model')} "
                    f"terminal={usage.terminal_event} bytes={received_bytes}",
                )
            if projector is not None:
                tail = projector.finish()
                if tail:
                    handler.wfile.write(tail)
                    handler.wfile.flush()
            elif response_tools:
                try:
                    decoded = json.loads(response_body)
                    projected_body = project_response_payload(decoded, response_tools)
                    handler.wfile.write(self._encode(projected_body))
                    handler.wfile.flush()
                except (UnicodeDecodeError, ValueError, TypeError):
                    handler.wfile.write(response_body)
                    handler.wfile.flush()
            observed = usage.finish()
            if bool(upstream_body.get("stream", True)) and usage.terminal_event is None:
                error = EOFError("upstream Responses stream ended without a terminal event")
                self._ports.log(
                    "ERROR",
                    "provider_responses_stream_missing_terminal "
                    f"provider={provider} model={upstream_body.get('model')} "
                    f"bytes={received_bytes}",
                )
                raise UpstreamStreamReadError(
                    provider,
                    str(upstream_body.get("model") or ""),
                    error,
                    attempts=1,
                    downstream_started=True,
                    response_id=usage.response_id,
                    received_bytes=received_bytes,
                ) from error
            if observed and not remote_bridge:
                observation = usage_with_cache_profile(observed, cache_profile)
                self._ports.record_usage(
                    provider,
                    str(upstream_body.get("model") or ""),
                    observation,
                )
                self._ports.log(
                    *cache_trace(
                        provider,
                        str(upstream_body.get("model") or ""),
                        observation,
                    )
                )
        return delivery_body


__all__ = [
    "ProviderResponsesPassthrough",
    "ProviderResponsesPassthroughPorts",
]
