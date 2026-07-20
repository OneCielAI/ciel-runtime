"""Provider/runtime choice normalization and selection strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .architecture import ProviderAdapter, ProviderConfig


ANTHROPIC_NATIVE_PROVIDER_CHOICE = "anthropic:native"
ANTHROPIC_ROUTED_PROVIDER_CHOICE = "anthropic:routed"
AGY_NATIVE_PROVIDER_CHOICE = "agy:native"
AGY_ROUTED_PROVIDER_CHOICE = "agy:routed"
CODEX_NATIVE_PROVIDER_CHOICE = "codex:native"
CODEX_ROUTED_PROVIDER_CHOICE = "codex:routed"


CHOICE_ALIASES = {
    "anthropic-native": ANTHROPIC_NATIVE_PROVIDER_CHOICE,
    "claude-native": ANTHROPIC_NATIVE_PROVIDER_CHOICE,
    "native": ANTHROPIC_NATIVE_PROVIDER_CHOICE,
    "claude-code": ANTHROPIC_NATIVE_PROVIDER_CHOICE,
    "anthropic-routed": ANTHROPIC_ROUTED_PROVIDER_CHOICE,
    "anthropic-router": ANTHROPIC_ROUTED_PROVIDER_CHOICE,
    "claude-routed": ANTHROPIC_ROUTED_PROVIDER_CHOICE,
    "claude-router": ANTHROPIC_ROUTED_PROVIDER_CHOICE,
    "agy": AGY_NATIVE_PROVIDER_CHOICE,
    "agy-native": AGY_NATIVE_PROVIDER_CHOICE,
    "native-agy": AGY_NATIVE_PROVIDER_CHOICE,
    "antigravity": AGY_NATIVE_PROVIDER_CHOICE,
    "google-antigravity": AGY_NATIVE_PROVIDER_CHOICE,
    "agy-routed": AGY_ROUTED_PROVIDER_CHOICE,
    "agy-router": AGY_ROUTED_PROVIDER_CHOICE,
    "routed-agy": AGY_ROUTED_PROVIDER_CHOICE,
    "antigravity-routed": AGY_ROUTED_PROVIDER_CHOICE,
    "codex": CODEX_NATIVE_PROVIDER_CHOICE,
    "codex-native": CODEX_NATIVE_PROVIDER_CHOICE,
    "native-codex": CODEX_NATIVE_PROVIDER_CHOICE,
    "codex-routed": CODEX_ROUTED_PROVIDER_CHOICE,
    "codex-router": CODEX_ROUTED_PROVIDER_CHOICE,
    "routed-codex": CODEX_ROUTED_PROVIDER_CHOICE,
}


@dataclass(frozen=True, slots=True)
class ProviderChoiceStrategy:
    provider: str
    routed: bool
    status_lines: tuple[str, ...]
    missing_api_key_line: str = ""


CHOICE_STRATEGIES = {
    ANTHROPIC_NATIVE_PROVIDER_CHOICE: ProviderChoiceStrategy(
        provider="anthropic",
        routed=False,
        status_lines=(
            "Provider set to anthropic (Claude Native).",
            "mode: anthropic-native",
            "Claude Code OAuth/Max can be used directly, but ciel-runtime router features such as /advisor are unavailable.",
        ),
    ),
    ANTHROPIC_ROUTED_PROVIDER_CHOICE: ProviderChoiceStrategy(
        provider="anthropic",
        routed=True,
        status_lines=(
            "Provider set to anthropic (Anthropic routed).",
            "mode: anthropic-routed",
        ),
        missing_api_key_line="Anthropic routed mode will use Claude Code OAuth/API auth headers when available.",
    ),
    AGY_NATIVE_PROVIDER_CHOICE: ProviderChoiceStrategy(
        provider="agy",
        routed=False,
        status_lines=(
            "Provider set to agy (AGY).",
            "mode: agy-native",
            "AGY runs with its own native settings; Ciel Runtime router features are unavailable.",
        ),
    ),
    AGY_ROUTED_PROVIDER_CHOICE: ProviderChoiceStrategy(
        provider="agy",
        routed=True,
        status_lines=(
            "Provider set to agy (AGY routed).",
            "mode: agy-routed",
            "AGY uses native Google Antigravity auth/settings; Ciel Runtime adds channel/PTY wake support only.",
        ),
    ),
    CODEX_NATIVE_PROVIDER_CHOICE: ProviderChoiceStrategy(
        provider="codex",
        routed=False,
        status_lines=(
            "Provider set to codex (Codex Native).",
            "mode: codex-native",
            "Codex runs with its own native settings; ciel-runtime router features are unavailable.",
        ),
    ),
    CODEX_ROUTED_PROVIDER_CHOICE: ProviderChoiceStrategy(
        provider="codex",
        routed=True,
        status_lines=(
            "Provider set to codex (Codex routed).",
            "mode: codex-routed",
            "Codex uses its native OpenAI account/config, with base URL routed through ciel-runtime.",
        ),
    ),
}


def normalize_provider_choice(name: str) -> str | None:
    raw = str(name or "").strip().lower().replace("_", "-").replace(" ", "-")
    if raw in CHOICE_STRATEGIES:
        return raw
    return CHOICE_ALIASES.get(raw.replace(":", "-"))


@dataclass(frozen=True, slots=True)
class ProviderChoicePorts:
    load_config: Callable[[], dict[str, Any]]
    save_config: Callable[[dict[str, Any]], None]
    clear_model_cache: Callable[[], None]
    provider_has_api_key: Callable[[str, dict[str, Any]], bool]
    configured_adapter: Callable[[str, dict[str, Any]], ProviderAdapter]
    contract_config: Callable[[str, dict[str, Any]], ProviderConfig]
    provider_label: Callable[[str], str]


class ProviderChoiceController:
    def __init__(self, ports: ProviderChoicePorts) -> None:
        self._ports = ports

    def select(self, choice: str) -> list[str]:
        normalized = normalize_provider_choice(choice) or choice
        strategy = CHOICE_STRATEGIES.get(normalized)
        if strategy is None:
            return self.select_standard(normalized)
        config = self._ports.load_config()
        config["current_provider"] = strategy.provider
        provider_config = config["providers"][strategy.provider]
        provider_config["route_through_router"] = strategy.routed
        self._ports.save_config(config)
        self._ports.clear_model_cache()
        lines = list(strategy.status_lines)
        if (
            strategy.missing_api_key_line
            and not self._ports.provider_has_api_key(strategy.provider, provider_config)
        ):
            lines.append(strategy.missing_api_key_line)
        return lines

    def select_standard(self, provider: str) -> list[str]:
        """Select a provider while delegating normalization to its adapter."""

        config = self._ports.load_config()
        config["current_provider"] = provider
        provider_config = config["providers"][provider]
        adapter = self._ports.configured_adapter(provider, provider_config)
        contract = self._ports.contract_config(provider, provider_config)
        updates = adapter.selection_config_updates(contract)
        provider_config.update(updates)
        self._ports.save_config(config)
        self._ports.clear_model_cache()
        selected_contract = self._ports.contract_config(provider, provider_config)
        lines = [f"Provider set to {provider} ({self._ports.provider_label(provider)})."]
        lines.extend(adapter.selection_status_lines(selected_contract))
        lines.extend(adapter.selection_update_status_lines(selected_contract, updates))
        return lines


__all__ = [
    "AGY_NATIVE_PROVIDER_CHOICE",
    "AGY_ROUTED_PROVIDER_CHOICE",
    "ANTHROPIC_NATIVE_PROVIDER_CHOICE",
    "ANTHROPIC_ROUTED_PROVIDER_CHOICE",
    "CODEX_NATIVE_PROVIDER_CHOICE",
    "CODEX_ROUTED_PROVIDER_CHOICE",
    "ProviderChoiceController",
    "ProviderChoicePorts",
    "ProviderChoiceStrategy",
    "normalize_provider_choice",
]
