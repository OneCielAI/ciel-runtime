"""DeepSeek provider adapter."""

from dataclasses import dataclass, field

from ..architecture import (
    ProviderCapabilities,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderModelCatalogPolicy,
    ProviderOptionPresentationPolicy,
    ProviderRequestPolicy,
    ProviderStatusPolicy,
)
from .base import HttpBearerProviderAdapter
from .constants import PROVIDER_DEFAULT_BASE_URLS


@dataclass(frozen=True)
class DeepSeekProviderAdapter(HttpBearerProviderAdapter):
    name: str = "deepseek"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["deepseek"]
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "DeepSeek"
    api_key_launch_error_value: str = (
        "Launch blocked: DeepSeek.com requires a DeepSeek API key."
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
            kind="configured",
            fallback_models=("deepseek-v4-pro[1m]", "deepseek-v4-flash"),
        )
    )

    def advisor_model_badge(self, config: ProviderConfig, model: str) -> str:
        del config
        return "recommended for long context" if model == "deepseek-v4-pro" else ""

    def normalize_model_id(self, model_id: str) -> str:
        return str(model_id or "").strip()

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
            kind="configured",
            configured_description="DeepSeek Anthropic API configured",
        )

    def supports_tool_choice(
        self, config: ProviderConfig, model: str | None = None
    ) -> bool:
        configured = config.options.get("supports_tool_choice")
        if configured is not None:
            return bool(configured)
        return (
            "deepseek-v4"
            not in str(model or config.model or "").split("[", 1)[0].lower()
        )


__all__ = ["DeepSeekProviderAdapter"]
