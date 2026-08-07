"""Native OpenAI Responses passthrough for compatible model providers."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .responses_usage_observer import ResponsesUsageObserver
from .responses_input_compatibility import repair_replayed_response_items


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
    record_usage: Callable[[str, str, dict[str, int]], None] = (
        lambda _provider, _model, _usage: None
    )


class ProviderResponsesPassthrough:
    """Forward Responses without collapsing typed items into another protocol."""

    def __init__(self, ports: ProviderResponsesPassthroughPorts) -> None:
        self._ports = ports

    def forward(
        self,
        handler: Any,
        provider: str,
        config: dict[str, Any],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        upstream_body = dict(repair_replayed_response_items(body))
        upstream_body["model"] = self._ports.normalize_model(
            provider, config, str(body.get("model") or "")
        )
        upstream_body = dict(
            self._ports.normalize_request(provider, config, upstream_body)
        )
        upstream_body, delivery_body = self._ports.project_channel_context(
            upstream_body
        )
        self._ports.begin_channel_delivery(handler, delivery_body)
        url = self._ports.join_url(
            self._ports.upstream_base(provider, config),
            "/v1/responses",
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(upstream_body, ensure_ascii=False).encode("utf-8"),
            headers=self._ports.headers(provider, config, handler.headers),
            method="POST",
        )
        with self._ports.urlopen(
            request,
            timeout=self._ports.timeout_seconds(config),
            provider=provider,
            pcfg=config,
        ) as response:
            usage = ResponsesUsageObserver()
            handler.send_response(getattr(response, "status", 200))
            self._ports.copy_response_headers(handler, response.headers)
            handler.end_headers()
            while chunk := response.read(65_536):
                usage.feed(chunk)
                handler.wfile.write(chunk)
                handler.wfile.flush()
            observed = usage.finish()
            if observed:
                self._ports.record_usage(
                    provider,
                    str(upstream_body.get("model") or ""),
                    observed,
                )
        return delivery_body


__all__ = [
    "ProviderResponsesPassthrough",
    "ProviderResponsesPassthroughPorts",
]
