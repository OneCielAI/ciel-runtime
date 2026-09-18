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


# stealth/ox-alpha ended its testing period on 2026-09-18 (POST replies 404
# "This model was ZAI's GLM-5.3 Flash. Use it now:
# https://openrouter.ai/z-ai/glm-5.3-flash"); the same model continues under
# its own name. Context and output caps from GET /api/v1/models.
OPENROUTER_GLM_FLASH_MODEL = "z-ai/glm-5.3-flash"
OPENROUTER_GLM_FLASH_CONTEXT_WINDOW = 1_310_720
OPENROUTER_GLM_FLASH_MAX_OUTPUT_TOKENS = 131_072
# stealth/union-alpha ended its testing period on 2026-09-17 (POST replies
# 404 "This model was Unbiased's Pareto. Use it now:
# https://openrouter.ai/unbiased/pareto"); the same model continues as
# unbiased/pareto (GET /api/v1/models 2026-09-17: 262,144-token context).
# Only Chat Completions is documented, so it stays on openai_chat.
OPENROUTER_PARETO_MODEL = "unbiased/pareto"
OPENROUTER_PARETO_CONTEXT_WINDOW = 262_144
OPENROUTER_PARETO_MAX_OUTPUT_TOKENS = 131_072


@dataclass(frozen=True)
class OpenRouterProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "OpenRouter"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["openrouter"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            custom_models=(OPENROUTER_GLM_FLASH_MODEL, OPENROUTER_PARETO_MODEL),
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
        if selected == OPENROUTER_GLM_FLASH_MODEL or native is True or str(
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
        selected = self.normalize_model_id(config.model)
        if selected == OPENROUTER_PARETO_MODEL:
            return (
                {
                    "context_window": OPENROUTER_PARETO_CONTEXT_WINDOW,
                    "max_model_len": OPENROUTER_PARETO_CONTEXT_WINDOW,
                    "max_output_tokens": OPENROUTER_PARETO_MAX_OUTPUT_TOKENS,
                    "model_profile": "openrouter-pareto-262k",
                    "supports_tool_choice": True,
                    "supports_vision": True,
                },
                "OpenRouter Pareto profile applied: 262,144-token context and 131,072-token maximum output.",
            )
        if selected != OPENROUTER_GLM_FLASH_MODEL:
            return {}, None
        return (
            {
                "context_window": OPENROUTER_GLM_FLASH_CONTEXT_WINDOW,
                "max_model_len": OPENROUTER_GLM_FLASH_CONTEXT_WINDOW,
                "max_output_tokens": OPENROUTER_GLM_FLASH_MAX_OUTPUT_TOKENS,
                "model_profile": "openrouter-glm-5.3-flash-1.3m",
                "supports_tool_choice": True,
                "supports_vision": True,
            },
            "OpenRouter GLM 5.3 Flash profile applied: 1,310,720-token context and 131,072-token maximum output.",
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
    "OPENROUTER_GLM_FLASH_CONTEXT_WINDOW",
    "OPENROUTER_GLM_FLASH_MAX_OUTPUT_TOKENS",
    "OPENROUTER_GLM_FLASH_MODEL",
    "OPENROUTER_PARETO_CONTEXT_WINDOW",
    "OPENROUTER_PARETO_MAX_OUTPUT_TOKENS",
    "OPENROUTER_PARETO_MODEL",
    "OpenRouterProviderAdapter",
]
