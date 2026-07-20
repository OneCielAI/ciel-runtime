"""Local router slash-command short-circuit controllers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol


class ShortcutHandler(Protocol):
    pass


@dataclass(frozen=True, slots=True)
class ShortcutResponsePorts:
    load_config: Callable[[], dict[str, Any]]
    current_alias: Callable[[dict[str, Any]], str]
    current_provider: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]
    write_anthropic: Callable[..., Any]
    publish_event: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ShortcutPredicates:
    router_debug: Callable[[dict[str, Any]], bool]
    version: Callable[[dict[str, Any]], bool]
    channel_clear: Callable[[dict[str, Any]], bool]
    live_llm_options: Callable[[dict[str, Any]], bool]
    live_api_keys: Callable[[dict[str, Any]], bool]


@dataclass(frozen=True, slots=True)
class RouterDebugShortcutPorts:
    value: Callable[[dict[str, Any]], str]
    external_enabled: Callable[[dict[str, Any]], bool]
    bind_host: Callable[[dict[str, Any]], str]
    set_external: Callable[[Any], list[str]]
    schedule_restart: Callable[[], Any]
    version: str
    source_fingerprint: str
    config_dir: Path


@dataclass(frozen=True, slots=True)
class ChannelShortcutPorts:
    value: Callable[[dict[str, Any]], str]
    clear: Callable[[], dict[str, Any]]
    status: Callable[[], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class LiveConfigShortcutPorts:
    llm_value: Callable[[dict[str, Any]], str]
    handle_llm: Callable[[str], tuple[list[str], bool]]
    api_key_value: Callable[[dict[str, Any]], str]
    handle_api_keys: Callable[[str], tuple[list[str], bool]]
    api_key_count: Callable[[str, dict[str, Any]], int]


@dataclass(frozen=True, slots=True)
class RouterShortcutController:
    response: ShortcutResponsePorts
    predicates: ShortcutPredicates
    debug: RouterDebugShortcutPorts
    channel: ChannelShortcutPorts
    live: LiveConfigShortcutPorts

    def _model_and_stream(self, body: dict[str, Any]) -> tuple[str, bool]:
        config = self.response.load_config()
        return (
            str(body.get("model") or self.response.current_alias(config)),
            bool(body.get("stream", True)),
        )

    def handle_router_debug(self, handler: ShortcutHandler, body: dict[str, Any]) -> bool:
        if not self.predicates.router_debug(body):
            return False
        config = self.response.load_config()
        current = self.debug.external_enabled(config)
        value = self.debug.value(body).strip().lower()
        should_restart = False
        if value in {"", "status", "state", "show", "?"}:
            lines = [
                f"Router debug external access: {'on' if current else 'off'}.",
                f"Current router bind host: {self.debug.bind_host(config)}.",
            ]
        elif value in {"toggle", "tog", "switch"}:
            lines = self.debug.set_external(not current)
            should_restart = True
        elif value in {"on", "true", "1", "enable", "enabled"}:
            lines = self.debug.set_external(True)
            should_restart = True
        elif value in {"off", "false", "0", "disable", "disabled"}:
            lines = self.debug.set_external(False)
            should_restart = True
        else:
            lines = [
                "Usage: `/router-debug`, `/router-debug on`, `/router-debug off`, or `/router-debug status`."
            ]
        if should_restart:
            lines.append("Router restart scheduled so the bind address changes immediately.")
        model, stream = self._model_and_stream(body)
        self.response.write_anthropic(handler, model, "\n".join(lines), stream)
        if should_restart:
            self.debug.schedule_restart()
        return True

    def handle_version(self, handler: ShortcutHandler, body: dict[str, Any]) -> bool:
        if not self.predicates.version(body):
            return False
        lines = [
            f"ciel-runtime {self.debug.version}",
            f"source: {self.debug.source_fingerprint[:12]}",
            f"config dir: {self.debug.config_dir}",
        ]
        model, stream = self._model_and_stream(body)
        self.response.write_anthropic(handler, model, "\n".join(lines), stream)
        return True

    @staticmethod
    def channel_status_lines(stats: dict[str, Any], cleared: bool) -> list[str]:
        if cleared:
            return [
                "Ciel Runtime channel backlog discarded.",
                f"- chat tail: {stats.get('chat_tail')}",
                f"- LLM cursor advanced by: {stats.get('discarded_llm')}",
                f"- MCP cursor advanced by: {stats.get('discarded_mcp')}",
                f"- active MCP channel sessions updated: {stats.get('mcp_sessions_updated')}",
                "New channel events arriving after this point will still be delivered.",
            ]
        return [
            "Ciel Runtime channel backlog status.",
            f"- chat tail: {stats.get('chat_tail')}",
            f"- pending LLM items by id range: {stats.get('pending_llm')}",
            f"- pending MCP items by id range: {stats.get('pending_mcp')}",
            f"- active MCP channel sessions: {stats.get('mcp_sessions')}",
        ]

    def handle_channel_clear(self, handler: ShortcutHandler, body: dict[str, Any]) -> bool:
        if not self.predicates.channel_clear(body):
            return False
        value = self.channel.value(body).strip().lower()
        if value in {"", "all", "clear", "discard", "drop", "purge", "reset", "now"}:
            lines = self.channel_status_lines(self.channel.clear(), cleared=True)
        elif value in {"status", "state", "show", "?", "dry-run", "dryrun"}:
            lines = self.channel_status_lines(self.channel.status(), cleared=False)
        else:
            lines = ["Usage: `/channel-clear`, `/channel-clear all`, or `/channel-clear status`."]
        model, stream = self._model_and_stream(body)
        self.response.write_anthropic(handler, model, "\n".join(lines), stream)
        return True

    def handle_live_llm_options(
        self, handler: ShortcutHandler, body: dict[str, Any]
    ) -> bool:
        if not self.predicates.live_llm_options(body):
            return False
        value = self.live.llm_value(body)
        lines, changed = self.live.handle_llm(value)
        if changed:
            provider = self.response.current_provider(self.response.load_config())[0]
            self.response.publish_event(
                level="info",
                category="config.llm",
                message="live LLM options updated from slash command",
                provider=provider,
                data={"value": value},
            )
        model, stream = self._model_and_stream(body)
        self.response.write_anthropic(handler, model, "\n".join(lines), stream)
        return True

    def handle_live_api_keys(
        self, handler: ShortcutHandler, body: dict[str, Any]
    ) -> bool:
        if not self.predicates.live_api_keys(body):
            return False
        value = self.live.api_key_value(body)
        lines, changed = self.live.handle_api_keys(value)
        if changed:
            provider, config = self.response.current_provider(self.response.load_config())
            self.response.publish_event(
                level="info",
                category="config.api_key",
                message="live API key settings updated from slash command",
                provider=provider,
                data={"key_count": self.live.api_key_count(provider, config)},
            )
        model, stream = self._model_and_stream(body)
        self.response.write_anthropic(handler, model, "\n".join(lines), stream)
        return True


__all__ = [
    "ChannelShortcutPorts",
    "LiveConfigShortcutPorts",
    "RouterDebugShortcutPorts",
    "RouterShortcutController",
    "ShortcutPredicates",
    "ShortcutResponsePorts",
]
