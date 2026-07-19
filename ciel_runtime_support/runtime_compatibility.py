"""Runtime/provider compatibility policy.

Runtime selection is a cross-boundary concern. Provider adapters expose upstream
behavior; this module owns which local CLI runtime can launch for a selected
provider configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class RuntimeCompatibilityPolicy:
    native_runtime_by_provider: Mapping[str, str] = field(default_factory=dict)
    provider_family_labels: Mapping[str, str] = field(default_factory=dict)
    routed_runtimes: frozenset[str] = frozenset({"claude", "codex"})

    def supports(self, runtime: str, provider: str) -> bool:
        native_runtime = self.native_runtime_by_provider.get(provider)
        if native_runtime:
            return runtime == native_runtime
        return runtime in self.routed_runtimes

    def provider_family(self, provider: str, fallback: str) -> str:
        return self.provider_family_labels.get(provider, fallback)


DEFAULT_RUNTIME_COMPATIBILITY = RuntimeCompatibilityPolicy(
    native_runtime_by_provider={
        "anthropic": "claude",
        "agy": "agy",
        "codex": "codex",
    },
    provider_family_labels={
        "anthropic": "Anthropic",
        "agy": "AGY",
        "codex": "Codex",
    },
)


__all__ = ["DEFAULT_RUNTIME_COMPATIBILITY", "RuntimeCompatibilityPolicy"]
