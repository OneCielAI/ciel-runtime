"""CLI controller for Channel configuration and capability probing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True, slots=True)
class ChannelCliView:
    load_config: Callable[[], dict[str, Any]]
    status_text: Callable[[dict[str, Any]], str]
    delivery_mode: Callable[[dict[str, Any] | None], str]
    configured_specs: Callable[[dict[str, Any]], list[str]]
    official_plugins: Mapping[str, str]
    output: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class ChannelCliCommands:
    add: Callable[[str], list[str]]
    development: Callable[[bool], list[str]]
    remove: Callable[[str], list[str]]
    clear: Callable[[], list[str]]
    refresh: Callable[[], dict[str, Any]]
    report: Callable[[dict[str, Any]], list[str]]
    set_delivery: Callable[[Any], list[str]]


class ChannelCliController:
    def __init__(self, view: ChannelCliView, commands: ChannelCliCommands) -> None:
        self.view = view
        self.commands = commands

    def run(self, args: Any) -> None:
        config = self.view.load_config()
        values = list(getattr(args, "values", []) or [])
        if not values:
            self._status(config)
            return
        head = values[0].strip().lower()
        if head in ("on", "enable", "add"):
            self._require_value(values, "Usage: ciel-runtime channels add CHANNEL_SPEC")
            self._lines(self.commands.add(values[1]))
            return
        if head in ("dev", "development"):
            if len(values) >= 2 and values[1].lower() in (
                "on",
                "off",
                "true",
                "false",
                "1",
                "0",
            ):
                self._lines(self.commands.development(True))
                return
            self._require_value(values, "Usage: ciel-runtime channels add CHANNEL_SPEC")
            self._lines(self.commands.add(values[1]))
            return
        if head in ("off", "disable", "remove", "rm"):
            self._require_value(values, "Usage: ciel-runtime channels remove CHANNEL_SPEC")
            self._lines(self.commands.remove(values[1]))
            return
        if head in ("clear", "reset"):
            self._lines(self.commands.clear())
            return
        if head in ("detect", "probe", "refresh"):
            try:
                result = self.commands.refresh()
            except Exception as exc:
                raise SystemExit(
                    f"Channel probe failed: {type(exc).__name__}: {exc}"
                ) from exc
            self._lines(self.commands.report(result))
            return
        if head in ("delivery", "mode"):
            if len(values) < 2:
                self.view.output(f"channel_delivery: {self.view.delivery_mode(config)}")
            else:
                self._lines(self.commands.set_delivery(values[1]))
            return
        if head in self.view.official_plugins:
            spec = self.view.official_plugins[head]
            command = (
                self.commands.remove
                if spec in self.view.configured_specs(config)
                else self.commands.add
            )
            self._lines(command(spec))
            return
        self._lines(self.commands.add(values[0]))

    def delivery(self, args: Any) -> None:
        value = getattr(args, "value", None)
        if value:
            self._lines(self.commands.set_delivery(value))
        else:
            self.view.output(f"channel_delivery: {self.view.delivery_mode(None)}")

    def _status(self, config: dict[str, Any]) -> None:
        self.view.output(f"channels: {self.view.status_text(config)}")
        self.view.output(f"delivery: {self.view.delivery_mode(config)}")
        specs = self.view.configured_specs(config)
        for name, spec in self.view.official_plugins.items():
            mark = "*" if spec in specs else " "
            self.view.output(f" {mark} {name:<10} {spec}")
        official = set(self.view.official_plugins.values())
        for spec in specs:
            if spec not in official:
                self.view.output(f" * custom    {spec}")

    @staticmethod
    def _require_value(values: list[str], message: str) -> None:
        if len(values) < 2:
            raise SystemExit(message)

    def _lines(self, lines: list[str]) -> None:
        for line in lines:
            self.view.output(line)
