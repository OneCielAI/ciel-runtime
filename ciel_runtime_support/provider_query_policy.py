"""Provider-aware upstream query-string projection policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse


ProviderConfig = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProviderQueryPolicy:
    normalize_provider: Callable[[str], str]
    propagates_inbound_beta: Callable[[str, ProviderConfig], bool]

    @staticmethod
    def inbound_has_beta(request_path: str) -> bool:
        query = urlparse(request_path).query
        return any(
            value.strip().lower() in {"true", "1"}
            for value in parse_qs(query).get("beta", ())
        )

    def upstream_query(
        self,
        config: ProviderConfig,
        request_path: str,
        provider: str | None = None,
    ) -> str:
        forced = self._forced_query(config)
        if forced:
            return forced
        provider_name = str(
            provider or config.get("provider") or ""
        ).strip()
        if not provider_name:
            return ""
        provider_key = self.normalize_provider(provider_name)
        if (
            self.propagates_inbound_beta(provider_key, config)
            and self.inbound_has_beta(request_path)
        ):
            return "beta=true"
        return ""

    def status(self, provider: str, config: ProviderConfig) -> str:
        forced = self._forced_query(config)
        if forced:
            return forced
        provider_key = self.normalize_provider(str(provider))
        if self.propagates_inbound_beta(provider_key, config):
            return "auto (beta=true when routed)"
        return "empty"

    @staticmethod
    def _forced_query(config: ProviderConfig) -> str:
        return (
            str(config.get("force_query_string") or "")
            .strip()
            .lstrip("?")
            .strip()
        )


__all__ = ["ProviderQueryPolicy"]
