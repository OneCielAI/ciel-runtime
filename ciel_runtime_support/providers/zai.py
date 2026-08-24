"""Z.AI provider adapter."""

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..architecture import (
    ProviderCapabilities,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderModelCatalogPolicy,
    ProviderOptionPresentationPolicy,
    ProviderRequestPolicy,
    ProviderStatusPolicy,
)
from .base import HttpBearerProviderAdapter, provider_configuration
from .constants import PROVIDER_DEFAULT_BASE_URLS, ZAI_MODEL_FALLBACK_IDS


@dataclass(frozen=True)
class ZaiProviderAdapter(HttpBearerProviderAdapter):
    name: str = "zai"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["zai"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            "glm-5.3[1m]",
            custom_models=ZAI_MODEL_FALLBACK_IDS,
            native_compat=True,
            preserve_anthropic_thinking=True,
            claude_code_supported_capabilities=["effort", "thinking"],
            context_window=1000000,
            auto_compact_window=1000000,
            max_output_tokens=131072,
            context_reserve_tokens=131072,
            request_timeout_ms=3000000,
            stream_enabled=True,
            stream_word_chunking=False,
            effort_level="max",
            opus_model="glm-5.3[1m]",
            sonnet_model="glm-5.3[1m]",
            haiku_model="glm-4.7",
            subagent_model="glm-5.3[1m]",
            managed_mcp=True,
        )
    )
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "Z.AI GLM"
    api_key_launch_error_value: str = (
        "Launch blocked: Z.AI GLM requires a Z.AI API key."
    )
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="anthropic_messages",
            supports_thinking=True,
            requires_api_key=True,
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/v1/messages", models_path="/v1/models"
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(
            kind="openai", fallback_models=ZAI_MODEL_FALLBACK_IDS
        )
    )

    def normalize_model_id(self, model_id: str) -> str:
        return str(model_id or "").strip()

    def upstream_api_model_id(self, model_id: str) -> str:
        return super().normalize_model_id(model_id)

    def model_selection_config_updates(
        self, config: ProviderConfig, model_id: str
    ) -> dict[str, str]:
        del config
        return {
            "haiku_model": model_id,
            "opus_model": model_id,
            "sonnet_model": model_id,
        }

    def model_configuration_profile(
        self, config: ProviderConfig
    ) -> tuple[Mapping[str, Any], str | None]:
        model = self.normalize_model_id(config.model).split("[", 1)[0].lower()
        if model != "glm-5.3":
            return {}, None
        return (
            {
                "context_window": 1_000_000,
                "max_model_len": 1_000_000,
                "auto_compact_window": 1_000_000,
                "max_output_tokens": 131_072,
                "context_reserve_tokens": 131_072,
                "effort_level": "max",
                "model_profile": "glm-5.3-1m",
            },
            "GLM-5.3 profile applied: 1M context, 128K maximum output, and max reasoning effort. Start a new session.",
        )

    def normalize_request_options(
        self, config: ProviderConfig, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        model = self.normalize_model_id(str(request.get("model") or config.model))
        model = model.split("[", 1)[0].lower()
        if model != "glm-5.3":
            return request
        normalized = dict(request)
        thinking = request.get("thinking")
        normalized["thinking"] = {
            **(dict(thinking) if isinstance(thinking, Mapping) else {}),
            "type": "enabled",
        }
        effort = str(
            request.get("reasoning_effort")
            or config.options.get("effort_level")
            or "max"
        ).strip().lower()
        normalized["reasoning_effort"] = {
            "none": "low",
            "minimal": "low",
            "medium": "high",
            "xhigh": "max",
            "ultra": "max",
        }.get(effort, effort if effort in {"low", "high", "max"} else "max")
        if "temperature" in normalized:
            normalized["temperature"] = 1.0
        return normalized

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="hint_first",
            settings_strategy="standard",
            hosted_timeout=True,
            context_family_before_size_markers=True,
            status_capacity_strategy="provider",
        )

    def router_native_anthropic_enabled(
        self, config: ProviderConfig, model: str | None = None
    ) -> bool:
        del model
        return bool(config.options.get("native_compat", True))

    def option_presentation_policy(
        self, config: ProviderConfig
    ) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(
            show_native=True,
            show_tool_choice=True,
            show_stream=True,
            show_rate_limit_controls=True,
            show_sampling_controls=True,
            show_ip_family_control=True,
        )

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(
            kind="configured", configured_description="Z.AI Anthropic API configured"
        )


__all__ = ["ZaiProviderAdapter"]
