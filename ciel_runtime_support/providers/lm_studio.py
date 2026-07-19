"""LM Studio provider adapter."""

from dataclasses import dataclass, field, replace

from ..architecture import (
    ProviderCapabilities,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderModelCatalogPolicy,
    ProviderOptionPresentationPolicy,
    ProviderStatusPolicy,
)
from .base import OpenAICompatibleProviderAdapter
from .constants import PROVIDER_DEFAULT_BASE_URLS


@dataclass(frozen=True)
class LMStudioProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "lm-studio"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["lm-studio"]
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="openai_chat", local=True
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="lm_studio")
    )

    def requires_catalog_model_selection(self, config: ProviderConfig) -> bool:
        del config
        return True

    def placeholder_model_ids(self) -> frozenset[str]:
        return super().placeholder_model_ids() | {"local-model"}

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        return replace(
            super().context_policy(config), status_capacity_strategy="openai_budget"
        )

    def option_presentation_policy(
        self, config: ProviderConfig
    ) -> ProviderOptionPresentationPolicy:
        return replace(super().option_presentation_policy(config), show_rate_limit=True)

    def model_paths(self, config: ProviderConfig) -> tuple[str, ...]:
        del config
        return ("/api/v0/models", "/api/v1/models", "/v1/models", "/models")

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        return replace(
            super().status_policy(config),
            unreachable_hint="Start LM Studio's Local Server or set a reachable Anthropic-compatible Base URL before launching Claude Code.",
            readiness_validation="lm_studio",
        )


__all__ = ["LMStudioProviderAdapter"]
