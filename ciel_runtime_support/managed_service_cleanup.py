"""Provider-independent managed service cleanup policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .architecture import ProviderRequestPolicy


class ProviderModeQuery(Protocol):
    def __call__(self, provider: str, config: dict[str, Any]) -> bool: ...


class RouterStop(Protocol):
    def __call__(self, reason: str, quiet: bool = True) -> bool: ...


class ManagedServiceStop(Protocol):
    def __call__(self, quiet: bool = False) -> bool: ...


class ProviderRequestPolicyQuery(Protocol):
    def __call__(
        self,
        provider: str,
        config: dict[str, Any],
    ) -> ProviderRequestPolicy: ...


@dataclass(frozen=True, slots=True)
class ManagedServiceCleanupPorts:
    direct_native_anthropic: ProviderModeQuery
    direct_native_codex: ProviderModeQuery
    direct_native_agy: ProviderModeQuery
    request_policy: ProviderRequestPolicyQuery
    native_compat_enabled: ProviderModeQuery
    stop_idle_router: RouterStop
    stop_nvidia_proxy: ManagedServiceStop


@dataclass(frozen=True, slots=True)
class ManagedServiceCleanupPolicy:
    ports: ManagedServiceCleanupPorts

    def managed_service_required(
        self,
        provider: str,
        provider_config: dict[str, Any],
    ) -> bool:
        policy = self.ports.request_policy(provider, provider_config)
        return (
            policy.managed_service != "none"
            and not self.ports.native_compat_enabled(provider, provider_config)
        )

    def cleanup(
        self,
        provider: str,
        provider_config: dict[str, Any],
        runtime_config: dict[str, Any],
        quiet: bool = False,
    ) -> None:
        if self.ports.direct_native_anthropic(provider, provider_config):
            self.ports.stop_idle_router("native_anthropic_launch", quiet=quiet)
            if not self.managed_service_required(provider, provider_config):
                self.ports.stop_nvidia_proxy(quiet=quiet)
            return
        if self.ports.direct_native_codex(provider, provider_config):
            self.ports.stop_idle_router("native_codex_launch", quiet=quiet)
            self.ports.stop_nvidia_proxy(quiet=quiet)
            return
        if self.ports.direct_native_agy(provider, provider_config):
            self.ports.stop_idle_router("native_agy_launch", quiet=quiet)
            self.ports.stop_nvidia_proxy(quiet=quiet)
            return
        if not runtime_config.get("cleanup", {}).get(
            "managed_services_on_launch",
            True,
        ):
            return
        if not self.managed_service_required(provider, provider_config):
            self.ports.stop_nvidia_proxy(quiet=quiet)


__all__ = [
    "ManagedServiceCleanupPolicy",
    "ManagedServiceCleanupPorts",
]
