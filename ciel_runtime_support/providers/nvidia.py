"""NVIDIA hosted provider adapter."""

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
from .base import OpenAICompatibleProviderAdapter, configuration_policy
from .constants import PROVIDER_DEFAULT_BASE_URLS


@dataclass(frozen=True)
class NvidiaHostedProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "nvidia-hosted"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["nvidia-hosted"]
    api_key_display_name_value: str = "NVIDIA"
    api_key_launch_error_value: str = "Launch blocked: NVIDIA hosted requires an NVIDIA API key."
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="openai_chat", requires_api_key=True
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="nvidia")
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/v1/chat/completions",
            models_path="/v1/models",
            model_alias_strategy="ncp",
            stream_required=True,
        )
    )

    def configuration_policy(self, config: ProviderConfig) -> ProviderConfigurationPolicy:
        del config
        return configuration_policy(
            native_compat_error=(
                "nvidia-hosted does not expose Anthropic /v1/messages; use router mode. "
                "Use self-hosted-nim or vLLM for native /v1/messages."
            ),
            status_fields=(
                "context_window",
                "max_output_tokens",
                "request_timeout_ms",
                "stream_idle_timeout_ms",
            ),
        )

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(kind="nvidia", label="NVIDIA hosted")

    def preserves_claude_model_alias(self, model_id: str) -> bool:
        return str(model_id).startswith("claude-")

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="nvidia",
            settings_strategy="standard",
            hosted_timeout=True,
            preset_context_profile="nvidia",
            status_capacity_strategy="openai_budget",
        )

    def router_native_anthropic_enabled(
        self, config: ProviderConfig, model: str | None = None
    ) -> bool:
        del config, model
        return False

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
        return replace(
            super().option_presentation_policy(config), show_rate_limit=True, show_native=False
        )


__all__ = ["NvidiaHostedProviderAdapter"]
