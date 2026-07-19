"""Ollama local and cloud provider adapters."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ..architecture import (
    ProviderCapabilities,
    ProviderConfigurationPolicy,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderModelCatalogPolicy,
    ProviderOptionPresentationPolicy,
    ProviderRequestPolicy,
    ProviderStatusPolicy,
)
from .base import HttpBearerProviderAdapter
from .constants import PROVIDER_DEFAULT_BASE_URLS


@dataclass(frozen=True)
class OllamaProviderAdapter(HttpBearerProviderAdapter):
    name: str = "ollama"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["ollama"]
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "Ollama"
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="ollama_chat",
            supports_thinking=True,
            local=True,
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/api/chat",
            models_path="/api/tags",
            model_info_path="/api/show",
            default_timeout_seconds=300.0,
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="ollama")
    )

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="ollama",
            settings_strategy="ollama",
            uses_catalog_timeout=True,
            preset_context_profile="ollama",
            status_capacity_strategy="ollama_budget",
        )

    def launch_model_strategy(self, config: ProviderConfig) -> str:
        del config
        return "ollama_unslug"

    def option_presentation_policy(
        self, config: ProviderConfig
    ) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(
            show_rate_limit=True,
            show_tool_choice=True,
            show_stream=True,
            show_rate_limit_controls=True,
            show_sampling_controls=True,
            show_ip_family_control=True,
        )

    def model_paths(self, config: ProviderConfig) -> tuple[str, ...]:
        del config
        return ("/api/tags", "/v1/models")

    def configuration_policy(
        self, config: ProviderConfig
    ) -> ProviderConfigurationPolicy:
        del config
        return ProviderConfigurationPolicy(
            mutation_strategy="ollama", uses_ollama_status=True
        )

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        return replace(
            super().status_policy(config),
            unreachable_hint="Start Ollama or set a reachable Base URL before launching Claude Code.",
        )


@dataclass(frozen=True)
class OllamaCloudProviderAdapter(OllamaProviderAdapter):
    name: str = "ollama-cloud"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["ollama-cloud"]
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="ollama_chat",
            supports_thinking=True,
            requires_api_key=True,
        )
    )
    api_key_display_name_value: str = "Ollama Cloud"
    api_key_launch_error_value: str = (
        "Launch blocked: Ollama Cloud requires an API key."
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(
            kind="ollama", use_bundled_catalog_fallback=True
        )
    )

    def normalize_model_id(self, model_id: str) -> str:
        normalized = super().normalize_model_id(model_id)
        return normalized[:-6] if normalized.endswith(":cloud") else normalized

    def launch_model_strategy(self, config: ProviderConfig) -> str:
        del config
        return "alias"

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="ollama",
            settings_strategy="ollama",
            hosted_timeout=True,
            timeout_weight=1.2,
            uses_catalog_timeout=True,
            preset_context_profile="ollama",
            status_capacity_strategy="ollama_budget",
        )


__all__ = ["OllamaCloudProviderAdapter", "OllamaProviderAdapter"]
