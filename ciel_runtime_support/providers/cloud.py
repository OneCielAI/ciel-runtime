"""Adapters for cloud providers whose endpoint is deployment-specific."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..architecture import (
    ProviderCapabilities,
    ProviderConfig,
    ProviderModelCatalogPolicy,
    ProviderRequestPolicy,
)
from .base import OpenAICompatibleProviderAdapter, provider_configuration


@dataclass(frozen=True)
class AzureOpenAIProviderAdapter(OpenAICompatibleProviderAdapter):
    """Azure OpenAI deployment transport with raw ``api-key`` authentication."""

    name: str = "azure"
    base_url: str = ""
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            "",
            native_compat=False,
            api_version="2024-10-21",
            context_window=128000,
            max_output_tokens=8192,
            context_reserve_tokens=4096,
            request_timeout_ms=300000,
            stream_enabled=True,
            stream_word_chunking=False,
        )
    )
    require_api_key: bool = True
    api_key_display_name_value: str = "Azure OpenAI"
    api_key_launch_error_value: str = (
        "Launch blocked: Azure OpenAI requires a deployment endpoint and API key."
    )
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="openai_chat",
            requires_api_key=True,
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/chat/completions",
            models_path="/models",
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(
            kind="configured",
            allow_configured_fallback=True,
        )
    )

    def build_headers(
        self,
        config: ProviderConfig,
        api_key: str | None,
    ) -> Mapping[str, str]:
        del config
        key = str(api_key or "").strip()
        if not key:
            raise RuntimeError("azure requires a configured API key.")
        return {"api-key": key}

    def build_model_headers(
        self,
        config: ProviderConfig,
        api_key: str | None,
    ) -> Mapping[str, str]:
        return self.build_headers(config, api_key)

    def resolve_endpoint(self, operation: str, config: ProviderConfig) -> str:
        policy = self.request_policy(config)
        paths = {
            "chat": policy.chat_path,
            "openai_chat": policy.chat_path,
            "models": policy.models_path,
        }
        path = paths.get(operation)
        if path is None:
            return super().resolve_endpoint(operation, config)
        version = str(config.options.get("api_version") or "2024-10-21").strip()
        if operation in {"chat", "openai_chat", "models"} and version:
            separator = "&" if "?" in path else "?"
            return f"{path}{separator}api-version={version}"
        return path

    def router_native_anthropic_enabled(
        self,
        config: ProviderConfig,
        model: str | None = None,
    ) -> bool:
        del config, model
        return False


@dataclass(frozen=True)
class CodeBuddyCnProviderAdapter(OpenAICompatibleProviderAdapter):
    """Tencent CodeBuddy OpenAI-compatible transport."""

    name: str = "codebuddy-cn"
    base_url: str = "https://copilot.tencent.com/v2"
    configuration_defaults_value: dict = field(
        default_factory=lambda: provider_configuration(
            "glm-5.2",
            custom_models=(
                "glm-5.2",
                "glm-5.1",
                "minimax-m2.7",
                "kimi-k2.7",
                "deepseek-v4-pro",
            ),
            native_compat=False,
            context_window=200000,
            max_output_tokens=8192,
            context_reserve_tokens=4096,
            request_timeout_ms=300000,
            stream_enabled=True,
            stream_word_chunking=False,
        )
    )
    require_api_key: bool = True
    include_x_api_key: bool = False
    api_key_display_name_value: str = "CodeBuddy CN"
    api_key_launch_error_value: str = (
        "Launch blocked: CodeBuddy CN requires an API key or access token."
    )
    capabilities_value: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            upstream_protocol="openai_chat",
            requires_api_key=True,
        )
    )
    request_policy_value: ProviderRequestPolicy = field(
        default_factory=lambda: ProviderRequestPolicy(
            chat_path="/chat/completions",
            models_path="/models",
        )
    )
    model_catalog_policy_value: ProviderModelCatalogPolicy = field(
        default_factory=lambda: ProviderModelCatalogPolicy(
            kind="openai",
            fallback_models=(
                "glm-5.2",
                "glm-5.1",
                "minimax-m2.7",
                "kimi-k2.7",
                "deepseek-v4-pro",
            ),
            allow_configured_fallback=True,
        )
    )

    def build_headers(
        self,
        config: ProviderConfig,
        api_key: str | None,
    ) -> Mapping[str, str]:
        headers = dict(super().build_headers(config, api_key))
        headers.update(
            {
                "User-Agent": "Ciel-Runtime",
                "X-Product": "SaaS",
                "X-IDE-Type": "CLI",
                "X-IDE-Name": "CLI",
                "x-requested-with": "XMLHttpRequest",
                "x-codebuddy-request": "1",
            }
        )
        return headers

    def build_model_headers(
        self,
        config: ProviderConfig,
        api_key: str | None,
    ) -> Mapping[str, str]:
        return self.build_headers(config, api_key)

    def router_native_anthropic_enabled(
        self,
        config: ProviderConfig,
        model: str | None = None,
    ) -> bool:
        del config, model
        return False


__all__ = ["AzureOpenAIProviderAdapter", "CodeBuddyCnProviderAdapter"]
