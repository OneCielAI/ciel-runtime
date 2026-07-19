"""vLLM provider adapter."""

from dataclasses import dataclass, field, replace

from ..architecture import (
    ProviderCapabilities,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderStatusPolicy,
)
from .base import OpenAICompatibleProviderAdapter, provider_configuration
from .constants import DEFAULT_REQUEST_TIMEOUT_MS, PROVIDER_DEFAULT_BASE_URLS


@dataclass(frozen=True)
class VllmProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "vllm"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["vllm"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            "my-model",
            api_key="dummy",
            custom_models=("my-model",),
            native_compat=True,
            supports_tool_choice=False,
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
            upstream_protocol="openai_chat", supports_tool_choice=False, local=True
        )
    )

    def requires_catalog_model_selection(self, config: ProviderConfig) -> bool:
        del config
        return True

    def placeholder_model_ids(self) -> frozenset[str]:
        return super().placeholder_model_ids() | {"my-model"}

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
            unreachable_hint="vLLM must be reachable from this machine and expose Anthropic-compatible /v1/messages.",
        )


__all__ = ["VllmProviderAdapter"]
