"""Live API-key status and mutation command controller."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LiveApiKeyPorts:
    load_config: Callable[[], dict[str, Any]]
    current_provider: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]
    status_line: Callable[[str, dict[str, Any]], str]
    stored_mask: Callable[[str, dict[str, Any]], str]
    store_input: Callable[[str, str], list[str]]


class LiveApiKeyController:
    def __init__(self, ports: LiveApiKeyPorts) -> None:
        self.ports = ports

    def status(self, provider: str, provider_config: dict[str, Any]) -> list[str]:
        return [
            f"Live API key status for provider: {provider}",
            self.ports.status_line(provider, provider_config),
            f"Stored: {self.ports.stored_mask(provider, provider_config)}",
        ]

    def handle(self, value: str) -> tuple[list[str], bool]:
        config = self.ports.load_config()
        provider, provider_config = self.ports.current_provider(config)
        raw = str(value or "").strip()
        normalized = raw.lower()
        if normalized in {"", "status", "state", "show", "current", "now"}:
            return self.status(provider, provider_config), False
        if normalized in {"help", "usage", "list"}:
            return [
                "Use `/api-key status` to show masked key status.",
                "Use `/api-key clear` or `/api-key unset` to remove API keys for only the current provider.",
                "Use `/api-key KEY` to set one key.",
                "Use `/api-key KEY1,KEY2` or `/api-keys KEY1;KEY2` to set multiple round-robin keys.",
                "Raw keys are never echoed; responses show only masked keys and fingerprints.",
            ], False
        try:
            lines = self.ports.store_input(provider, raw)
        except SystemExit as exc:
            message = str(exc).strip() or "No API keys provided; unchanged."
            return [message, "", *self.status(provider, provider_config)], False
        config_after = self.ports.load_config()
        provider_after, provider_config_after = self.ports.current_provider(
            config_after
        )
        return lines + [
            "",
            "Updated live API key settings. The next model request uses these settings.",
            *self.status(provider_after, provider_config_after),
        ], True
