"""OpenCode Zen provider adapter."""

from dataclasses import dataclass, field
from typing import Mapping

from ..architecture import (
    MessageProtocol,
    ProviderCapabilities,
    ProviderConfigurationPolicy,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderModelCatalogPolicy,
    ProviderOptionPresentationPolicy,
    ProviderRequestPolicy,
    ProviderStatusPolicy,
)
from .base import (
    HttpBearerProviderAdapter,
    configuration_policy,
    provider_configuration,
)
from .constants import DEFAULT_REQUEST_TIMEOUT_MS, PROVIDER_DEFAULT_BASE_URLS


@dataclass(frozen=True)
class OpenCodeProviderAdapter(HttpBearerProviderAdapter):
    name: str = "opencode"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["opencode"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            "claude-sonnet-4-6",
            custom_models=("claude-sonnet-4-6", "qwen3.6-plus-free"),
            native_compat=True,
            context_window=200000,
            max_output_tokens=8192,
            context_reserve_tokens=8192,
            request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            stream_enabled=True,
            stream_word_chunking=False,
            ip_family="ipv6-preferred",
            haiku_model="claude-haiku-4-5",
            subagent_model="claude-sonnet-4-6",
            model_endpoints={},
        )
    )
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "OpenCode Zen"
    api_key_launch_error_value: str = (
        "Launch blocked: OpenCode Zen requires a OpenCode Zen API key."
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
            chat_path="/messages", models_path="/v1/models"
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(
            kind="openai",
            allow_configured_fallback=True,
            allow_public_without_auth=True,
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
        return bool(config.options.get("native_compat", True)) and (
            self.select_protocol("anthropic_messages", config, model)
            == "anthropic_messages"
        )

    def option_presentation_policy(
        self, config: ProviderConfig
    ) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(
            show_native=True,
            show_tool_choice=True,
            show_stream=True,
            show_ip_family=True,
            show_rate_limit_controls=True,
            show_sampling_controls=True,
            show_ip_family_control=True,
        )

    def select_protocol(
        self,
        operation: MessageProtocol,
        config: ProviderConfig,
        model: str | None = None,
    ) -> MessageProtocol:
        del operation
        raw_model = str(model or config.model or "").strip()
        overrides = config.options.get("model_endpoints")
        if isinstance(overrides, Mapping):
            raw = overrides.get(raw_model)
            key = str(raw or "").strip().lower().replace("_", "-")
            mapped = {
                "anthropic": "anthropic_messages",
                "anthropic-messages": "anthropic_messages",
                "messages": "anthropic_messages",
                "openai": "openai_chat",
                "openai-chat": "openai_chat",
                "chat": "openai_chat",
                "openai-responses": "openai_responses",
                "responses": "openai_responses",
                "google-generative": "google_generative",
                "gemini": "google_generative",
            }.get(key)
            if mapped is not None:
                return mapped
        normalized = raw_model.split("[", 1)[0].lower()
        for prefix in ("ciel-runtime-opencode-go-", "ciel-runtime-opencode-"):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :]
                break
        if self.name == "opencode-go":
            if normalized.startswith(("glm-", "kimi-", "deepseek-", "mimo-", "hy3-")):
                return "openai_chat"
            return "anthropic_messages"
        if normalized.startswith("gpt-"):
            return "openai_responses"
        if normalized.startswith("gemini-"):
            return "google_generative"
        if normalized.startswith(
            (
                "minimax-",
                "glm-",
                "kimi-",
                "grok-",
                "big-pickle",
                "deepseek-",
                "mimo-",
                "nemotron-",
                "north-",
            )
        ):
            return "openai_chat"
        return "anthropic_messages"

    def supported_protocols(
        self, config: ProviderConfig, model: str | None = None
    ) -> frozenset[MessageProtocol]:
        return frozenset({self.select_protocol("anthropic_messages", config, model)})

    def openai_reasoning_passback_enabled(
        self, config: ProviderConfig, model: str | None = None
    ) -> bool:
        requested = self.normalize_model_id(str(model or ""))
        prefix = f"ciel-runtime-{self.name}-"
        if requested.startswith(prefix):
            requested = requested[len(prefix) :]
        elif requested.startswith("ciel-runtime-"):
            requested = config.model
        model_id = self.normalize_model_id(requested or config.model).lower()
        return model_id.startswith("deepseek-") and self.select_protocol(
            "openai_chat", config, model_id
        ) == "openai_chat"

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(
            kind="catalog",
            label=self.api_key_display_name_value,
            catalog_path="/v1/models",
        )

    def model_panel_badge(self, config: ProviderConfig, model: str) -> str:
        protocol = self.select_protocol("anthropic_messages", config, model)
        label = {
            "anthropic_messages": "messages",
            "openai_chat": "chat",
            "openai_responses": "responses unsupported",
            "google_generative": "gemini unsupported",
        }.get(protocol, str(protocol))
        overrides = config.options.get("model_endpoints")
        if isinstance(overrides, Mapping) and model in overrides:
            label += " override"
        return label

    def project_router_model_metadata(
        self, config: ProviderConfig, model_id: str
    ) -> Mapping[str, object]:
        protocol = self.select_protocol("anthropic_messages", config, model_id)
        endpoint = {
            "anthropic_messages": "anthropic-messages",
            "openai_chat": "openai-chat",
            "openai_responses": "openai-responses",
            "google_generative": "google-generative",
        }.get(protocol, str(protocol).replace("_", "-"))
        return {
            "opencode_endpoint": endpoint,
            "router_supported": protocol in {"anthropic_messages", "openai_chat"},
        }

    def configuration_policy(
        self, config: ProviderConfig
    ) -> ProviderConfigurationPolicy:
        del config
        return configuration_policy(supports_model_endpoint_overrides=True)


__all__ = ["OpenCodeProviderAdapter"]
