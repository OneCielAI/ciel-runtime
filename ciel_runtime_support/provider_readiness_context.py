"""Provider configuration status and launch-readiness bounded context.

This module owns the application-level coordination between provider adapters,
status projection, and launch-readiness validation.  The compatibility facade
supplies concrete ports; no support module reaches back into ``ciel_runtime``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .architecture import ProviderAdapter, ProviderConfig
from .provider_readiness import (
    ProviderReadinessServices,
    launch_readiness_errors as evaluate_provider_readiness,
)
from .provider_status import (
    ProviderStatusServices,
    base_url_status_line as project_provider_base_url_status,
)


@dataclass(frozen=True, slots=True)
class ProviderDefaultsPorts:
    nvidia_upstream_base_url: Callable[[], str]
    adapter_exists: Callable[[str], bool]
    adapter_default_base_url: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class ProviderCredentialPorts:
    key_count: Callable[[str, dict[str, Any]], int]
    primary_key: Callable[[str, dict[str, Any]], str]
    mask_secret: Callable[[str], str]
    secret_fingerprint: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class ProviderConfigurationPorts:
    load: Callable[[], dict[str, Any]]
    current: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]
    adapter: Callable[[str, dict[str, Any]], ProviderAdapter]
    contract: Callable[[str, dict[str, Any]], ProviderConfig]


@dataclass(frozen=True, slots=True)
class ProviderProjectionPorts:
    status_services: Callable[[], ProviderStatusServices]
    readiness_services: Callable[[], ProviderReadinessServices]
    notes: Mapping[str, Mapping[str, list[str]]]


@dataclass(frozen=True, slots=True)
class ProviderReadinessContext:
    defaults: ProviderDefaultsPorts
    credentials: ProviderCredentialPorts
    configuration: ProviderConfigurationPorts
    projection: ProviderProjectionPorts

    def default_base_url(self, provider: str) -> str:
        if provider == "nvidia-hosted":
            return self.defaults.nvidia_upstream_base_url()
        if self.defaults.adapter_exists(provider):
            configured = self.defaults.adapter_default_base_url(provider)
            if configured:
                return configured
        return "http://localhost:8000"

    @staticmethod
    def meaningful_key(value: str | None) -> bool:
        text = str(value or "").strip()
        return bool(text and text.lower() not in {"none", "null"})

    def api_key_status_line(self, provider: str, pcfg: dict[str, Any]) -> str:
        key_count = self.credentials.key_count(provider, pcfg)
        primary = self.credentials.primary_key(provider, pcfg)
        primary_detail = (
            f"; primary {self.credentials.mask_secret(primary)}; "
            f"fp {self.credentials.secret_fingerprint(primary)}"
            if key_count
            else ""
        )
        adapter = self.configuration.adapter(provider, pcfg)
        return adapter.api_key_status(
            self.configuration.contract(provider, pcfg),
            key_count=key_count,
            primary_detail=primary_detail,
        )

    def base_url_status_line(self, provider: str, pcfg: dict[str, Any]) -> str:
        adapter = self.configuration.adapter(provider, pcfg)
        status_policy = adapter.status_policy(
            self.configuration.contract(provider, pcfg)
        )
        return project_provider_base_url_status(
            provider,
            pcfg,
            status_policy,
            services=self.projection.status_services(),
        )

    def preflight_lines(self) -> list[str]:
        cfg = self.configuration.load()
        provider, pcfg = self.configuration.current(cfg)
        lang = str(cfg.get("language") or "en")
        localized = self.projection.notes.get(
            lang, self.projection.notes.get("en", {})
        )
        notes = localized.get(provider, [])
        return [
            self.base_url_status_line(provider, pcfg),
            self.api_key_status_line(provider, pcfg),
            *notes,
        ]

    def launch_readiness_errors(
        self, cfg: dict[str, Any] | None = None
    ) -> list[str]:
        cfg = cfg or self.configuration.load()
        provider, pcfg = self.configuration.current(cfg)
        adapter = self.configuration.adapter(provider, pcfg)
        contract = self.configuration.contract(provider, pcfg)
        status_policy = adapter.status_policy(contract)
        return evaluate_provider_readiness(
            cfg,
            provider,
            pcfg,
            adapter,
            contract,
            status_policy,
            services=self.projection.readiness_services(),
        )

    @staticmethod
    def launch_blockers_require_api_key(blockers: list[str]) -> bool:
        return any(
            "requires" in line.lower() and "api key" in line.lower()
            for line in blockers
        )

    def settings_ready_except_api_key(self) -> bool:
        cfg = self.configuration.load()
        provider, pcfg = self.configuration.current(cfg)
        if provider == "codex":
            return True
        base = pcfg.get("base_url", "")
        model = pcfg.get("current_model", "")
        return bool(provider and base and model and "your-" not in base)


@dataclass(frozen=True, slots=True)
class ProviderReadinessCompatibilityApi:
    """Late-bound facade API that preserves patchable composition ports."""

    context: Callable[[], ProviderReadinessContext]

    def default_base_url(self, provider: str) -> str:
        return self.context().default_base_url(provider)

    def api_key_status_line(self, provider: str, pcfg: dict[str, Any]) -> str:
        return self.context().api_key_status_line(provider, pcfg)

    def base_url_status_line(self, provider: str, pcfg: dict[str, Any]) -> str:
        return self.context().base_url_status_line(provider, pcfg)

    def preflight_lines(self) -> list[str]:
        return self.context().preflight_lines()

    def launch_readiness_errors(
        self, cfg: dict[str, Any] | None = None
    ) -> list[str]:
        return self.context().launch_readiness_errors(cfg)

    def launch_blockers_require_api_key(self, blockers: list[str]) -> bool:
        return self.context().launch_blockers_require_api_key(blockers)

    def settings_ready_except_api_key(self) -> bool:
        return self.context().settings_ready_except_api_key()


__all__ = [
    "ProviderConfigurationPorts",
    "ProviderCredentialPorts",
    "ProviderDefaultsPorts",
    "ProviderProjectionPorts",
    "ProviderReadinessCompatibilityApi",
    "ProviderReadinessContext",
]
