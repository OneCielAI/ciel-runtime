"""OpenRouter provider adapter."""

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..architecture import (
    MessageProtocol,
    ProviderCapabilities,
    ProviderConfig,
    ProviderContextPolicy,
)
from .base import OpenAICompatibleProviderAdapter, provider_configuration
from .constants import DEFAULT_REQUEST_TIMEOUT_MS, PROVIDER_DEFAULT_BASE_URLS


OPENROUTER_OX_ALPHA_MODEL = "stealth/ox-alpha"
OPENROUTER_OX_ALPHA_CONTEXT_WINDOW = 1_048_576
OPENROUTER_OX_ALPHA_MAX_OUTPUT_TOKENS = 131_072


@dataclass(frozen=True)
class OpenRouterProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "OpenRouter"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["openrouter"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            custom_models=(OPENROUTER_OX_ALPHA_MODEL,),
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
        return self.select_protocol("anthropic_messages", config, model) == "anthropic_messages"

    def supported_protocols(
        self, config: ProviderConfig, model: str | None = None
    ) -> frozenset[MessageProtocol]:
        protocols: set[MessageProtocol] = {"openai_chat"}
        selected = self.normalize_model_id(str(model or config.model or ""))
        native = config.options.get("native_compat")
        if selected == OPENROUTER_OX_ALPHA_MODEL or native is True or str(
            native
        ).strip().lower() in {"1", "true", "yes", "on"}:
            protocols.add("anthropic_messages")
        return frozenset(protocols)

    def select_protocol(
        self,
        operation: MessageProtocol,
        config: ProviderConfig,
        model: str | None = None,
    ) -> MessageProtocol:
        if operation == "anthropic_messages" and "anthropic_messages" in self.supported_protocols(
            config, model
        ):
            return "anthropic_messages"
        return "openai_chat"

    def model_configuration_profile(
        self, config: ProviderConfig
    ) -> tuple[Mapping[str, Any], str | None]:
        if self.normalize_model_id(config.model) != OPENROUTER_OX_ALPHA_MODEL:
            return {}, None
        return (
            {
                "context_window": OPENROUTER_OX_ALPHA_CONTEXT_WINDOW,
                "max_model_len": OPENROUTER_OX_ALPHA_CONTEXT_WINDOW,
                "max_output_tokens": OPENROUTER_OX_ALPHA_MAX_OUTPUT_TOKENS,
                "model_profile": "openrouter-ox-alpha-1m",
                "supports_tool_choice": True,
                "supports_vision": True,
            },
            "OpenRouter Ox Alpha profile applied: 1,048,576-token context and 131,072-token maximum output.",
        )

    def openai_reasoning_effort(
        self,
        config: ProviderConfig,
        model: str,
        request: Mapping[str, Any],
    ) -> str | None:
        del config, model
        metadata = request.get("metadata")
        hinted = (
            metadata.get("ciel_runtime_reasoning_effort")
            if isinstance(metadata, Mapping)
            else None
        )
        value = str(request.get("reasoning_effort") or hinted or "").strip().casefold()
        return value or None


__all__ = [
    "OPENROUTER_OX_ALPHA_CONTEXT_WINDOW",
    "OPENROUTER_OX_ALPHA_MAX_OUTPUT_TOKENS",
    "OPENROUTER_OX_ALPHA_MODEL",
    "OpenRouterProviderAdapter",
]
