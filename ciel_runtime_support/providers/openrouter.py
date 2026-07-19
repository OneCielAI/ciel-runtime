"""OpenRouter provider adapter."""

from dataclasses import dataclass, field

from ..architecture import ProviderCapabilities, ProviderConfig, ProviderContextPolicy
from .base import OpenAICompatibleProviderAdapter, provider_configuration
from .constants import DEFAULT_REQUEST_TIMEOUT_MS, PROVIDER_DEFAULT_BASE_URLS


@dataclass(frozen=True)
class OpenRouterProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "OpenRouter"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["openrouter"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            native_compat=False,
            rate_limit_rpm=0,
            rate_limit_status=False,
            context_window=262144,
            max_output_tokens=8192,
            temperature=0.7,
            top_p=0.8,
            context_reserve_tokens=1024,
            request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            stream_enabled=True,
            stream_word_chunking=False,
        )
    )
    authorization_header: str = "Authorization"
    require_api_key: bool = True
    api_key_display_name_value: str = "OpenRouter"
    api_key_launch_error_value: str = (
        "Launch blocked: OpenRouter requires an OpenRouter API key."
    )
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="openai_chat", requires_api_key=True
        )
    )

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="configured_first",
            settings_strategy="standard",
            hosted_timeout=True,
        )

    def router_native_anthropic_enabled(
        self, config: ProviderConfig, model: str | None = None
    ) -> bool:
        del config, model
        return False


__all__ = ["OpenRouterProviderAdapter"]
