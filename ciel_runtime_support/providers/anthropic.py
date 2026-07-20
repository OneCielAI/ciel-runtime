"""Anthropic native and routed provider adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..architecture import (
    ProviderCapabilities,
    ProviderConfigurationPolicy,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderModelCatalogPolicy,
    ProviderOptionPresentationPolicy,
    ProviderRequestPolicy,
    ProviderUiPolicy,
)
from .base import NoAuthProviderAdapter, provider_configuration
from .constants import PROVIDER_DEFAULT_BASE_URLS


@dataclass(frozen=True)
class AnthropicProviderAdapter(NoAuthProviderAdapter):
    name: str = "anthropic"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["anthropic"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            "claude-sonnet-4-6", route_through_router=False
        )
    )
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="anthropic_messages",
            supports_thinking=True,
            preserves_anthropic_thinking=True,
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/v1/messages",
            models_path="/v1/models",
            credential_strategy="anthropic_inbound",
            normalize_historical_tool_turns=False,
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="anthropic")
    )

    def advisor_panel_notice(
        self, config: ProviderConfig
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        del config
        return (
            (
                "Claude native and Anthropic routed sessions use Claude Code's",
                "built-in /advisor (run /advisor in the session to pick its model).",
                "Back",
            ),
            ("back", "back", "back"),
        )

    def configuration_policy(
        self, config: ProviderConfig
    ) -> ProviderConfigurationPolicy:
        del config
        return ProviderConfigurationPolicy(
            supports_route_through_router=True, restricts_runtime_options=True
        )

    def supports_server_advisor_tool(self, config: ProviderConfig) -> bool:
        del config
        return True

    def context_compaction_available(self, config: ProviderConfig) -> bool:
        return bool(config.api_keys)

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        routed = bool(config.options.get("route_through_router"))
        return ProviderContextPolicy(
            capacity_strategy="anthropic_hint" if routed else "managed",
            hosted_timeout=True,
            managed_preset_inference=True,
            status_capacity_strategy="provider" if routed else "configured",
        )

    def intercepts_advisor_shortcut(self, config: ProviderConfig) -> bool:
        del config
        return False

    def routing_mode_update(self, enabled: bool) -> tuple[str, ...]:
        mode = (
            "routed through ciel-runtime router" if enabled else "direct Claude Native"
        )
        return ("Anthropic routing mode updated.", f"mode: {mode}")

    def selection_config_updates(self, config: ProviderConfig) -> Mapping[str, Any]:
        del config
        return {"route_through_router": False}

    def selection_status_lines(self, config: ProviderConfig) -> tuple[str, ...]:
        del config
        return ("mode: anthropic-native",)

    def option_presentation_policy(
        self, config: ProviderConfig
    ) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(
            show_route=True, show_ip_family_control=True
        )

    def ui_policy(self, config: ProviderConfig) -> ProviderUiPolicy:
        del config
        return ProviderUiPolicy(
            menu_label="Claude Native",
            routed_menu_label="Anthropic routed",
            native_choice="anthropic:native",
            routed_choice="anthropic:routed",
            advisor_placeholder="Claude Code native /advisor",
            uses_native_advisor=True,
        )

    def option_timeout_default(self) -> str:
        return "Claude Code default"

    def propagates_inbound_beta_query(self, config: ProviderConfig) -> bool:
        del config
        return True

    def api_key_status(
        self, config: ProviderConfig, *, key_count: int, primary_detail: str
    ) -> str:
        routed = bool(config.options.get("route_through_router"))
        scope = "Anthropic routed" if routed else "Anthropic"
        if key_count > 1:
            return f"API keys: {key_count} keys, round-robin ({scope}{primary_detail})"
        if key_count:
            return f"API key: set ({scope}{primary_detail})"
        return (
            "API key: not set (uses Claude Code OAuth/API auth headers)"
            if routed
            else "API key: not set (use API key or Claude login)"
        )

    def build_model_headers(
        self, config: ProviderConfig, api_key: str | None
    ) -> Mapping[str, str]:
        del config
        key = str(api_key or "").strip()
        return (
            {"anthropic-version": "2023-06-01", "x-api-key": key}
            if key
            else {"anthropic-version": "2023-06-01"}
        )


__all__ = ["AnthropicProviderAdapter"]
