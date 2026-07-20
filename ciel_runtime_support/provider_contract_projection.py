"""Provider-neutral projection of registered adapter contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .architecture import (
    ProviderAdapter,
    ProviderConfig,
    ProviderConfigurationPolicy,
    ProviderContextPolicy,
    ProviderModelCatalogPolicy,
    ProviderRequestPolicy,
)


@dataclass(frozen=True, slots=True)
class ProviderContractProjectionApi:
    """Project provider contracts without provider-name dispatch in the facade."""

    adapter: Callable[[str, dict[str, Any]], ProviderAdapter]
    contract: Callable[[str, dict[str, Any]], ProviderConfig]
    request_base: Callable[[str, dict[str, Any]], str]
    join_url: Callable[[str, str], str]

    def _parts(self, provider: str, pcfg: dict[str, Any]) -> tuple[ProviderAdapter, ProviderConfig]:
        return self.adapter(provider, pcfg), self.contract(provider, pcfg)

    def endpoint(self, provider: str, pcfg: dict[str, Any], operation: str) -> str:
        adapter, contract = self._parts(provider, pcfg)
        return self.join_url(self.request_base(provider, pcfg), adapter.resolve_endpoint(operation, contract))

    def model_paths(self, provider: str, pcfg: dict[str, Any]) -> tuple[str, ...]:
        adapter, contract = self._parts(provider, pcfg)
        return adapter.model_paths(contract)

    def request_policy(self, provider: str, pcfg: dict[str, Any]) -> ProviderRequestPolicy:
        adapter, contract = self._parts(provider, pcfg)
        return adapter.request_policy(contract)

    def model_catalog_policy(self, provider: str, pcfg: dict[str, Any]) -> ProviderModelCatalogPolicy:
        adapter, contract = self._parts(provider, pcfg)
        return adapter.model_catalog_policy(contract)

    def preserves_anthropic_thinking_contract(self, provider: str, pcfg: dict[str, Any]) -> bool:
        adapter, contract = self._parts(provider, pcfg)
        return adapter.preserves_anthropic_thinking(contract)

    def context_compaction_available(self, provider: str, pcfg: dict[str, Any]) -> bool:
        adapter, contract = self._parts(provider, pcfg)
        return adapter.context_compaction_available(contract)

    def context_policy(self, provider: str, pcfg: dict[str, Any]) -> ProviderContextPolicy:
        adapter, contract = self._parts(provider, pcfg)
        return adapter.context_policy(contract)

    def configuration_policy(self, provider: str, pcfg: dict[str, Any]) -> ProviderConfigurationPolicy:
        adapter, contract = self._parts(provider, pcfg)
        return adapter.configuration_policy(contract)

    def model_panel_badge(self, provider: str, pcfg: dict[str, Any], model: str) -> str:
        adapter, contract = self._parts(provider, pcfg)
        return adapter.model_panel_badge(contract, model)

    def advisor_panel_notice(self, provider: str, pcfg: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
        adapter, contract = self._parts(provider, pcfg)
        return adapter.advisor_panel_notice(contract)

    def advisor_model_badge(self, provider: str, pcfg: dict[str, Any], model: str) -> str:
        adapter, contract = self._parts(provider, pcfg)
        return adapter.advisor_model_badge(contract, model)
