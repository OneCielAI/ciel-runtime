"""Provider request credentials, headers, model aliases, and routing access."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ciel_runtime_support.architecture import ProviderRequestPolicy


@dataclass(frozen=True, slots=True)
class ProviderRequestAccessPorts:
    request_policy: Callable[
        [str, dict[str, Any]], ProviderRequestPolicy
    ]
    select_api_key: Callable[[str, dict[str, Any]], str | None]
    meaningful_key: Callable[[str], bool]
    adapter_headers: Callable[
        [str, dict[str, Any], str | None], Mapping[str, str]
    ]
    inbound_credentials: Callable[
        [str, Any | None], Mapping[str, str] | None
    ]


@dataclass(frozen=True, slots=True)
class ProviderRequestAccessEffects:
    user_agent_headers: Callable[[dict[str, str]], dict[str, str]]
    ncp_model_id: Callable[[str], str]
    normalize_provider: Callable[[Any], str]


@dataclass(frozen=True, slots=True)
class ProviderRequestAccessService:
    ports: ProviderRequestAccessPorts
    effects: ProviderRequestAccessEffects

    def upstream_model(
        self, provider: str, config: dict[str, Any], model: str
    ) -> str:
        strategy = self.ports.request_policy(
            provider, config
        ).model_alias_strategy
        normalizers = {
            "identity": lambda value: value,
            "ncp": self.effects.ncp_model_id,
        }
        return normalizers[strategy](model)

    def requires_streaming(
        self, provider: str, config: dict[str, Any]
    ) -> bool:
        return self.ports.request_policy(provider, config).stream_required

    @staticmethod
    def key_from_headers(headers: Any) -> str:
        try:
            key = headers.get("x-api-key")
            if key:
                return str(key)
            authorization = str(
                headers.get("authorization")
                or headers.get("Authorization")
                or ""
            )
        except Exception:
            return ""
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return authorization.strip()

    def headers(
        self,
        provider: str,
        config: dict[str, Any],
        inbound_headers: Any | None = None,
    ) -> dict[str, str]:
        headers = self.effects.user_agent_headers(
            {
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            }
        )
        key = (
            self.ports.select_api_key(provider, config)
            or str(config.get("api_key") or "")
            or "not-used"
        )
        policy = self.ports.request_policy(provider, config)
        meaningful = str(key) if self.ports.meaningful_key(str(key)) else None
        if policy.credential_strategy == "anthropic_inbound":
            credential_headers = self.ports.inbound_credentials(
                meaningful or "", inbound_headers
            )
            if credential_headers is None:
                raise RuntimeError(
                    "Anthropic routed mode needs a configured API key "
                    "or inbound Claude Code auth headers."
                )
            headers.update(credential_headers)
        else:
            headers.update(
                self.ports.adapter_headers(
                    provider, config, meaningful
                )
            )
        return headers

    def current_provider(
        self, config: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        provider = self.effects.normalize_provider(
            config.get("current_provider", "nvidia-hosted")
        )
        return provider, config["providers"][provider]
