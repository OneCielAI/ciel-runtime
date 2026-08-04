"""Provider model cache identity, capability, and catalog header policy."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from . import anthropic_model_policy


@dataclass(frozen=True, slots=True)
class ModelCapabilityPorts:
    normalize_capabilities: Callable[[Any], list[str]]
    current_model: Callable[[str, dict[str, Any]], str]
    strip_context_suffix: Callable[[str], str]
    is_kimi_k3: Callable[[str], bool]
    parse_bool: Callable[..., bool]


@dataclass(frozen=True, slots=True)
class ModelRegistryRecommendationPorts:
    unique_model_ids: Callable[[list[str]], list[str]]
    preset_timeout_ms: Callable[[str], int]
    timeout_idle_ms: Callable[[str], int]


@dataclass(frozen=True, slots=True)
class ModelCatalogHeaderPorts:
    api_key_count: Callable[[str, dict[str, Any]], int]
    read_env_file: Callable[[Path], dict[str, str]]
    environment: Mapping[str, str]
    user_agent_headers: Callable[..., dict[str, str]]
    primary_api_key: Callable[[str, dict[str, Any]], str | None]
    meaningful_key: Callable[[str | None], bool]
    configured_adapter: Callable[..., Any]
    contract_config: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ProviderModelMetadataContext:
    capabilities: ModelCapabilityPorts
    recommendations: ModelRegistryRecommendationPorts
    headers: ModelCatalogHeaderPorts
    nvidia_env: Path

    def cache_key(self, provider: str, pcfg: dict[str, Any]) -> str:
        api_state = (
            "key" if self.headers.api_key_count(provider, pcfg) else "nokey"
        )
        return json.dumps(
            {
                "provider": provider,
                "base_url": pcfg.get("base_url", ""),
                "model_api_base_url": pcfg.get("model_api_base_url", ""),
                "account_id": pcfg.get("account_id", ""),
                "api": api_state,
                "custom": pcfg.get("custom_models", []),
                "schema": 7,
            },
            sort_keys=True,
        )

    def infer_claude_capabilities(self, model_id: str) -> list[str]:
        return anthropic_model_policy.infer_capabilities(
            model_id, self.capabilities.strip_context_suffix
        )

    def claude_capabilities(
        self,
        provider: str,
        pcfg: dict[str, Any],
        model_id: str | None = None,
    ) -> list[str]:
        caps = self.capabilities.normalize_capabilities(
            pcfg.get("claude_code_supported_capabilities")
        )
        model = model_id or self.capabilities.current_model(provider, pcfg)
        if not caps:
            caps = self.infer_claude_capabilities(model)
        if (
            provider == "kimi"
            and self.capabilities.is_kimi_k3(model)
            and "max_effort" not in caps
        ):
            caps.append("max_effort")
        return caps

    def claude_capability_string(
        self,
        provider: str,
        pcfg: dict[str, Any],
        model_id: str | None = None,
    ) -> str:
        return ",".join(self.claude_capabilities(provider, pcfg, model_id))

    def workflows_enabled(self, provider: str, pcfg: dict[str, Any]) -> bool:
        del provider
        ultracode = (
            pcfg.get("ultracode_enabled")
            if "ultracode_enabled" in pcfg
            else pcfg.get("ultracode")
        )
        if self.capabilities.parse_bool(ultracode, False):
            return True
        value = (
            pcfg.get("workflows_enabled")
            if "workflows_enabled" in pcfg
            else pcfg.get("workflows")
        )
        return self.capabilities.parse_bool(value, False)

    def ultracode_enabled(self, provider: str, pcfg: dict[str, Any]) -> bool:
        del provider
        value = (
            pcfg.get("ultracode_enabled")
            if "ultracode_enabled" in pcfg
            else pcfg.get("ultracode")
        )
        return self.capabilities.parse_bool(value, False)

    def registry_recommendations(
        self, provider: str, models: list[str]
    ) -> dict[str, Any]:
        return anthropic_model_policy.AnthropicModelRecommendations(
            self.recommendations.unique_model_ids,
            self.recommendations.preset_timeout_ms,
            self.recommendations.timeout_idle_ms,
        ).build(provider, models)

    def nvidia_list_headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        key = self.headers.read_env_file(self.nvidia_env).get(
            "NVIDIA_API_KEY"
        ) or self.headers.environment.get("NVIDIA_API_KEY")
        if key:
            headers["authorization"] = f"Bearer {key}"
            headers["x-api-key"] = key
        return headers

    def provider_list_headers(
        self, provider: str, pcfg: dict[str, Any]
    ) -> dict[str, str]:
        headers = self.headers.user_agent_headers(
            {"content-type": "application/json"}
        )
        key = self.headers.primary_api_key(provider, pcfg)
        meaningful = str(key) if self.headers.meaningful_key(key) else None
        adapter = self.headers.configured_adapter(provider, pcfg)
        headers.update(
            adapter.build_model_headers(
                self.headers.contract_config(provider, pcfg), meaningful
            )
        )
        return headers


@dataclass(frozen=True, slots=True)
class ProviderModelMetadataCompatibilityApi:
    context: Callable[[], ProviderModelMetadataContext]

    def cache_key(self, *args: Any, **kwargs: Any) -> str:
        return self.context().cache_key(*args, **kwargs)

    def infer_claude_capabilities(self, *args: Any, **kwargs: Any) -> list[str]:
        return self.context().infer_claude_capabilities(*args, **kwargs)

    def claude_capabilities(self, *args: Any, **kwargs: Any) -> list[str]:
        return self.context().claude_capabilities(*args, **kwargs)

    def claude_capability_string(self, *args: Any, **kwargs: Any) -> str:
        return self.context().claude_capability_string(*args, **kwargs)

    def workflows_enabled(self, *args: Any, **kwargs: Any) -> bool:
        return self.context().workflows_enabled(*args, **kwargs)

    def ultracode_enabled(self, *args: Any, **kwargs: Any) -> bool:
        return self.context().ultracode_enabled(*args, **kwargs)

    def registry_recommendations(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.context().registry_recommendations(*args, **kwargs)

    def nvidia_list_headers(self) -> dict[str, str]:
        return self.context().nvidia_list_headers()

    def provider_list_headers(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        return self.context().provider_list_headers(*args, **kwargs)


__all__ = [
    "ModelCapabilityPorts",
    "ModelCatalogHeaderPorts",
    "ModelRegistryRecommendationPorts",
    "ProviderModelMetadataCompatibilityApi",
    "ProviderModelMetadataContext",
]
