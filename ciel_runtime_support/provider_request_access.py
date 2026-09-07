"""Provider request credentials, headers, model aliases, and routing access."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ciel_runtime_support.architecture import MessageProtocol, ProviderRequestPolicy
from ciel_runtime_support.header_forwarding import (
    CONFIGURED_PROVIDER_CREDENTIAL_HEADERS,
    HOP_BY_HOP_REQUEST_HEADERS,
    project_end_to_end_request_headers,
)
from ciel_runtime_support.remote_bridge import (
    REMOTE_BRIDGE_CONFIG_MARKER,
    REQUEST_API_KEY_MARKER,
)


_HOST_CREDENTIAL_SCOPE_HEADERS = frozenset(
    {"openai-organization", "openai-project"}
)
_CONFIGURED_PROTOCOL_HEADER_EXCLUSIONS = (
    CONFIGURED_PROVIDER_CREDENTIAL_HEADERS | HOP_BY_HOP_REQUEST_HEADERS
)


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

    @staticmethod
    def _configured_protocol_headers(
        config: dict[str, Any], protocol: MessageProtocol | None
    ) -> dict[str, str]:
        """Project safe, protocol-specific provider headers from configuration.

        Credentials and connection-owned headers stay under the existing
        adapter/transport policies.  This hook is for provider protocol
        switches such as Alibaba's Responses session cache header.
        """

        if protocol is None:
            return {}
        configured = config.get("protocol_headers")
        if not isinstance(configured, Mapping):
            return {}
        selected = configured.get(protocol)
        if not isinstance(selected, Mapping):
            return {}
        projected: dict[str, str] = {}
        for raw_name, raw_value in selected.items():
            name = str(raw_name).strip()
            folded = name.casefold()
            if (
                not name
                or raw_value is None
                or folded in _CONFIGURED_PROTOCOL_HEADER_EXCLUSIONS
                or folded.startswith("x-ciel-runtime-")
            ):
                continue
            projected[name] = str(raw_value)
        return projected

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
        protocol: MessageProtocol | None = None,
        preserve_inbound: bool = False,
    ) -> dict[str, str]:
        policy = self.ports.request_policy(provider, config)
        passthrough = (
            (protocol is not None or preserve_inbound)
            and inbound_headers is not None
        )
        if passthrough:
            projected_headers = project_end_to_end_request_headers(
                inbound_headers,
                replace_credentials=True,
            )
            if (
                config.get(REMOTE_BRIDGE_CONFIG_MARKER) is True
                and config.get(REQUEST_API_KEY_MARKER) is not True
            ):
                projected_headers = {
                    name: value
                    for name, value in projected_headers.items()
                    if str(name).casefold() not in _HOST_CREDENTIAL_SCOPE_HEADERS
                }
            headers = self.effects.user_agent_headers(projected_headers)
        else:
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
        configured_protocol_headers = self._configured_protocol_headers(
            config, protocol
        )
        if configured_protocol_headers:
            configured_names = {
                name.casefold() for name in configured_protocol_headers
            }
            headers = {
                name: value
                for name, value in headers.items()
                if name.casefold() not in configured_names
            }
            headers.update(configured_protocol_headers)
        if protocol == "anthropic_messages":
            normalized_names = {
                str(name).casefold() for name in headers
            }
            if "content-type" not in normalized_names:
                headers["content-type"] = "application/json"
            if "anthropic-version" not in normalized_names:
                headers["anthropic-version"] = "2023-06-01"
        return headers

    def current_provider(
        self, config: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        provider = self.effects.normalize_provider(
            config.get("current_provider", "nvidia-hosted")
        )
        return provider, config["providers"][provider]
