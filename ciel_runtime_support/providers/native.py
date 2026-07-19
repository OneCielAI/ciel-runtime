"""Native Codex and AGY provider-selection adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..architecture import (
    ProviderCapabilities,
    ProviderConfigurationPolicy,
    ProviderConfig,
    ProviderModelCatalogPolicy,
    ProviderOptionPresentationPolicy,
    ProviderRequestPolicy,
    ProviderStatusPolicy,
    ProviderUiPolicy,
)
from .base import NoAuthProviderAdapter
from .constants import PROVIDER_DEFAULT_BASE_URLS


@dataclass(frozen=True)
class CodexProviderAdapter(NoAuthProviderAdapter):
    name: str = "codex"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["codex"]
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="openai_responses"
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/v1/responses", models_path="/v1/models"
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="configured")
    )

    def routing_mode_update(self, enabled: bool) -> tuple[str, ...]:
        return (
            "Codex routing mode updated.",
            f"mode: {'codex-routed' if enabled else 'codex-native'}",
        )

    def selection_config_updates(self, config: ProviderConfig) -> Mapping[str, Any]:
        del config
        return {"route_through_router": False}

    def selection_status_lines(self, config: ProviderConfig) -> tuple[str, ...]:
        del config
        return ("mode: codex-native",)

    def option_presentation_policy(
        self, config: ProviderConfig
    ) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(show_route=True)

    def ui_policy(self, config: ProviderConfig) -> ProviderUiPolicy:
        del config
        return ProviderUiPolicy(
            menu_label="Codex Native",
            routed_menu_label="Codex routed",
            native_choice="codex:native",
            routed_choice="codex:routed",
            model_placeholder="Codex default",
            advisor_placeholder="Codex native",
        )

    def shows_claude_workflow_options(self, config: ProviderConfig) -> bool:
        del config
        return False

    def option_timeout_default(self) -> str:
        return "Codex default"

    def api_key_status(
        self, config: ProviderConfig, *, key_count: int, primary_detail: str
    ) -> str:
        routed = bool(config.options.get("route_through_router"))
        if routed:
            if key_count > 1:
                return (
                    f"API keys: {key_count} keys, round-robin "
                    f"(stored; Codex routed uses native login/auth headers{primary_detail})"
                )
            return (
                f"API key: set (stored; Codex routed uses native login/auth headers{primary_detail})"
                if key_count
                else "API key: not set (uses native Codex login/auth headers)"
            )
        if key_count > 1:
            return f"API keys: {key_count} keys, round-robin (Codex fallback{primary_detail})"
        return (
            f"API key: set (Codex fallback{primary_detail})"
            if key_count
            else "API key: not set (uses native Codex login/config)"
        )

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(kind="native_codex")

    def configuration_policy(
        self, config: ProviderConfig
    ) -> ProviderConfigurationPolicy:
        del config
        return ProviderConfigurationPolicy(
            runtime_owns_model=True, restricts_runtime_options=True
        )


@dataclass(frozen=True)
class AgyProviderAdapter(NoAuthProviderAdapter):
    name: str = "agy"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["agy"]
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="openai_responses"
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="configured")
    )

    def routing_mode_update(self, enabled: bool) -> tuple[str, ...]:
        return (
            "AGY routing mode updated.",
            f"mode: {'agy-routed' if enabled else 'agy-native'}",
        )

    def selection_config_updates(self, config: ProviderConfig) -> Mapping[str, Any]:
        del config
        return {"route_through_router": False}

    def selection_status_lines(self, config: ProviderConfig) -> tuple[str, ...]:
        del config
        return ("mode: agy-native",)

    def option_presentation_policy(
        self, config: ProviderConfig
    ) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(show_route=True)

    def ui_policy(self, config: ProviderConfig) -> ProviderUiPolicy:
        del config
        return ProviderUiPolicy(
            menu_label="AGY",
            routed_menu_label="AGY Routed",
            native_choice="agy:native",
            routed_choice="agy:routed",
            model_placeholder="AGY default",
            advisor_placeholder="AGY native",
        )

    def option_timeout_default(self) -> str:
        return "AGY default"

    def api_key_status(
        self, config: ProviderConfig, *, key_count: int, primary_detail: str
    ) -> str:
        del key_count, primary_detail
        return (
            "API key: not set (uses native AGY Google sign-in/keyring)"
            if config.options.get("route_through_router")
            else "API key: not set (uses native AGY Google sign-in/config)"
        )

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(kind="native_agy")

    def configuration_policy(
        self, config: ProviderConfig
    ) -> ProviderConfigurationPolicy:
        del config
        return ProviderConfigurationPolicy(runtime_owns_model=True)


__all__ = ["AgyProviderAdapter", "CodexProviderAdapter"]
