"""TaBiAI (tabitoken.com) provider adapter."""

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..architecture import (
    MessageProtocol,
    ProviderCapabilities,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderModelCatalogPolicy,
    ProviderRequestPolicy,
)
from .base import OpenAICompatibleProviderAdapter, provider_configuration
from .constants import DEFAULT_REQUEST_TIMEOUT_MS, PROVIDER_DEFAULT_BASE_URLS


TABITOKEN_MODELS: tuple[str, ...] = (
    "claude-opus-4-8",
    "claude-opus-4-8-thinking",
    "claude-opus-5",
    "claude-opus-5-thinking",
)
TABITOKEN_THINKING_MODELS = frozenset(
    model for model in TABITOKEN_MODELS if model.endswith("-thinking")
)


@dataclass(frozen=True)
class TabitokenProviderAdapter(OpenAICompatibleProviderAdapter):
    """Dual OpenAI Chat and Anthropic Messages transport for TaBiAI."""

    name: str = "tabitoken"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["tabitoken"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            TABITOKEN_MODELS[0],
            custom_models=TABITOKEN_MODELS,
            native_compat=True,
            context_window=1_000_000,
            max_output_tokens=8192,
            context_reserve_tokens=8192,
            request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            stream_enabled=True,
            stream_word_chunking=False,
        )
    )
    authorization_header: str = "Authorization"
    include_x_api_key: bool = False
    require_api_key: bool = True
    api_key_display_name_value: str = "TaBiAI (Tabitoken.com)"
    api_key_launch_error_value: str = (
        "Launch blocked: TaBiAI (Tabitoken.com) requires an API key."
    )
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="openai_chat",
            requires_api_key=True,
            supports_thinking=True,
            preserves_anthropic_thinking=True,
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/v1/chat/completions",
            models_path="/v1/models",
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(
            kind="openai",
            fallback_models=TABITOKEN_MODELS,
            allow_configured_fallback=True,
        )
    )

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="configured_first",
            settings_strategy="standard",
            hosted_timeout=True,
        )

    def anthropic_base_url(self, config: ProviderConfig) -> str:
        return str(config.base_url or self.default_base_url()).rstrip("/")

    def supported_protocols(
        self,
        config: ProviderConfig,
        model: str | None = None,
    ) -> frozenset[MessageProtocol]:
        del config, model
        return frozenset({"anthropic_messages", "openai_chat"})

    def select_protocol(
        self,
        operation: MessageProtocol,
        config: ProviderConfig,
        model: str | None = None,
    ) -> MessageProtocol:
        del config, model
        if operation == "anthropic_messages":
            return "anthropic_messages"
        return "openai_chat"

    def openai_reasoning_effort(
        self,
        config: ProviderConfig,
        model: str,
        request: Mapping[str, Any],
    ) -> str | None:
        del config
        if self.normalize_model_id(model) not in TABITOKEN_THINKING_MODELS:
            return None
        metadata = request.get("metadata")
        hinted = (
            metadata.get("ciel_runtime_reasoning_effort")
            if isinstance(metadata, Mapping)
            else None
        )
        value = str(request.get("reasoning_effort") or hinted or "").strip().casefold()
        return value or None


__all__ = [
    "TABITOKEN_MODELS",
    "TABITOKEN_THINKING_MODELS",
    "TabitokenProviderAdapter",
]
