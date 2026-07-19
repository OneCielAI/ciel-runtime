"""Fireworks.ai provider adapter."""

from dataclasses import dataclass, field, replace
import re
from typing import Any, Mapping
from urllib.parse import quote

from ..architecture import (
    MessageProtocol,
    ProviderCapabilities,
    ProviderConfigurationPolicy,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderModelCatalogPolicy,
    ProviderOptionPresentationPolicy,
    ProviderStatusPolicy,
)
from .base import OpenAICompatibleProviderAdapter, configuration_policy
from .constants import PROVIDER_DEFAULT_BASE_URLS


@dataclass(frozen=True)
class FireworksProviderAdapter(OpenAICompatibleProviderAdapter):
    name: str = "fireworks"
    base_url: str = PROVIDER_DEFAULT_BASE_URLS["fireworks"]
    send_placeholder_key: bool = True
    api_key_display_name_value: str = "Fireworks.ai"
    api_key_launch_error_value: str = (
        "Launch blocked: Fireworks.ai requires a Fireworks API key."
    )
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="openai_chat", requires_api_key=True
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(kind="fireworks")
    )

    def context_policy(self, config: ProviderConfig) -> ProviderContextPolicy:
        del config
        return ProviderContextPolicy(
            capacity_strategy="configured_first",
            settings_strategy="standard",
            hosted_timeout=True,
        )

    def option_presentation_policy(
        self, config: ProviderConfig
    ) -> ProviderOptionPresentationPolicy:
        return replace(super().option_presentation_policy(config), show_sampling=False)

    def supported_protocols(
        self, config: ProviderConfig, model: str | None = None
    ) -> frozenset[MessageProtocol]:
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

    def select_protocol(
        self,
        operation: MessageProtocol,
        config: ProviderConfig,
        model: str | None = None,
    ) -> MessageProtocol:
        del config, model
        return (
            "openai_chat"
            if operation in {"openai_chat", "openai_responses"}
            else "anthropic_messages"
        )

    def configuration_policy(
        self, config: ProviderConfig
    ) -> ProviderConfigurationPolicy:
        del config
        return configuration_policy(
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


__all__ = ["FireworksProviderAdapter"]
