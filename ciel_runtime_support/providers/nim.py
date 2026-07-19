"""Self-hosted NVIDIA NIM provider adapter."""

from dataclasses import dataclass, field, replace

from ..architecture import (
    ProviderCapabilities,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderOptionPresentationPolicy,
    ProviderStatusPolicy,
)
from .base import OpenAICompatibleProviderAdapter, provider_configuration
from .constants import DEFAULT_REQUEST_TIMEOUT_MS, PROVIDER_DEFAULT_BASE_URLS


@dataclass(frozen=True)
class SelfHostedNimProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "self-hosted-nim"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["self-hosted-nim"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            "model",
            api_key="not-used",
            custom_models=("model",),
            native_compat=True,
            rate_limit_rpm=0,
            rate_limit_status=False,
            context_window=32768,
            max_output_tokens=4096,
            temperature=0.7,
            top_p=0.8,
            context_reserve_tokens=1024,
            request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            stream_enabled=True,
            stream_word_chunking=False,
        )
    )
    send_placeholder_key: bool = True
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="openai_chat", local=True
        )
    )

    def requires_catalog_model_selection(self, config: ProviderConfig) -> bool:
        del config
        return True

    def option_presentation_policy(
        self, config: ProviderConfig
    ) -> ProviderOptionPresentationPolicy:
        return replace(super().option_presentation_policy(config), show_rate_limit=True)

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="remote_first",
            settings_strategy="standard",
            status_capacity_strategy="openai_budget",
        )

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        return replace(
            super().status_policy(config),
            unreachable_hint="Start NIM or set a reachable Anthropic-compatible Base URL before launching Claude Code.",
        )


__all__ = ["SelfHostedNimProviderAdapter"]
