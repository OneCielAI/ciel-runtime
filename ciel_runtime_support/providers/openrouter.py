"""OpenRouter provider adapter."""

from dataclasses import dataclass, field

from ..architecture import ProviderCapabilities, ProviderConfig, ProviderContextPolicy
from .base import OpenAICompatibleProviderAdapter
from .constants import PROVIDER_DEFAULT_BASE_URLS


@dataclass(frozen=True)
class OpenRouterProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "OpenRouter"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["openrouter"]
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
