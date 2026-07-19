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
from .base import HttpBearerProviderAdapter, provider_configuration
from .constants import DEFAULT_REQUEST_TIMEOUT_MS, PROVIDER_DEFAULT_BASE_URLS


@dataclass(frozen=True)
class OllamaProviderAdapter(HttpBearerProviderAdapter):
    name: str = "ollama"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["ollama"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            "qwen3-coder",
            api_key="ollama",
            custom_models=("qwen3-coder",),
            native_compat=True,
            rate_limit_rpm=0,
            rate_limit_status=False,
            num_ctx="auto",
            num_ctx_min=32768,
            num_ctx_max=131072,
            keep_alive="5m",
            think=False,
            request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            stream_enabled=True,
            stream_word_chunking=False,
            ollama_options={
                "temperature": 0.7,
                "top_p": 0.8,
                "top_k": 40,
                "num_predict": 4096,
            },
        )
    )
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
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            "glm-5.1",
            custom_models=("glm-5.1",),
            rate_limit_rpm=0,
            rate_limit_status=False,
            num_ctx="auto",
            num_ctx_min=32768,
            num_ctx_max=131072,
            keep_alive="5m",
            think=True,
            request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            stream_enabled=True,
            stream_word_chunking=False,
            ollama_options={
                "temperature": 0.7,
                "top_p": 0.8,
                "top_k": 40,
                "num_predict": 4096,
            },
        )
    )
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
