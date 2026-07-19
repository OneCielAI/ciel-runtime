"""Production provider adapters for common HTTP authentication dialects."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
from typing import Any, Mapping
from urllib.parse import quote

from .architecture import (
    MessageProtocol,
    ProviderAdapter,
    ProviderCapabilities,
    ProviderConfigurationPolicy,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderModelCatalogPolicy,
    ProviderOptionPresentationPolicy,
    ProviderRequestPolicy,
    ProviderStatusPolicy,
)
from .registry import AdapterRegistry
from .providers.base import (
    HttpBearerProviderAdapter,
    NoAuthProviderAdapter,
    OpenAICompatibleProviderAdapter,
    configuration_policy as _configuration_policy,
)
from .providers.anthropic import AnthropicProviderAdapter
from .providers.constants import PROVIDER_DEFAULT_BASE_URLS, ZAI_MODEL_FALLBACK_IDS
from .providers.native import AgyProviderAdapter, CodexProviderAdapter
from .providers.ollama import OllamaCloudProviderAdapter, OllamaProviderAdapter
from .providers.openrouter import OpenRouterProviderAdapter
from .providers.lm_studio import LMStudioProviderAdapter
from .providers.nim import SelfHostedNimProviderAdapter
from .providers.nvidia import NvidiaHostedProviderAdapter
from .providers.vllm import VllmProviderAdapter


@dataclass(frozen=True)
class DeepSeekProviderAdapter(HttpBearerProviderAdapter):
    name: str = "deepseek"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["deepseek"]
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "DeepSeek"
    api_key_launch_error_value: str = "Launch blocked: DeepSeek.com requires a DeepSeek API key."
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="anthropic_messages", supports_thinking=True, requires_api_key=True
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(chat_path="/v1/messages", models_path="/v1/models")
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
            capacity_strategy="configured_first", settings_strategy="standard", hosted_timeout=True
        )

    def router_native_anthropic_enabled(self, config: ProviderConfig, model: str | None = None) -> bool:
        del model
        return bool(config.options.get("native_compat", True))

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
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
        return ProviderStatusPolicy(kind="configured", configured_description="DeepSeek Anthropic API configured")

    def supports_tool_choice(self, config: ProviderConfig, model: str | None = None) -> bool:
        configured = config.options.get("supports_tool_choice")
        if configured is not None:
            return bool(configured)
        return "deepseek-v4" not in str(model or config.model or "").split("[", 1)[0].lower()


@dataclass(frozen=True)
class KimiProviderAdapter(HttpBearerProviderAdapter):
    name: str = "kimi"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["kimi"]
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "Kimi.com"
    api_key_launch_error_value: str = "Launch blocked: Kimi.com requires a Kimi API key."
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="anthropic_messages", supports_thinking=True, requires_api_key=True
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(chat_path="/v1/messages", models_path="/v1/models")
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="openai", allow_configured_fallback=True)
    )

    def normalize_model_id(self, model_id: str) -> str:
        normalized = super().normalize_model_id(model_id)
        lowered = normalized.lower().replace("_", "-").strip()
        if lowered in ("k3", "kimi-k3", "kimi/k3", "kimi-code/k3"):
            return "k3"
        if lowered in (
            "kimi-code/kimi-for-coding",
            "kimi/kimi-for-coding",
            "moonshot/kimi-for-coding",
            "kimi-k2.7-code",
            "kimi-k2.7-coding",
            "k2.7-code",
            "k2.7-coding",
        ):
            return "kimi-for-coding"
        return normalized

    def model_configuration_profile(
        self, config: ProviderConfig
    ) -> tuple[Mapping[str, Any], str | None]:
        if self.normalize_model_id(config.model) != "k3":
            return {}, None
        return (
            {"context_window": 1048576, "max_model_len": 1048576, "effort_level": "max"},
            "Kimi K3 profile applied: 1M context and max reasoning effort.",
        )

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="hint_first",
            settings_strategy="standard",
            hosted_timeout=True,
        )

    def router_native_anthropic_enabled(self, config: ProviderConfig, model: str | None = None) -> bool:
        del model
        return bool(config.options.get("native_compat", True))

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
        del config
        return ProviderOptionPresentationPolicy(
            show_native=True,
            show_tool_choice=True,
            show_stream=True,
            show_rate_limit_controls=True,
            show_sampling_controls=True,
            show_ip_family_control=True,
        )

    def supported_protocols(self, config: ProviderConfig, model: str | None = None) -> frozenset[MessageProtocol]:
        del config, model
        return frozenset({"anthropic_messages", "openai_chat"})

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(kind="catalog", label="Kimi.com", catalog_path="/v1/models")

    def select_protocol(self, operation: MessageProtocol, config: ProviderConfig, model: str | None = None) -> MessageProtocol:
        del config, model
        return "openai_chat" if operation in {"openai_chat", "openai_responses"} else "anthropic_messages"

    def normalize_request_options(self, config: ProviderConfig, request: Mapping[str, Any]) -> Mapping[str, Any]:
        del config
        model = str(request.get("model") or "").split("[", 1)[0].strip().lower().replace("_", "-")
        if model.startswith("ciel-runtime-kimi-"):
            model = model[len("ciel-runtime-kimi-"):]
        thinking = request.get("thinking")
        if model not in {"k3", "kimi-k3", "kimi/k3", "kimi-code/k3"} or not isinstance(thinking, Mapping):
            return request
        if str(thinking.get("type") or "").lower() == "disabled":
            return request
        normalized = dict(request)
        normalized["thinking"] = {**thinking, "effort": "max"}
        return normalized

    def normalize_tool_choice(self, config: ProviderConfig, model: str, tool_choice: Any) -> Any:
        del model
        if config.options.get("supports_tool_choice") is False or not isinstance(tool_choice, Mapping):
            return tool_choice
        if str(tool_choice.get("type") or "").strip().lower() in {"any", "tool"}:
            return {"type": "auto"}
        return tool_choice


@dataclass(frozen=True)
class ZaiProviderAdapter(HttpBearerProviderAdapter):
    name: str = "zai"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["zai"]
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "Z.AI GLM"
    api_key_launch_error_value: str = "Launch blocked: Z.AI GLM requires a Z.AI API key."
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="anthropic_messages", supports_thinking=True, requires_api_key=True
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(chat_path="/v1/messages", models_path="/v1/models")
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="openai", fallback_models=ZAI_MODEL_FALLBACK_IDS)
    )

    def normalize_model_id(self, model_id: str) -> str:
        return str(model_id or "").strip()

    def upstream_api_model_id(self, model_id: str) -> str:
        return super().normalize_model_id(model_id)

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="hint_first",
            settings_strategy="standard",
            hosted_timeout=True,
            context_family_before_size_markers=True,
            status_capacity_strategy="provider",
        )

    def router_native_anthropic_enabled(self, config: ProviderConfig, model: str | None = None) -> bool:
        del model
        return bool(config.options.get("native_compat", True))

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
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
        return ProviderStatusPolicy(kind="configured", configured_description="Z.AI Anthropic API configured")


@dataclass(frozen=True)
class FireworksProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "fireworks"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["fireworks"]
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "Fireworks.ai"
    api_key_launch_error_value: str = "Launch blocked: Fireworks.ai requires a Fireworks API key."
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(upstream_protocol="openai_chat", requires_api_key=True)
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="fireworks")
    )

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="configured_first", settings_strategy="standard", hosted_timeout=True
        )

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
        return replace(super().option_presentation_policy(config), show_sampling=False)

    def supported_protocols(self, config: ProviderConfig, model: str | None = None) -> frozenset[MessageProtocol]:
        del config, model
        return frozenset({"anthropic_messages", "openai_chat"})

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        account_id = str(config.options.get("account_id") or "").strip()
        if not account_id:
            match = re.match(r"^accounts/([^/]+)/models/[^/]+$", config.model)
            account_id = match.group(1) if match else "fireworks"
        return ProviderStatusPolicy(
            kind="catalog",
            label="Fireworks.ai",
            catalog_path=f"/v1/accounts/{quote(account_id, safe='')}/models?pageSize=1",
            catalog_scope="fireworks_management",
            catalog_count_label="sampled",
        )

    def select_protocol(self, operation: MessageProtocol, config: ProviderConfig, model: str | None = None) -> MessageProtocol:
        del config, model
        return "openai_chat" if operation in {"openai_chat", "openai_responses"} else "anthropic_messages"

    def configuration_policy(self, config: ProviderConfig) -> ProviderConfigurationPolicy:
        del config
        return _configuration_policy(
            text_option_aliases={
                "account": "account_id",
                "account_id": "account_id",
                "management_base_url": "model_api_base_url",
                "model_api_base_url": "model_api_base_url",
                "model_base_url": "model_api_base_url",
                "models_base_url": "model_api_base_url",
            },
            strip_trailing_slash_fields=frozenset({"model_api_base_url"}),
        )

    def project_model_metadata(self, raw: Mapping[str, Any]) -> Mapping[str, Any]:
        metadata = dict(super().project_model_metadata(raw))
        for source_key, target_key in (
            ("displayName", "display_name"),
            ("description", "description"),
            ("kind", "kind"),
            ("importedFrom", "imported_from"),
        ):
            value = raw.get(source_key)
            if value is not None:
                metadata[target_key] = value
        for source_key, target_key in (
            ("supportsTools", "supports_tool_call"),
            ("supportsImageInput", "supports_vision"),
            ("public", "public"),
            ("supportsServerless", "supports_serverless"),
        ):
            value = raw.get(source_key)
            if isinstance(value, bool):
                metadata[target_key] = value
        details = raw.get("baseModelDetails")
        if isinstance(details, Mapping):
            if details.get("parameterCount") is not None:
                metadata["parameter_count"] = str(details["parameterCount"])
            for source_key, target_key in (
                ("worldSize", "world_size"),
                ("checkpointFormat", "checkpoint_format"),
                ("modelType", "model_type"),
                ("defaultPrecision", "default_precision"),
            ):
                value = details.get(source_key)
                if value is not None:
                    metadata[target_key] = value
        return metadata


@dataclass(frozen=True)
class OpenCodeProviderAdapter(HttpBearerProviderAdapter):
    name: str = "opencode"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["opencode"]
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "OpenCode Zen"
    api_key_launch_error_value: str = "Launch blocked: OpenCode Zen requires a OpenCode Zen API key."
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="anthropic_messages", supports_thinking=True, requires_api_key=True
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(chat_path="/messages", models_path="/v1/models")
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
            capacity_strategy="configured_first", settings_strategy="standard", hosted_timeout=True
        )

    def router_native_anthropic_enabled(self, config: ProviderConfig, model: str | None = None) -> bool:
        return bool(config.options.get("native_compat", True)) and (
            self.select_protocol("anthropic_messages", config, model) == "anthropic_messages"
        )

    def option_presentation_policy(self, config: ProviderConfig) -> ProviderOptionPresentationPolicy:
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

    def select_protocol(self, operation: MessageProtocol, config: ProviderConfig, model: str | None = None) -> MessageProtocol:
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
                normalized = normalized[len(prefix):]
                break
        if self.name == "opencode-go":
            if normalized.startswith(("glm-", "kimi-", "deepseek-", "mimo-", "hy3-")):
                return "openai_chat"
            return "anthropic_messages"
        if normalized.startswith("gpt-"):
            return "openai_responses"
        if normalized.startswith("gemini-"):
            return "google_generative"
        if normalized.startswith(("minimax-", "glm-", "kimi-", "grok-", "big-pickle", "deepseek-", "mimo-", "nemotron-", "north-")):
            return "openai_chat"
        return "anthropic_messages"

    def supported_protocols(self, config: ProviderConfig, model: str | None = None) -> frozenset[MessageProtocol]:
        return frozenset({self.select_protocol("anthropic_messages", config, model)})

    def status_policy(self, config: ProviderConfig) -> ProviderStatusPolicy:
        del config
        return ProviderStatusPolicy(kind="catalog", label=self.api_key_display_name_value, catalog_path="/v1/models")

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

    def configuration_policy(self, config: ProviderConfig) -> ProviderConfigurationPolicy:
        del config
        return _configuration_policy(supports_model_endpoint_overrides=True)


@dataclass(frozen=True)
class OpenCodeGoProviderAdapter(OpenCodeProviderAdapter):
    name: str = "opencode-go"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["opencode-go"]
    api_key_display_name_value: str = "OpenCode Go"
    api_key_launch_error_value: str = "Launch blocked: OpenCode Go requires a OpenCode Go API key."


PROVIDER_ADAPTERS: AdapterRegistry[ProviderAdapter] = AdapterRegistry()


def _configured_factory(adapter_type: type[ProviderAdapter]):
    def create(**kwargs: Any) -> ProviderAdapter:
        base_url = str(kwargs.get("base_url") or "").strip()
        return adapter_type(**({"base_url": base_url} if base_url else {}))

    return create


_PROVIDER_DEFINITIONS: dict[str, tuple[type[ProviderAdapter], str]] = {
    "anthropic": (AnthropicProviderAdapter, "Claude Native"),
    "agy": (AgyProviderAdapter, "AGY"),
    "codex": (CodexProviderAdapter, "Codex Native"),
    "ollama": (OllamaProviderAdapter, "Ollama"),
    "ollama-cloud": (OllamaCloudProviderAdapter, "Ollama Cloud"),
    "deepseek": (DeepSeekProviderAdapter, "DeepSeek.com"),
    "opencode": (OpenCodeProviderAdapter, "OpenCode Zen"),
    "opencode-go": (OpenCodeGoProviderAdapter, "OpenCode Go"),
    "kimi": (KimiProviderAdapter, "Kimi.com"),
    "zai": (ZaiProviderAdapter, "Z.AI GLM"),
    "vllm": (VllmProviderAdapter, "vLLM"),
    "lm-studio": (LMStudioProviderAdapter, "LM Studio"),
    "nvidia-hosted": (NvidiaHostedProviderAdapter, "Nvidia Hosted"),
    "self-hosted-nim": (SelfHostedNimProviderAdapter, "Self Hosted NIM"),
    "openrouter": (OpenRouterProviderAdapter, "OpenRouter"),
    "fireworks": (FireworksProviderAdapter, "Fireworks.ai"),
}
PROVIDER_LABELS: dict[str, str] = {
    name: label for name, (_, label) in _PROVIDER_DEFINITIONS.items()
}

for _provider_name, (_adapter_type, _provider_label) in _PROVIDER_DEFINITIONS.items():
    PROVIDER_ADAPTERS.register(_provider_name, _configured_factory(_adapter_type))


__all__ = [
    "PROVIDER_ADAPTERS",
    "PROVIDER_DEFAULT_BASE_URLS",
    "PROVIDER_LABELS",
    "HttpBearerProviderAdapter",
    "NoAuthProviderAdapter",
    "AgyProviderAdapter",
    "AnthropicProviderAdapter",
    "CodexProviderAdapter",
    "DeepSeekProviderAdapter",
    "FireworksProviderAdapter",
    "KimiProviderAdapter",
    "LMStudioProviderAdapter",
    "NvidiaHostedProviderAdapter",
    "OllamaCloudProviderAdapter",
    "OllamaProviderAdapter",
    "OpenCodeGoProviderAdapter",
    "OpenCodeProviderAdapter",
    "OpenRouterProviderAdapter",
    "SelfHostedNimProviderAdapter",
    "VllmProviderAdapter",
    "ZaiProviderAdapter",
    "ZAI_MODEL_FALLBACK_IDS",
]
