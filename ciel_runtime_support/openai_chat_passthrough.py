"""Native OpenAI Chat Completions passthrough for compatible CLI runtimes."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .remote_bridge import is_remote_bridge_request


@dataclass(frozen=True, slots=True)
class OpenAIChatPassthroughPorts:
    normalize_model: Callable[[str, dict[str, Any], str], str]
    normalize_request: Callable[[str, dict[str, Any], Mapping[str, Any]], Mapping[str, Any]]
    upstream_base: Callable[[str, dict[str, Any]], str]
    join_url: Callable[[str, str], str]
    headers: Callable[[str, dict[str, Any], Any], dict[str, str]]
    urlopen: Callable[..., Any]
    timeout_seconds: Callable[[dict[str, Any]], float]
    copy_response_headers: Callable[[Any, Any], None]
    finalize_body: Callable[[dict[str, Any]], dict[str, Any]] = lambda body: body
    endpoint: Callable[[str, dict[str, Any], str], str] | None = None


class OpenAIChatPassthrough:
    """Forward Chat Completions while preserving its native wire protocol."""

    def __init__(self, ports: OpenAIChatPassthroughPorts) -> None:
        self._ports = ports

    def forward(
        self,
        handler: Any,
        provider: str,
        config: dict[str, Any],
        body: dict[str, Any],
    ) -> None:
        upstream_body = dict(body)
        upstream_body["model"] = self._ports.normalize_model(
            provider, config, str(body.get("model") or "")
        )
        upstream_body = dict(
            self._ports.normalize_request(provider, config, upstream_body)
        )
        if not is_remote_bridge_request(handler):
            upstream_body = self._ports.finalize_body(upstream_body)
        url = (
            self._ports.endpoint(provider, config, "openai_chat")
            if self._ports.endpoint is not None
            else self._ports.join_url(
                self._ports.upstream_base(provider, config),
                "/v1/chat/completions",
            )
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(upstream_body, ensure_ascii=False).encode("utf-8"),
            headers=self._ports.headers(provider, config, handler.headers),
            method="POST",
        )
        try:
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
        except urllib.error.HTTPError as exc:
            handler.send_response(exc.code)
            self._ports.copy_response_headers(handler, exc.headers)
            handler.end_headers()
            while chunk := exc.read(65_536):
                handler.wfile.write(chunk)
                handler.wfile.flush()


__all__ = ["OpenAIChatPassthrough", "OpenAIChatPassthroughPorts"]
