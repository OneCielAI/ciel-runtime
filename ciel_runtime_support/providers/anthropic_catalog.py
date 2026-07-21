"""Declarative adapters for Anthropic Messages-compatible API providers."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Callable

from ..architecture import (
    ProviderAdapter,
    ProviderCapabilities,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderModelCatalogPolicy,
    ProviderRequestPolicy,
)
from ..runtime_constants import DEFAULT_REQUEST_TIMEOUT_MS
from .base import HttpBearerProviderAdapter, provider_configuration


@dataclass(frozen=True, slots=True)
class AnthropicCompatibleProviderSpec:
    name: str
    label: str
    base_url: str
    models: tuple[str, ...]
    aliases: tuple[str, ...] = ()


class CatalogAnthropicProviderAdapter(HttpBearerProviderAdapter):
    """Anthropic-compatible transport assembled from immutable provider data."""

    def __init__(
        self,
        spec: AnthropicCompatibleProviderSpec,
        *,
        base_url: str = "",
    ) -> None:
        self.spec = spec
        super().__init__(
            name=spec.name,
            base_url=base_url or spec.base_url,
            configuration_defaults_value=provider_configuration(
                spec.models[0],
                custom_models=spec.models,
                native_compat=True,
                preserve_anthropic_thinking=True,
                context_window=200000,
                max_output_tokens=8192,
                context_reserve_tokens=8192,
                request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
                stream_enabled=True,
                stream_word_chunking=False,
            ),
            require_api_key=True,
            api_key_display_name_value=spec.label,
            api_key_launch_error_value=(
                f"Launch blocked: {spec.label} requires an API key."
            ),
            capabilities_value=ProviderCapabilities(
                upstream_protocol="anthropic_messages",
                requires_api_key=True,
                supports_thinking=True,
                preserves_anthropic_thinking=True,
            ),
            request_policy_value=ProviderRequestPolicy(
                chat_path="/v1/messages",
                models_path="/v1/models",
            ),
            model_catalog_policy_value=ProviderModelCatalogPolicy(
                kind="openai",
                fallback_models=spec.models,
                allow_configured_fallback=True,
            ),
        )

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="configured_first",
            settings_strategy="standard",
            hosted_timeout=True,
        )


def anthropic_catalog_provider_factory(
    spec: AnthropicCompatibleProviderSpec,
) -> Callable[..., ProviderAdapter]:
    return partial(CatalogAnthropicProviderAdapter, spec)


ANTHROPIC_COMPATIBLE_PROVIDER_SPECS = (
    AnthropicCompatibleProviderSpec(
        "minimax",
        "MiniMax",
        "https://api.minimax.io/anthropic",
        ("MiniMax-M2.5", "MiniMax-M2.1", "MiniMax-M2"),
    ),
    AnthropicCompatibleProviderSpec(
        "minimax-cn",
        "MiniMax China",
        "https://api.minimaxi.com/anthropic",
        ("MiniMax-M2.5", "MiniMax-M2.1", "MiniMax-M2"),
        ("minimaxi",),
    ),
)

ANTHROPIC_CATALOG_PROVIDER_BASE_URLS = {
    spec.name: spec.base_url for spec in ANTHROPIC_COMPATIBLE_PROVIDER_SPECS
}


__all__ = [
    "ANTHROPIC_CATALOG_PROVIDER_BASE_URLS",
    "ANTHROPIC_COMPATIBLE_PROVIDER_SPECS",
    "AnthropicCompatibleProviderSpec",
    "CatalogAnthropicProviderAdapter",
    "anthropic_catalog_provider_factory",
]
