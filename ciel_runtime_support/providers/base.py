"""Shared transport bases for concrete provider adapters."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..architecture import (
    MessageProtocol,
    ModelInfo,
    ProviderAdapter,
    ProviderCapabilities,
    ProviderConfigurationPolicy,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderModelCatalogPolicy,
    ProviderOptionPresentationPolicy,
    ProviderRequestPolicy,
)


ROUTER_STATUS_FIELDS: tuple[str, ...] = (
    "context_window",
    "context_reserve_tokens",
    "max_output_tokens",
    "request_timeout_ms",
    "stream_idle_timeout_ms",
)


def configuration_policy(**values: Any) -> ProviderConfigurationPolicy:
    status_fields = values.pop("status_fields", ROUTER_STATUS_FIELDS)
    return ProviderConfigurationPolicy(status_fields=status_fields, **values)


def provider_configuration(
    current_model: str = "",
    *,
    api_key: str = "",
    custom_models: Sequence[str] = (),
    **values: Any,
) -> dict[str, Any]:
    """Build the common persisted configuration shape for one provider."""

    return {
        "api_key": api_key,
        "current_model": current_model,
        "advisor_model": "",
        "custom_models": list(custom_models),
        **values,
    }


@dataclass(frozen=True)
class HttpBearerProviderAdapter(ProviderAdapter):
    """Provider adapter for bearer/x-api-key compatible APIs."""

    name: str
    base_url: str = ""
    configuration_defaults_value: Mapping[str, Any] = field(default_factory=dict)
    authorization_header: str = "authorization"
    include_x_api_key: bool = True
    require_api_key: bool = False
    send_placeholder_key: bool = False
    api_key_display_name_value: str = ""
    api_key_launch_error_value: str = ""
    capabilities_value: ProviderCapabilities = field(
        default_factory=ProviderCapabilities
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/v1/chat/completions",
            models_path="/v1/models",
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=ProviderModelCatalogPolicy
    )

    def default_base_url(self) -> str:
        return self.base_url

    def default_configuration(self) -> Mapping[str, Any]:
        defaults = dict(super().default_configuration())
        defaults.update(deepcopy(dict(self.configuration_defaults_value)))
        defaults["base_url"] = self.default_base_url()
        return defaults

    def list_models(self, config: ProviderConfig) -> Sequence[ModelInfo]:
        raw_models: Any = (
            config.options.get("available_models") or config.options.get("models") or ()
        )
        if isinstance(raw_models, str):
            raw_models = [raw_models]
        models: list[ModelInfo] = []
        if isinstance(raw_models, (list, tuple)):
            for item in raw_models:
                if isinstance(item, str) and item.strip():
                    models.append(ModelInfo(id=item.strip()))
                elif isinstance(item, dict) and str(item.get("id") or "").strip():
                    models.append(ModelInfo(id=str(item["id"]).strip(), raw=item))
        if config.model and all(model.id != config.model for model in models):
            models.insert(0, ModelInfo(id=config.model))
        return tuple(models)

    def build_headers(
        self, config: ProviderConfig, api_key: str | None
    ) -> Mapping[str, str]:
        key = str(api_key or "").strip()
        if self.require_api_key and not key:
            raise RuntimeError(f"{self.name} requires a configured API key.")
        if not key and self.send_placeholder_key:
            key = "not-used"
        if not key:
            return {}
        headers = {self.authorization_header: f"Bearer {key}"}
        if self.include_x_api_key:
            headers["x-api-key"] = key
        return headers

    def capabilities(self, config: ProviderConfig) -> ProviderCapabilities:
        del config
        return self.capabilities_value

    def request_policy(self, config: ProviderConfig) -> ProviderRequestPolicy:
        del config
        return self.request_policy_value

    def model_catalog_policy(
        self, config: ProviderConfig
    ) -> ProviderModelCatalogPolicy:
        del config
        return self.model_catalog_policy_value

    def configuration_policy(
        self, config: ProviderConfig
    ) -> ProviderConfigurationPolicy:
        del config
        return configuration_policy()

    def api_key_display_name(self) -> str:
        return self.api_key_display_name_value

    def launch_api_key_error(self, config: ProviderConfig) -> str | None:
        if self.capabilities(config).requires_api_key and not config.api_keys:
            return self.api_key_launch_error_value or super().launch_api_key_error(
                config
            )
        return None

    def build_model_headers(
        self, config: ProviderConfig, api_key: str | None
    ) -> Mapping[str, str]:
        del config
        key = str(api_key or "").strip()
        if not key:
            return {}
        headers = {self.authorization_header: f"Bearer {key}"}
        if self.include_x_api_key:
            headers["x-api-key"] = key
        return headers


@dataclass(frozen=True)
class NoAuthProviderAdapter(HttpBearerProviderAdapter):
    def build_headers(
        self, config: ProviderConfig, api_key: str | None
    ) -> Mapping[str, str]:
        del config, api_key
        return {}


@dataclass(frozen=True)
class OpenAICompatibleProviderAdapter(HttpBearerProviderAdapter):
    """Base for providers that implement the OpenAI Chat/Models wire surface."""

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="hint_configured", settings_strategy="standard"
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
            show_sampling=True,
            show_stream=True,
            show_rate_limit_controls=True,
            show_sampling_controls=True,
            show_ip_family_control=True,
        )

    def supported_protocols(
        self, config: ProviderConfig, model: str | None = None
    ) -> frozenset[MessageProtocol]:
        del model
        protocols: set[MessageProtocol] = {"openai_chat"}
        native = config.options.get("native_compat")
        if native is True or str(native).strip().lower() in {"1", "true", "yes", "on"}:
            protocols.add("anthropic_messages")
        return frozenset(protocols)

    def select_protocol(
        self,
        operation: MessageProtocol,
        config: ProviderConfig,
        model: str | None = None,
    ) -> MessageProtocol:
        supported = self.supported_protocols(config, model)
        if operation == "anthropic_messages" and "anthropic_messages" in supported:
            return "anthropic_messages"
        return "openai_chat"


__all__ = [
    "HttpBearerProviderAdapter",
    "NoAuthProviderAdapter",
    "OpenAICompatibleProviderAdapter",
    "ROUTER_STATUS_FIELDS",
    "configuration_policy",
]
