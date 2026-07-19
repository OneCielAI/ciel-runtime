"""Data-driven provider registration descriptors.

Descriptors contain discovery metadata and adapter construction only. Runtime
behavior remains in concrete adapters, so complex providers can use specialized
implementations without growing a central conditional router.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ciel_runtime_support.architecture import ProviderAdapter


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """Stable provider identity and its concrete adapter factory type."""

    name: str
    label: str
    adapter_type: type[ProviderAdapter]
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.normalized_name:
            raise ValueError("provider descriptor name cannot be empty")
        if not self.label.strip():
            raise ValueError(f"provider descriptor label cannot be empty: {self.name}")

    @property
    def normalized_name(self) -> str:
        return normalize_provider_name(self.name)

    def create(self, **kwargs: Any) -> ProviderAdapter:
        base_url = str(kwargs.get("base_url") or "").strip()
        return self.adapter_type(**({"base_url": base_url} if base_url else {}))


def normalize_provider_name(name: str) -> str:
    return str(name or "").strip().lower().replace("_", "-")


class ProviderDescriptorRegistry:
    """Read-only discovery index with normalized alias lookup."""

    def __init__(self, descriptors: tuple[ProviderDescriptor, ...]) -> None:
        self._descriptors: dict[str, ProviderDescriptor] = {}
        self._canonical: dict[str, ProviderDescriptor] = {}
        for descriptor in descriptors:
            name = descriptor.normalized_name
            if name in self._canonical:
                raise ValueError(f"provider descriptor already registered: {name}")
            self._canonical[name] = descriptor
            for candidate in (name, *descriptor.aliases):
                key = normalize_provider_name(candidate)
                if not key:
                    raise ValueError(f"provider alias cannot be empty: {name}")
                if key in self._descriptors:
                    raise ValueError(f"provider descriptor alias already registered: {key}")
                self._descriptors[key] = descriptor

    def get(self, name: str) -> ProviderDescriptor:
        try:
            return self._descriptors[normalize_provider_name(name)]
        except KeyError as exc:
            raise KeyError(f"unknown provider descriptor: {name}") from exc

    def descriptors(self) -> tuple[ProviderDescriptor, ...]:
        return tuple(self._canonical[name] for name in sorted(self._canonical))

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._canonical))

