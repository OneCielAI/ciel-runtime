"""Application service for persisted Channel configuration and CLI imports."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ChannelConfigPorts:
    load: Callable[[], dict[str, Any]]
    save: Callable[[dict[str, Any]], None]
    invalidate: Callable[[], None]
    configured_specs: Callable[[dict[str, Any]], list[str]]
    dedupe: Callable[[Iterable[str]], list[str]]
    log: Callable[[str, str], None]
    environment: Mapping[str, str]


class ChannelConfigService:
    def __init__(
        self,
        builtin_spec: str,
        ports: ChannelConfigPorts,
    ) -> None:
        self.builtin_spec = builtin_spec
        self.ports = ports

    @staticmethod
    def is_tagged(spec: str) -> bool:
        return spec.startswith("plugin:") or spec.startswith("server:")

    def parse_passthrough(self, passthrough: list[str]) -> list[str]:
        specs: list[str] = []
        index = 0
        options = ("--channels", "--dangerously-load-development-channels")
        while index < len(passthrough):
            argument = passthrough[index]
            if argument in options:
                index += 1
                while index < len(passthrough) and self.is_tagged(passthrough[index]):
                    specs.append(passthrough[index])
                    index += 1
                continue
            if any(argument.startswith(option + "=") for option in options):
                value = argument.split("=", 1)[1].strip()
                if value and self.is_tagged(value):
                    specs.append(value)
            index += 1
        return self.ports.dedupe(specs)

    def auto_import(self, passthrough: list[str]) -> list[str]:
        specs = self.parse_passthrough(passthrough)
        if not specs:
            return []
        config = self.ports.load()
        existing = set(self.ports.configured_specs(config))
        if all(spec in existing for spec in specs):
            return []
        merged = [
            spec
            for spec in self.ports.configured_specs(config)
            if spec != self.builtin_spec
        ]
        added: list[str] = []
        for spec in specs:
            if spec not in existing and spec != self.builtin_spec:
                merged.append(spec)
                existing.add(spec)
                added.append(spec)
        if not added:
            return []
        config.setdefault("claude_code", {})["channels"] = merged
        self.ports.save(config)
        self.ports.invalidate()
        self.ports.log(
            "INFO",
            f"channels_auto_imported_from_passthrough count={len(added)} "
            f"specs={','.join(added)}",
        )
        return added

    def launch_specs(
        self,
        config: dict[str, Any],
        extra_specs: list[str] | None = None,
    ) -> list[str]:
        specs = [
            spec
            for spec in self.ports.configured_specs(config)
            if self.is_tagged(spec)
        ]
        if extra_specs:
            specs.extend(extra_specs)
        return self.ports.dedupe(spec for spec in specs if self.is_tagged(spec))

    @staticmethod
    def normalize_delivery(value: Any) -> str:
        text = str(value or "").strip().lower().replace("_", "-")
        if text in {
            "native",
            "native-channel",
            "native-channel-bridge",
            "claude-channel",
            "claude/native",
        }:
            return "native"
        if text in {
            "stdin",
            "pty",
            "terminal",
            "wake",
            "wake-proxy",
            "legacy",
        }:
            return "stdin"
        return "llm"

    def delivery_mode(self, config: dict[str, Any] | None = None) -> str:
        environment_value = self.ports.environment.get("CIEL_RUNTIME_CHANNEL_DELIVERY")
        if environment_value is not None:
            return self.normalize_delivery(environment_value)
        config = config or self.ports.load()
        value = config.setdefault("claude_code", {}).get("channel_delivery", "llm")
        return self.normalize_delivery(value)

    def set_delivery(self, value: Any) -> list[str]:
        mode = self.normalize_delivery(value)
        config = self.ports.load()
        config.setdefault("claude_code", {})["channel_delivery"] = mode
        self.ports.save(config)
        messages = {
            "native": "Channel delivery set to native claude/channel bridge.",
            "llm": "Channel delivery set to LLM context injection.",
            "stdin": "Channel delivery set to stdin wake proxy.",
        }
        return [messages[mode]]

    def add(self, spec: str) -> list[str]:
        spec = spec.strip()
        if not spec:
            return ["Channel spec was empty."]
        if not self.is_tagged(spec):
            return ["Channel spec must start with plugin: or server:."]
        if spec == self.builtin_spec:
            return ["Ciel Runtime router channel is always enabled."]
        config = self.ports.load()
        channels = [
            item
            for item in self.ports.configured_specs(config)
            if item != self.builtin_spec
        ]
        if spec not in channels:
            channels.append(spec)
        config.setdefault("claude_code", {})["channels"] = channels
        self.ports.save(config)
        return [f"Channel added: {spec}."]

    def remove(self, spec: str) -> list[str]:
        if spec == self.builtin_spec:
            return ["Ciel Runtime router channel is always enabled and cannot be removed."]
        config = self.ports.load()
        before = [
            item
            for item in self.ports.configured_specs(config)
            if item != self.builtin_spec
        ]
        after = [item for item in before if item != spec]
        config.setdefault("claude_code", {})["channels"] = after
        self.ports.save(config)
        message = (
            f"Channel removed: {spec}."
            if len(after) != len(before)
            else f"Channel was not configured: {spec}."
        )
        return [message]

    def clear(self) -> list[str]:
        config = self.ports.load()
        config.setdefault("claude_code", {})["channels"] = []
        self.ports.save(config)
        return [
            "External Claude Code channels cleared. Ciel Runtime router remains enabled."
        ]


@dataclass(frozen=True, slots=True)
class ChannelConfigApi:
    """Explicit public adapter for late-bound Channel configuration services."""

    service_factory: Callable[[], ChannelConfigService]

    def parse_passthrough_channel_specs(self, passthrough: list[str]) -> list[str]:
        return self.service_factory().parse_passthrough(passthrough)

    def auto_import_passthrough_channels(self, passthrough: list[str]) -> list[str]:
        return self.service_factory().auto_import(passthrough)

    def channel_specs_for_launch(
        self,
        cfg: dict[str, Any],
        passthrough: list[str],
        extra_specs: list[str] | None = None,
    ) -> list[str]:
        del passthrough
        return self.service_factory().launch_specs(cfg, extra_specs)

    def is_channel_spec_tagged(self, spec: str) -> bool:
        return self.service_factory().is_tagged(spec)

    def normalize_channel_delivery(self, value: Any) -> str:
        return self.service_factory().normalize_delivery(value)

    def channel_delivery_mode(self, cfg: dict[str, Any] | None = None) -> str:
        return self.service_factory().delivery_mode(cfg)

    def set_channel_delivery_config(self, value: Any) -> list[str]:
        return self.service_factory().set_delivery(value)

    def add_channel_spec(
        self, spec: str, *, development: bool = False
    ) -> list[str]:
        del development
        return self.service_factory().add(spec)

    def remove_channel_spec(self, spec: str) -> list[str]:
        return self.service_factory().remove(spec)

    def clear_channel_specs(self) -> list[str]:
        return self.service_factory().clear()
