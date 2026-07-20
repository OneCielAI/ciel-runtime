"""Runtime routing and provider-native compatibility policies."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimeModePolicy:
    parse_bool: Callable[..., bool]
    runtime_providers: Mapping[str, str]

    def native_enabled(self, runtime: str, provider: str) -> bool:
        return provider == self.runtime_providers[runtime]

    def routed_enabled(
        self, runtime: str, provider: str, config: dict[str, Any]
    ) -> bool:
        return self.native_enabled(runtime, provider) and self.parse_bool(
            config.get("route_through_router"), default=False
        )

    def direct_enabled(
        self, runtime: str, provider: str, config: dict[str, Any]
    ) -> bool:
        return self.native_enabled(runtime, provider) and not self.routed_enabled(
            runtime, provider, config
        )

    def native_anthropic(self, provider: str) -> bool:
        return self.native_enabled("anthropic", provider)

    def anthropic_routed(
        self, provider: str, config: dict[str, Any]
    ) -> bool:
        return self.routed_enabled("anthropic", provider, config)

    def direct_anthropic(
        self, provider: str, config: dict[str, Any]
    ) -> bool:
        return self.direct_enabled("anthropic", provider, config)

    def native_agy(self, provider: str) -> bool:
        return self.native_enabled("agy", provider)

    def agy_routed(self, provider: str, config: dict[str, Any]) -> bool:
        return self.routed_enabled("agy", provider, config)

    def direct_agy(self, provider: str, config: dict[str, Any]) -> bool:
        return self.direct_enabled("agy", provider, config)

    def native_codex(self, provider: str) -> bool:
        return self.native_enabled("codex", provider)

    def codex_routed(self, provider: str, config: dict[str, Any]) -> bool:
        return self.routed_enabled("codex", provider, config)

    def direct_codex(self, provider: str, config: dict[str, Any]) -> bool:
        return self.direct_enabled("codex", provider, config)


@dataclass(frozen=True, slots=True)
class ProviderNativeCompatibilityPolicy:
    native_enabled: Callable[[str, dict[str, Any]], bool]
    compatibility_groups: Mapping[str, frozenset[str]]

    def group_enabled(
        self, group: str, provider: str, config: dict[str, Any]
    ) -> bool:
        return (
            provider in self.compatibility_groups[group]
            and self.native_enabled(provider, config)
        )

    def ollama(self, provider: str, config: dict[str, Any]) -> bool:
        return self.group_enabled("ollama", provider, config)

    def vllm(self, provider: str, config: dict[str, Any]) -> bool:
        return self.group_enabled("vllm", provider, config)

    def nim(self, provider: str, config: dict[str, Any]) -> bool:
        return self.group_enabled("nim", provider, config)

    def lm_studio(self, provider: str, config: dict[str, Any]) -> bool:
        return self.group_enabled("lm_studio", provider, config)

    def nvidia(self, provider: str, config: dict[str, Any]) -> bool:
        return self.group_enabled("nvidia", provider, config)

    def deepseek(self, provider: str, config: dict[str, Any]) -> bool:
        return self.group_enabled("deepseek", provider, config)

    def opencode(self, provider: str, config: dict[str, Any]) -> bool:
        return self.group_enabled("opencode", provider, config)

    def kimi(self, provider: str, config: dict[str, Any]) -> bool:
        return self.group_enabled("kimi", provider, config)

    def zai(self, provider: str, config: dict[str, Any]) -> bool:
        return self.group_enabled("zai", provider, config)

    def fireworks(self, provider: str, config: dict[str, Any]) -> bool:
        return self.group_enabled("fireworks", provider, config)
