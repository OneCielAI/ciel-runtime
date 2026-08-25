"""Z.AI provider adapter."""

from __future__ import annotations

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
    MessageProtocol,
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
            zcode_app_version="0.16.3",
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

    def build_headers(
        self, config: ProviderConfig, api_key: str | None
    ) -> Mapping[str, str]:
        headers = dict(super().build_headers(config, api_key))
        version = str(config.options.get("zcode_app_version") or "0.16.3").strip()
        headers["User-Agent"] = f"ZCode/{version}"
        return headers

    def build_model_headers(
        self, config: ProviderConfig, api_key: str | None
    ) -> Mapping[str, str]:
        return self.build_headers(config, api_key)

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
        documented_profiles: dict[str, tuple[int, str]] = {
            "glm-5.1": (200_000, "200K"),
            "glm-5.2": (1_000_000, "1M"),
            "glm-5.3": (1_000_000, "1M"),
        }
        documented = documented_profiles.get(model)
        if documented is None:
            return {}, None
        context_window, context_label = documented
        profile: dict[str, Any] = {
            "context_window": context_window,
            "max_model_len": context_window,
            "auto_compact_window": context_window,
            "max_output_tokens": 131_072,
            "context_reserve_tokens": 131_072,
            "model_profile": f"{model}-{context_label.lower()}",
        }
        if model == "glm-5.3":
            profile["effort_level"] = "max"
            notice = (
                "GLM-5.3 profile applied: 1M context, 128K maximum output, "
                "and max reasoning effort. Start a new session."
            )
        else:
            notice = (
                f"{model.upper()} profile applied: {context_label} context and "
                "128K maximum output. Start a new session."
            )
        return (
            profile,
            notice,
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


@dataclass(frozen=True)
class ZaiApiProviderAdapter(ZaiProviderAdapter):
    """General pay-as-you-go Z.AI API using the documented OpenAI surface."""

    name: str = "zai-api"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["zai-api"]
    include_x_api_key: bool = False
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="openai_chat",
            supports_thinking=True,
            requires_api_key=True,
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/chat/completions", models_path="/models"
        )
    )
    api_key_display_name_value: str = "Z.AI Model API"
    api_key_launch_error_value: str = (
        "Launch blocked: Z.AI Model API requires a pay-as-you-go API key."
    )

    def resolve_endpoint(self, operation: str, config: ProviderConfig) -> str:
        policy = self.request_policy(config)
        if operation in {"chat", "openai_chat"}:
            return policy.chat_path
        if operation == "models":
            return policy.models_path
        return super().resolve_endpoint(operation, config)

    def supported_protocols(
        self, config: ProviderConfig, model: str | None = None
    ) -> frozenset[MessageProtocol]:
        del config, model
        return frozenset({"openai_chat"})

    def select_protocol(
        self,
        operation: MessageProtocol,
        config: ProviderConfig,
        model: str | None = None,
    ) -> MessageProtocol:
        del operation, config, model
        return "openai_chat"

    def router_native_anthropic_enabled(
        self, config: ProviderConfig, model: str | None = None
    ) -> bool:
        del config, model
        return False


