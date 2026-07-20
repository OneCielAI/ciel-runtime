"""Provider protocol endpoint override and presentation policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderEndpointPorts:
    normalize_model_id: Callable[[str, str], str]
    strip_context_suffix: Callable[[str], str]
    alias_for: Callable[[str, str], str]
    select_protocol: Callable[[str, dict[str, Any], str], str]


@dataclass(frozen=True, slots=True)
class ProviderEndpointPresentation:
    aliases: Mapping[str, str]
    labels: Mapping[str, str]
    routed_protocols: frozenset[str]


@dataclass(frozen=True, slots=True)
class ProviderEndpointPolicy:
    ports: ProviderEndpointPorts
    presentation: ProviderEndpointPresentation

    def normalize_endpoint_kind(self, value: Any) -> str | None:
        key = str(value or "").strip().lower().replace("_", "-")
        return self.presentation.aliases.get(key)

    def endpoint_override(
        self,
        provider: str,
        model_id: str,
        provider_config: dict[str, Any] | None = None,
    ) -> str | None:
        if not provider_config:
            return None
        overrides = provider_config.get("model_endpoints")
        if not isinstance(overrides, dict):
            return None
        normalized = self.ports.normalize_model_id(provider, model_id)
        candidates = (
            model_id,
            normalized,
            self.ports.strip_context_suffix(model_id),
            self.ports.alias_for(provider, normalized),
        )
        for candidate in candidates:
            endpoint = self.normalize_endpoint_kind(overrides.get(candidate))
            if endpoint:
                return endpoint
        return None

    def endpoint_kind(
        self,
        provider: str,
        model_id: str,
        provider_config: dict[str, Any] | None = None,
    ) -> str:
        override = self.endpoint_override(provider, model_id, provider_config)
        if override:
            return override
        config = provider_config or {}
        protocol = self.ports.select_protocol(
            provider,
            config,
            model_id,
        )
        return str(protocol).replace("_", "-")

    def model_supported(
        self,
        provider: str,
        model_id: str,
        provider_config: dict[str, Any] | None = None,
    ) -> bool:
        return (
            self.endpoint_kind(provider, model_id, provider_config)
            in self.presentation.routed_protocols
        )

    def endpoint_display(
        self,
        provider: str,
        model_id: str,
        provider_config: dict[str, Any] | None = None,
    ) -> str:
        endpoint = self.endpoint_kind(provider, model_id, provider_config)
        text = self.presentation.labels.get(endpoint, endpoint)
        if endpoint not in self.presentation.routed_protocols:
            text += " unsupported"
        if self.endpoint_override(provider, model_id, provider_config):
            text += " override"
        return text

    def zen_endpoint_kind(self, model_id: str) -> str:
        return self.endpoint_kind("opencode", model_id)

    def zen_model_supported(self, model_id: str) -> bool:
        return self.model_supported("opencode", model_id)

    def go_endpoint_kind(self, model_id: str) -> str:
        return self.endpoint_kind("opencode-go", model_id)
