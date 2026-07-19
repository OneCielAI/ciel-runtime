"""vLLM provider adapter."""

from dataclasses import dataclass, field, replace

from ..architecture import ProviderCapabilities, ProviderConfig, ProviderContextPolicy, ProviderStatusPolicy
from .base import OpenAICompatibleProviderAdapter
from .constants import PROVIDER_DEFAULT_BASE_URLS


@dataclass(frozen=True)
class VllmProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "vllm"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["vllm"]
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
