"""Ollama local and cloud provider adapters."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping

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
from .base import HttpBearerProviderAdapter, provider_configuration
from .constants import DEFAULT_REQUEST_TIMEOUT_MS, PROVIDER_DEFAULT_BASE_URLS
from ..ollama_thinking import (
    OllamaThinkingPolicy,
    normalized_model_id,
    ollama_cloud_model_config_updates,
)


@dataclass(frozen=True)
class OllamaProviderAdapter(HttpBearerProviderAdapter):
    name: str = "ollama"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["ollama"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            "qwen3-coder",
            api_key="ollama",
            custom_models=("qwen3-coder",),
            native_compat=True,
            rate_limit_rpm=0,
            rate_limit_status=False,
            num_ctx="auto",
            num_ctx_min=32768,
            num_ctx_max=131072,
            keep_alive="5m",
            think=False,
            request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            stream_enabled=True,
            stream_word_chunking=False,
            ollama_options={},
        )
    )
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "Ollama"
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="ollama_chat",
            supports_thinking=True,
            local=True,
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/api/chat",
            models_path="/api/tags",
            model_info_path="/api/show",
            default_timeout_seconds=300.0,
            probe_strategy="ollama",
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="ollama")
    )

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="ollama",
            settings_strategy="ollama",
            uses_catalog_timeout=True,
            preset_context_profile="ollama",
            status_capacity_strategy="ollama_budget",
        )

    def launch_model_strategy(self, config: ProviderConfig) -> str:
        del config
        return "ollama_unslug"

    def ollama_think_value(
        self, config: ProviderConfig, model: str, request: Mapping[str, Any]
    ) -> bool | str | None:
        return OllamaThinkingPolicy().value(config.options, model, request)

    def reasoning_output_recovery(
        self,
        config: ProviderConfig,
        model: str,
        protocol: MessageProtocol,
        request: Mapping[str, Any],
    ) -> tuple[str, str | None]:
        """Choose only values accepted by the discovered Ollama architecture."""

        if protocol != "ollama_chat":
            return "none", None
        policy = OllamaThinkingPolicy()
        capabilities = {
            str(item).strip().lower()
            for item in config.options.get("ollama_model_capabilities") or []
        }
        if "thinking" not in capabilities and not policy.architecture(
            config.options, model
        ):
            return "none", None
        disabled_request = {**request, "thinking": {"type": "disabled"}}
        disabled_value = policy.value(config.options, model, disabled_request)
        if disabled_value is False:
            return "disable", None
        minimum_request = {
            **request,
            "thinking": {"type": "enabled", "effort": "low"},
        }
        minimum_value = policy.value(config.options, model, minimum_request)
        if isinstance(minimum_value, str) and minimum_value:
            return "minimum", minimum_value
        return "none", None

    def reasoning_passback_enabled(
        self, config: ProviderConfig, model: str | None = None
    ) -> bool:
        """Keep native Ollama thinking blocks so multi-turn tool history remains valid."""

        del config, model
        return True

    def option_presentation_policy(
        self, config: ProviderConfig
    ) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(
            show_rate_limit=True,
            show_tool_choice=True,
            show_stream=True,
            show_rate_limit_controls=True,
            show_sampling_controls=True,
            show_ip_family_control=True,
        )

    def model_paths(self, config: ProviderConfig) -> tuple[str, ...]:
        del config
        return ("/api/tags", "/v1/models")

    def configuration_policy(
        self, config: ProviderConfig
    ) -> ProviderConfigurationPolicy:
        del config
        return ProviderConfigurationPolicy(
            mutation_strategy="ollama", uses_ollama_status=True
        )

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        return replace(
            super().status_policy(config),
            unreachable_hint="Start Ollama or set a reachable Base URL before launching Claude Code.",
        )


@dataclass(frozen=True)
class OllamaCloudProviderAdapter(OllamaProviderAdapter):
    name: str = "ollama-cloud"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["ollama-cloud"]
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            "glm-5.1",
            custom_models=("glm-5.1", "deepseek-v4-flash:0731"),
            rate_limit_rpm=0,
            rate_limit_status=False,
            num_ctx="auto",
            num_ctx_min=32768,
            num_ctx_max=131072,
            keep_alive="5m",
            think=True,
            request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            stream_enabled=True,
            stream_word_chunking=False,
            ollama_options={},
        )
    )
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="ollama_chat",
            supports_thinking=True,
            requires_api_key=True,
        )
    )
    api_key_display_name_value: str = "Ollama Cloud"
    api_key_launch_error_value: str = (
        "Launch blocked: Ollama Cloud requires an API key."
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(
            kind="ollama", use_bundled_catalog_fallback=True
        )
    )

    def normalize_model_id(self, model_id: str) -> str:
        normalized = super().normalize_model_id(model_id)
        if normalized.endswith("-cloud") and ":" in normalized:
            return normalized[:-6]
        return normalized[:-6] if normalized.endswith(":cloud") else normalized

    @staticmethod
    def _is_deepseek_v4_flash_0731(model_id: str) -> bool:
        return normalized_model_id(model_id) == "deepseek-v4-flash:0731"

    def model_configuration_profile(
        self, config: ProviderConfig
    ) -> tuple[Mapping[str, Any], str | None]:
        if not self._is_deepseek_v4_flash_0731(config.model):
            return {}, None
        return (
            {
                "context_window": 1_000_000,
                "max_model_len": 1_000_000,
                "model_profile": "deepseek-v4-flash-0731-cloud-1m",
                "claude_code_supported_capabilities": [
                    "effort",
                    "max_effort",
                    "thinking",
                ],
            },
            "DeepSeek V4 Flash 0731 Cloud profile applied: 1M context and "
            "three-mode reasoning; Max thinking is the default for a new selection.",
        )

    def model_selection_config_updates(
        self, config: ProviderConfig, model_id: str
    ) -> Mapping[str, Any]:
        del config
        updates = ollama_cloud_model_config_updates(model_id)
        updates.update(
            {
                "haiku_model": model_id,
                "opus_model": model_id,
                "sonnet_model": model_id,
                "subagent_model": model_id,
            }
        )
        return updates

    def launch_model_strategy(self, config: ProviderConfig) -> str:
        del config
        return "alias"

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="ollama",
            settings_strategy="ollama",
            hosted_timeout=True,
            timeout_weight=1.2,
            uses_catalog_timeout=True,
            preset_context_profile="ollama",
            status_capacity_strategy="ollama_budget",
        )


__all__ = ["OllamaCloudProviderAdapter", "OllamaProviderAdapter"]
