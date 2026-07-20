"""Provider endpoint route adapter and compatibility probe policy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderEndpointRoutePorts:
    decorate_headers: Callable[[dict[str, str]], dict[str, str]]
    request: Callable[..., Any]
    urlopen: Callable[..., Any]
    http_error: type[BaseException]


@dataclass(frozen=True, slots=True)
class ProviderEndpointRouteAdapter:
    ports: ProviderEndpointRoutePorts

    def exists(
        self,
        url: str,
        headers: dict[str, str],
        timeout: float = 1.5,
    ) -> bool | None:
        request = self.ports.request(
            url,
            data=b"{}",
            headers=self.ports.decorate_headers(headers),
            method="POST",
        )
        try:
            with self.ports.urlopen(request, timeout=timeout):
                return True
        except self.ports.http_error as exc:
            try:
                exc.read()  # type: ignore[attr-defined]
            except Exception:
                pass
            code = getattr(exc, "code", None)
            if code == 404:
                return False
            if code in {400, 401, 403, 405, 422}:
                return True
            return None
        except Exception:
            return None


@dataclass(frozen=True, slots=True)
class ProviderEndpointProbeProjection:
    upstream_base: Callable[[str, dict[str, Any]], str]
    native_base: Callable[[str, dict[str, Any]], str]
    join_url: Callable[[str, str], str]


@dataclass(frozen=True, slots=True)
class ProviderEndpointProbeQueries:
    primary_headers: Callable[[str, dict[str, Any]], dict[str, str]]
    fallback_headers: Callable[[str, dict[str, Any]], dict[str, str]]
    route_exists: Callable[[str, dict[str, str], float], bool | None]


@dataclass(frozen=True, slots=True)
class ProviderEndpointProbePolicy:
    projection: ProviderEndpointProbeProjection
    query: ProviderEndpointProbeQueries

    @staticmethod
    def status_label(value: bool | None) -> str:
        if value is True:
            return "available"
        if value is False:
            return "missing"
        return "inconclusive"

    def headers_for(
        self,
        provider: str,
        config: dict[str, Any],
    ) -> dict[str, str]:
        try:
            return self.query.primary_headers(provider, config)
        except Exception:
            return self.query.fallback_headers(provider, config)

    def _routes(
        self,
        provider: str,
        config: dict[str, Any],
        timeout: float,
        headers: dict[str, str] | None = None,
    ) -> tuple[str, str, bool | None, bool | None]:
        request_headers = (
            headers
            if headers is not None
            else self.headers_for(provider, config)
        )
        anthropic_url = self.projection.join_url(
            self.projection.native_base(provider, config),
            "/v1/messages",
        )
        openai_url = self.projection.join_url(
            self.projection.upstream_base(provider, config),
            "/v1/chat/completions",
        )
        return (
            anthropic_url,
            openai_url,
            self.query.route_exists(anthropic_url, request_headers, timeout),
            self.query.route_exists(openai_url, request_headers, timeout),
        )

    def detect_native_compat(
        self,
        provider: str,
        config: dict[str, Any],
        supported_providers: frozenset[str],
    ) -> tuple[bool | None, str]:
        if provider not in supported_providers:
            return None, ""
        if not self.projection.upstream_base(provider, config):
            return None, "missing base URL"
        _, _, anthropic, openai = self._routes(
            provider,
            config,
            1.5,
            self.query.fallback_headers(provider, config),
        )
        if anthropic is True:
            return True, "Anthropic Messages route detected"
        if openai is True and anthropic is False:
            return False, "OpenAI chat completions route detected"
        if openai is True:
            return (
                None,
                "OpenAI route detected, Anthropic route inconclusive; "
                "keeping Anthropic default",
            )
        return None, "endpoint family inconclusive; keeping Anthropic default"

    def report(
        self,
        provider: str,
        config: dict[str, Any],
        excluded_providers: frozenset[str],
        timeout: float = 1.5,
    ) -> list[str]:
        if provider in excluded_providers:
            return []
        probe_timeout = max(0.25, min(float(timeout or 1.5), 3.0))
        anthropic_url, openai_url, anthropic, openai = self._routes(
            provider,
            config,
            probe_timeout,
        )
        return [
            "Endpoint probes:",
            "- Anthropic Messages (/v1/messages): %s (%s)"
            % (self.status_label(anthropic), anthropic_url),
            "- OpenAI Chat (/v1/chat/completions): %s (%s)"
            % (self.status_label(openai), openai_url),
        ]