@dataclass(frozen=True)
class ZaiCodingPlanProviderAdapter(ZaiApiProviderAdapter):
    """Explicit Coding Plan profile with distinct OpenAI and Anthropic URLs."""

    name: str = "zai-coding-plan"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["zai-coding-plan"]
    include_x_api_key: bool = True
    configuration_defaults_value: dict = field(
        default_factory=lambda: {
            **ZaiProviderAdapter().configuration_defaults_value,
            "native_compat": True,
            "plan_type": "coding-plan",
            "anthropic_base_url": "https://api.z.ai/api/anthropic",
            "openai_responses_base_url": "https://api.z.ai/api/v1",
        }
    )
    api_key_display_name_value: str = "Z.AI Coding Plan"
    api_key_launch_error_value: str = (
        "Launch blocked: Z.AI Coding Plan requires a Coding Plan API key or OAuth login."
    )

    def supported_protocols(
        self, config: ProviderConfig, model: str | None = None
    ) -> frozenset[MessageProtocol]:
        del model
        protocols: set[MessageProtocol] = {"openai_chat", "openai_responses"}
        if bool(config.options.get("native_compat", True)):
            protocols.add("anthropic_messages")
        return frozenset(protocols)

    def select_protocol(
        self,
        operation: MessageProtocol,
        config: ProviderConfig,
        model: str | None = None,
    ) -> MessageProtocol:
        supported = self.supported_protocols(config, model)
        return operation if operation in supported else "openai_chat"

    def operation_base_url(self, operation: str, config: ProviderConfig) -> str:
        documented = {
            "anthropic_messages": str(
                config.options.get("anthropic_base_url")
                or "https://api.z.ai/api/anthropic"
            ),
            "openai_responses": str(
                config.options.get("openai_responses_base_url")
                or "https://api.z.ai/api/v1"
            ),
        }
        return documented.get(operation, config.base_url or self.default_base_url()).rstrip("/")

    def anthropic_base_url(self, config: ProviderConfig) -> str:
        del config
        return PROVIDER_DEFAULT_BASE_URLS["zai"]

    def router_native_anthropic_enabled(
        self, config: ProviderConfig, model: str | None = None
    ) -> bool:
        del model
        return bool(config.options.get("native_compat", True))


@dataclass(frozen=True)
class ZaiStartPlanProviderAdapter(ZaiCodingPlanProviderAdapter):
    """Retained configuration identity for an unverified Start Plan route.

    The public ZCode CLI source does not expose a Start Plan model transport,
    and Z.AI's public Coding Plan documentation does not publish one. Keep the
    profile readable so existing configuration is not destroyed, but never
    present its historical guessed gateway as launch-ready.
    """

    name: str = "zai-start-plan"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["zai-start-plan"]
    include_x_api_key: bool = True
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="anthropic_messages",
            supports_thinking=True,
            requires_api_key=True,
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/chat/completions", models_path="/models"
        )
    )
    configuration_defaults_value: dict = field(
        default_factory=lambda: {
            **ZaiProviderAdapter().configuration_defaults_value,
            "native_compat": True,
            "plan_type": "start-plan",
            "zcode_app_version": "0.16.3",
        }
    )
    api_key_display_name_value: str = "Z.AI Start Plan OAuth"
    api_key_launch_error_value: str = (
        "Launch blocked: Z.AI Start Plan requires its own OAuth login."
    )

    def anthropic_base_url(self, config: ProviderConfig) -> str:
        del config
        return "https://zcode.z.ai/api/v1/zcode-plan/anthropic"

    def resolve_endpoint(self, operation: str, config: ProviderConfig) -> str:
        policy = self.request_policy(config)
        paths = {
            "chat": policy.chat_path,
            "openai_chat": policy.chat_path,
            "models": policy.models_path,
            "anthropic_messages": "/anthropic/v1/messages",
        }
        return paths.get(operation) or super().resolve_endpoint(operation, config)

    def supported_protocols(
        self, config: ProviderConfig, model: str | None = None
    ) -> frozenset[MessageProtocol]:
        del config, model
        return frozenset({"anthropic_messages"})

    def select_protocol(
        self,
        operation: MessageProtocol,
        config: ProviderConfig,
        model: str | None = None,
    ) -> MessageProtocol:
        del operation, config, model
        return "anthropic_messages"

    def build_headers(
        self, config: ProviderConfig, api_key: str | None
    ) -> Mapping[str, str]:
        del config, api_key
        raise RuntimeError(
            "Z.AI Start Plan transport is blocked because no public model "
            "endpoint contract has been verified."
        )

    def launch_api_key_error(self, config: ProviderConfig) -> str | None:
        del config
        return (
            "Launch blocked: no Start Plan model endpoint is published by the "
            "public ZCode CLI source or Z.AI Coding Plan documentation. Existing "
            "Start Plan credentials were preserved but will not be sent to an "
            "unverified gateway."
        )


__all__ = [
    "ZaiApiProviderAdapter",
    "ZaiCodingPlanProviderAdapter",
    "ZaiProviderAdapter",
    "ZaiStartPlanProviderAdapter",
]
