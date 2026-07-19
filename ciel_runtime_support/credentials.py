"""Credential resolution ports for API keys, OAuth pass-through, and future stores."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Protocol

from ciel_runtime_support.architecture import ProviderConfig


API_KEY_CLEAR_TOKENS = frozenset({"clear", "unset", "none", "null", "off", "delete", "remove"})


def meaningful_key_value(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and text not in {"dummy", "not-used", "ollama"})


def api_key_clear_requested(value: Any) -> bool:
    return str(value or "").strip().lower() in API_KEY_CLEAR_TOKENS


def parse_api_key_list(value: Any) -> list[str]:
    """Parse pasted, delimited, and soft-wrapped key input deterministically."""

    if value is None:
        return []
    raw_items: list[Any]
    if isinstance(value, (list, tuple, set)):
        raw_items = [key for item in value for key in parse_api_key_list(item)]
    else:
        text = str(value)
        if re.search(r"[,;]", text):
            raw_items = []
            for field in re.split(r"[,;]+", text):
                current = ""
                for line in str(field).splitlines() or [str(field)]:
                    if not line.strip():
                        continue
                    if current and line[:1].isspace():
                        current += line.strip()
                    else:
                        if current:
                            raw_items.append(current)
                        current = line.strip()
                if current:
                    raw_items.append(current)
        else:
            raw_items = re.split(r"[\r\n]+", text)
    return list(
        dict.fromkeys(
            key
            for item in raw_items
            if meaningful_key_value(key := str(item or "").strip())
        )
    )


def provider_config_api_keys(
    pcfg: Mapping[str, Any],
    supplemental_keys: Any = (),
) -> list[str]:
    keys = [
        *parse_api_key_list(supplemental_keys),
        *parse_api_key_list(pcfg.get("api_keys")),
        *parse_api_key_list(pcfg.get("api_key")),
    ]
    return list(dict.fromkeys(keys))


def provider_contract_config(
    provider: str,
    pcfg: Mapping[str, Any],
    api_keys: list[str] | tuple[str, ...],
) -> ProviderConfig:
    return ProviderConfig(
        name=provider,
        base_url=str(pcfg.get("base_url") or ""),
        model=str(pcfg.get("current_model") or pcfg.get("model") or ""),
        api_keys=tuple(api_keys),
        options=pcfg,
    )


@dataclass(frozen=True, slots=True)
class CredentialContext:
    provider: str
    api_key: str = ""
    inbound_headers: Any | None = None


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    source: str
    headers: Mapping[str, str]


class CredentialSource(Protocol):
    def resolve(self, context: CredentialContext) -> ResolvedCredential | None: ...


@dataclass(frozen=True, slots=True)
class ApiKeyCredentialSource:
    header: str = "x-api-key"

    def resolve(self, context: CredentialContext) -> ResolvedCredential | None:
        key = str(context.api_key or "").strip()
        return ResolvedCredential("api_key", {self.header: key}) if key else None


@dataclass(frozen=True, slots=True)
class InboundHeaderCredentialSource:
    """Pass through only explicitly allowed auth and protocol headers."""

    allowed_headers: tuple[str, ...]
    required_any: tuple[str, ...] = ("authorization", "x-api-key")

    def resolve(self, context: CredentialContext) -> ResolvedCredential | None:
        inbound = context.inbound_headers
        if inbound is None:
            return None
        headers: dict[str, str] = {}
        for name in self.allowed_headers:
            try:
                value = inbound.get(name)
            except Exception:
                return None
            if value:
                headers[name] = str(value)
        if not any(headers.get(name) for name in self.required_any):
            return None
        return ResolvedCredential("inbound", headers)


class CredentialChain:
    """Chain of Responsibility: first available credential wins."""

    def __init__(self, *sources: CredentialSource) -> None:
        self._sources = sources

    def resolve(self, context: CredentialContext) -> ResolvedCredential | None:
        for source in self._sources:
            if credential := source.resolve(context):
                return credential
        return None


ANTHROPIC_INBOUND_CREDENTIAL_SOURCE = InboundHeaderCredentialSource(
    allowed_headers=(
        "authorization",
        "x-api-key",
        "anthropic-version",
        "anthropic-beta",
        "anthropic-dangerous-direct-browser-access",
    )
)


def resolve_anthropic_credentials(
    api_key: str,
    inbound_headers: Any | None,
) -> ResolvedCredential | None:
    return CredentialChain(
        ApiKeyCredentialSource(),
        ANTHROPIC_INBOUND_CREDENTIAL_SOURCE,
    ).resolve(CredentialContext("anthropic", api_key, inbound_headers))
