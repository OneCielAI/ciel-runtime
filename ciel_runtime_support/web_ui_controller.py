"""Router web UI projection and GET application controller."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


RuntimeConfig = dict[str, Any]
ProviderConfig = dict[str, Any]


@dataclass(frozen=True, slots=True)
class WebUiConstants:
    version: str
    activity_path: Path
    context_usage_path: Path
    default_timeout_ms: int


@dataclass(frozen=True, slots=True)
class WebUiProjectionPorts:
    current_alias: Callable[[RuntimeConfig], str]
    read_json: Callable[[Path], dict[str, Any]]
    rate_limit_usage: Callable[
        [str, ProviderConfig],
        tuple[int, int | None],
    ]
    positive_int: Callable[[Any], int | None]
    idle_timeout_ms: Callable[[int], int]
    context_limit: Callable[[str, ProviderConfig], int | None]


@dataclass(frozen=True, slots=True)
class WebUiDisplayPorts:
    render_home: Callable[..., str]
    render_chat: Callable[..., str]
    provider_mode: Callable[[str, ProviderConfig], str]
    api_key_status: Callable[[str, ProviderConfig], str]


@dataclass(frozen=True, slots=True)
class WebUiHttpPorts:
    load_config: Callable[[], RuntimeConfig]
    current_provider: Callable[
        [RuntimeConfig],
        tuple[str, ProviderConfig],
    ]
    write_text: Callable[..., None]


@dataclass(frozen=True, slots=True)
class WebUiController:
    constants: WebUiConstants
    projection: WebUiProjectionPorts
    display: WebUiDisplayPorts
    http: WebUiHttpPorts

    def render_router_home(
        self,
        config: RuntimeConfig,
        provider: str,
        provider_config: ProviderConfig,
    ) -> str:
        upstream = self.projection.read_json(
            self.constants.activity_path
        )
        context = self.projection.read_json(
            self.constants.context_usage_path
        )
        used, rpm_limit = self.projection.rate_limit_usage(
            provider,
            provider_config,
        )
        rpm_text = self._rpm_text(provider_config, used, rpm_limit)
        timeout_ms = (
            self.projection.positive_int(
                provider_config.get("request_timeout_ms")
            )
            or self.constants.default_timeout_ms
        )
        idle_ms = (
            self.projection.positive_int(
                provider_config.get("stream_idle_timeout_ms")
            )
            or self.projection.idle_timeout_ms(timeout_ms)
        )
        context_limit = self.projection.context_limit(
            provider,
            provider_config,
        )
        context_text = self._context_text(context, context_limit)
        upstream_text = " · ".join(
            value
            for value in (
                str(upstream.get("event") or "idle"),
                str(upstream.get("provider") or provider),
                str(
                    upstream.get("model")
                    or provider_config.get("current_model")
                    or ""
                ),
            )
            if value
        )
        return self.display.render_home(
            version=self.constants.version,
            provider=provider,
            model=self.projection.current_alias(config),
            context_text=context_text,
            timeout_ms=timeout_ms,
            idle_ms=idle_ms,
            rpm_text=rpm_text,
            upstream_text=upstream_text,
        )

    def render_web_chat(
        self,
        config: RuntimeConfig,
        provider: str,
        provider_config: ProviderConfig,
    ) -> str:
        timeout_ms = (
            self.projection.positive_int(
                provider_config.get("request_timeout_ms")
            )
            or self.constants.default_timeout_ms
        )
        return self.display.render_chat(
            model=self.projection.current_alias(config),
            provider=provider,
            mode=self.display.provider_mode(provider, provider_config),
            api_status=self.display.api_key_status(
                provider,
                provider_config,
            ),
            timeout_ms=timeout_ms,
        )

    def handle_get(self, handler: Any, path: str) -> bool:
        if path not in {"/ca/web/chat", "/ca/web/chat/"}:
            return False
        config = self.http.load_config()
        provider, provider_config = self.http.current_provider(config)
        self.http.write_text(
            handler,
            self.render_web_chat(
                config,
                provider,
                provider_config,
            ),
            content_type="text/html; charset=utf-8",
        )
        return True

    @staticmethod
    def _rpm_text(
        provider_config: ProviderConfig,
        used: int,
        limit: int | None,
    ) -> str:
        if not bool(provider_config.get("rate_limit_status", False)):
            return "off"
        if limit == 0:
            return f"{used}/min unmanaged"
        return f"{used}/{limit}" if limit else "unknown"

    def _context_text(
        self,
        context: dict[str, Any],
        limit: int | None,
    ) -> str:
        tokens = self.projection.positive_int(context.get("tokens"))
        percent = context.get("percent")
        if isinstance(percent, (int, float)):
            return f"{tokens or 0:,}/{limit or 0:,} tok ({percent}%)"
        if limit:
            return f"{tokens or 0:,}/{limit:,} tok"
        return "unknown"


__all__ = [
    "WebUiConstants",
    "WebUiController",
    "WebUiDisplayPorts",
    "WebUiHttpPorts",
    "WebUiProjectionPorts",
]
