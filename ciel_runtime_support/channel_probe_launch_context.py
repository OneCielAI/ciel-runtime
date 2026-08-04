"""Channel probe-cache decisions and runtime launch orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .codex_channel_sse_launch import (
    CodexChannelSseEffects,
    CodexChannelSseLaunchService,
    CodexChannelSseQueryPorts,
)


@dataclass(frozen=True, slots=True)
class ChannelProbeLaunchDiscoveryPorts:
    discover_servers: Callable[..., dict[str, dict[str, Any]]]
    cached_external_capable: Callable[[], list[str]]
    channel_specs: Callable[[dict[str, Any], list[str]], list[str]]
    server_names: Callable[[Iterable[str]], list[str]]
    codex_capable_names: Callable[..., list[str]]
    external_names: Callable[..., list[str]]
    dedupe: Callable[[Iterable[str]], list[str]]


@dataclass(frozen=True, slots=True)
class ChannelProbeLaunchCachePorts:
    service: Callable[[], Any]
    read: Callable[[], dict[str, Any]]
    records: Callable[[], list[dict[str, Any]]]
    refresh: Callable[..., dict[str, Any]]
    bucket: Callable[[dict[str, Any]], str]
    panel_rows: Callable[[dict[str, Any]], tuple[list[str], list[str]]]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ChannelProbeLaunchEffects:
    delivery_mode: Callable[[dict[str, Any]], str]
    auto_start: Callable[..., list[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class ChannelProbeLaunchContext:
    discovery: ChannelProbeLaunchDiscoveryPorts
    cache: ChannelProbeLaunchCachePorts
    effects: ChannelProbeLaunchEffects
    native_channel_names: frozenset[str]

    def native_auto_capable_names(
        self, passthrough: list[str] | None = None
    ) -> list[str]:
        discovered = set(
            self.discovery.discover_servers(passthrough or []).keys()
        )
        if not discovered:
            return []
        return [
            name
            for name in self.discovery.cached_external_capable()
            if name in discovered
        ]

    def start_codex_sse(
        self,
        cfg: dict[str, Any],
        codex_mcp_config: Path | None,
        allowed_server_names: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        service = CodexChannelSseLaunchService(
            query=CodexChannelSseQueryPorts(
                delivery_mode=self.effects.delivery_mode,
                channel_specs=self.discovery.channel_specs,
                server_names=self.discovery.server_names,
                capable_names=self.discovery.codex_capable_names,
                dedupe=self.discovery.dedupe,
            ),
            effects=CodexChannelSseEffects(
                auto_start=self.effects.auto_start,
                log=self.cache.log,
            ),
            native_channel_names=frozenset(
                name.casefold() for name in self.native_channel_names
            ),
        )
        return service.start(cfg, codex_mcp_config, allowed_server_names)

    def summary(self, prefix: str, cache: dict[str, Any]) -> str:
        records = [
            record
            for record in cache.get("servers") or []
            if isinstance(record, dict)
        ]
        grouped = {
            name: [
                record for record in records if self.cache.bucket(record) == name
            ]
            for name in ("capable", "inconclusive", "non_capable")
        }
        return (
            f"{prefix}: {len(grouped['capable'])} channel-capable, "
            f"{len(grouped['inconclusive'])} inconclusive, "
            f"{len(grouped['non_capable'])} non-capable server(s)."
        )

    def panel_rows(
        self, cfg: dict[str, Any], passthrough: list[str]
    ) -> tuple[list[str], list[str], list[str]]:
        messages: list[str] = []
        if self.needs_refresh(cfg, passthrough):
            try:
                self.cache.log(
                    "INFO",
                    "channel_probe_menu_refresh "
                    "reason=missing_cache_or_selected_server",
                )
                result = self.cache.refresh(passthrough)
                messages = [self.summary("Probe complete", result)]
            except Exception as exc:
                self.cache.log(
                    "WARN",
                    "channel_probe_menu_refresh_failed "
                    f"error={type(exc).__name__}: {exc}",
                )
                messages = [
                    f"Channel probe failed: {type(exc).__name__}: {exc}"
                ]
        rows, values = self.cache.panel_rows(cfg)
        return rows, values, messages

    def candidate_names(
        self,
        cfg: dict[str, Any],
        passthrough: list[str],
        extra_config_paths: list[Path | str] | None = None,
    ) -> list[str]:
        return self.cache.service().candidate_names(
            self.discovery.channel_specs(cfg, passthrough),
            lambda: self.discovery.external_names(
                passthrough,
                extra_config_paths=extra_config_paths,
            ),
        )

    def needs_refresh(
        self,
        cfg: dict[str, Any],
        passthrough: list[str],
        extra_config_paths: list[Path | str] | None = None,
    ) -> bool:
        candidates = self.candidate_names(
            cfg, passthrough, extra_config_paths=extra_config_paths
        )
        return self.cache.service().needs_refresh(
            self.cache.read(), self.cache.records(), candidates
        )

    def ensure_cache(
        self,
        cfg: dict[str, Any],
        passthrough: list[str],
        extra_config_paths: list[Path | str] | None = None,
    ) -> bool:
        needed = self.needs_refresh(
            cfg, passthrough, extra_config_paths=extra_config_paths
        )
        return self.cache.service().ensure_refresh(
            needed,
            lambda: self.cache.refresh(
                passthrough,
                **(
                    {"extra_config_paths": extra_config_paths}
                    if extra_config_paths is not None
                    else {}
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class ChannelProbeLaunchCompatibilityApi:
    context: Callable[[], ChannelProbeLaunchContext]

    def native_auto_capable_names(self, *args: Any, **kwargs: Any) -> list[str]:
        return self.context().native_auto_capable_names(*args, **kwargs)

    def start_codex_sse(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return self.context().start_codex_sse(*args, **kwargs)

    def summary(self, *args: Any, **kwargs: Any) -> str:
        return self.context().summary(*args, **kwargs)

    def panel_rows(self, *args: Any, **kwargs: Any) -> tuple[list[str], list[str], list[str]]:
        return self.context().panel_rows(*args, **kwargs)

    def candidate_names(self, *args: Any, **kwargs: Any) -> list[str]:
        return self.context().candidate_names(*args, **kwargs)

    def needs_refresh(self, *args: Any, **kwargs: Any) -> bool:
        return self.context().needs_refresh(*args, **kwargs)

    def ensure_cache(self, *args: Any, **kwargs: Any) -> bool:
        return self.context().ensure_cache(*args, **kwargs)


__all__ = [
    "ChannelProbeLaunchCachePorts",
    "ChannelProbeLaunchCompatibilityApi",
    "ChannelProbeLaunchContext",
    "ChannelProbeLaunchDiscoveryPorts",
    "ChannelProbeLaunchEffects",
]
