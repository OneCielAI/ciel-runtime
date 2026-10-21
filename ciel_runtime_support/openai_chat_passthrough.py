"""Native OpenAI Chat Completions passthrough for compatible CLI runtimes."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER, is_remote_bridge_request
from .transient_retry import bounded_timeout, open_with_transient_retry
from .upstream_stream_io import client_connection_closed


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
    log: Callable[[str, str], Any] = lambda _level, _message: None
    publish: Callable[..., Any] = lambda **_kwargs: None
    # Rebuild the provider config for one more attempt on a fresh gateway.
    reselect: Callable[[dict[str, Any]], dict[str, Any]] | None = None


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
        remote_bridge = is_remote_bridge_request(handler)
        if not remote_bridge:
            upstream_body = self._ports.finalize_body(upstream_body)
        data = json.dumps(upstream_body, ensure_ascii=False).encode("utf-8")
        # A remote bridge client owns its own retries; never replay for it.
        retry_provider = (
            ""
            if remote_bridge or config.get(REMOTE_BRIDGE_CONFIG_MARKER) is True
            else provider
        )
        attempt: dict[str, Any] = {"config": config}

        def build_request(attempt_config: dict[str, Any]) -> urllib.request.Request:
            url = (
                self._ports.endpoint(provider, attempt_config, "openai_chat")
                if self._ports.endpoint is not None
                else self._ports.join_url(
                    self._ports.upstream_base(provider, attempt_config),
                    "/v1/chat/completions",
                )
            )
            return urllib.request.Request(
                url,
                data=data,
                headers=self._ports.headers(
                    provider, attempt_config, handler.headers
                ),
                method="POST",
            )

        attempt["request"] = build_request(config)

        def open_once() -> Any:
            return self._ports.urlopen(
                attempt["request"],
                timeout=bounded_timeout(attempt["config"], retry_provider,
                                        self._ports.timeout_seconds(attempt["config"])),
                provider=provider,
                pcfg=attempt["config"],
            )

        def reselect() -> None:
            if self._ports.reselect is None:
                return
            fresh = self._ports.reselect(dict(attempt["config"]))
            if not isinstance(fresh, dict) or fresh is attempt["config"]:
                return
            attempt["config"] = fresh
            attempt["request"] = build_request(fresh)

        try:
            with open_with_transient_retry(
                open_once,
                provider=retry_provider,
                config=attempt["config"],
                log=self._ports.log,
                publish=self._ports.publish,
                cancelled=lambda: client_connection_closed(handler),
                request=attempt["request"],
                reselect=reselect if self._ports.reselect is not None else None,
                model=str(upstream_body.get("model") or ""),
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
