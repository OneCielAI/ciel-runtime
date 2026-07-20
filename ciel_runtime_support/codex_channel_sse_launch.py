"""Codex MCP channel SSE launch ownership and capability policy."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class CodexChannelSseQueryPorts:
    delivery_mode: Callable[[dict[str, Any]], str]
    channel_specs: Callable[[dict[str, Any], list[str]], list[str]]
    server_names: Callable[[Iterable[str]], list[str]]
    capable_names: Callable[[dict[str, Any], Path], list[str]]
    dedupe: Callable[[Iterable[str]], list[str]]


@dataclass(frozen=True, slots=True)
class CodexChannelSseEffects:
    auto_start: Callable[..., list[dict[str, Any]]]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class CodexChannelSseLaunchService:
    query: CodexChannelSseQueryPorts
    effects: CodexChannelSseEffects
    native_channel_names: frozenset[str]

    def start(
        self,
        config: dict[str, Any],
        mcp_config: Path | None,
        allowed_server_names: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not mcp_config or self.query.delivery_mode(config) != "llm":
            return []
        if not mcp_config.exists() or not mcp_config.is_file():
            return []
        explicit = {
            name
            for name in self.query.server_names(
                self.query.channel_specs(config, [])
            )
            if name.strip().casefold() not in self.native_channel_names
        }
        if allowed_server_names is None:
            names = self.query.capable_names(config, mcp_config)
        else:
            names = self.query.dedupe(
                str(name or "").strip()
                for name in allowed_server_names
                if str(name or "").strip()
                and str(name or "").strip().casefold()
                not in self.native_channel_names
            )
        names = [name for name in names if name not in explicit]
        if not names:
            self.effects.log(
                "INFO",
                "codex_channel_sse_skipped "
                "reason=no_capable_unowned_codex_mcp "
                "allowed=%s explicit=%s"
                % (
                    ",".join(names) or "-",
                    ",".join(sorted(explicit)) or "-",
                ),
            )
            return []
        started = self.effects.auto_start(
            [],
            extra_config_paths=[mcp_config],
            allowed_server_names=names,
            include_default_paths=False,
        )
        self.effects.log(
            "INFO",
            "codex_channel_sse_started count=%d servers=%s"
            % (
                len(started),
                ",".join(str(item.get("name") or "") for item in started)
                or "-",
            ),
        )
        return started
