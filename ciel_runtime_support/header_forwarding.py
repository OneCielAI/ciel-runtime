"""Transparent end-to-end request header forwarding policies."""

from __future__ import annotations

from typing import Any


HOP_BY_HOP_REQUEST_HEADERS = frozenset(
    {
        "accept-encoding",
        "connection",
        "content-length",
        "expect",
        "forwarded",
        "host",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "via",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
    }
)
CONFIGURED_PROVIDER_CREDENTIAL_HEADERS = frozenset(
    {"authorization", "cookie", "set-cookie", "x-api-key"}
)


def project_end_to_end_request_headers(
    inbound_headers: Any | None,
    *,
    replace_credentials: bool,
    transport_headers: frozenset[str] = HOP_BY_HOP_REQUEST_HEADERS,
) -> dict[str, str]:
    """Copy client headers verbatim except transport-owned connection headers.

    Header names and values are not interpreted or reconstructed. Configured
    provider routes additionally remove client credentials before the provider
    adapter installs the selected provider credential.
    """

    if inbound_headers is None:
        return {}
    try:
        items = inbound_headers.items()
    except Exception:
        return {}
    excluded = transport_headers
    if replace_credentials:
        excluded = excluded | CONFIGURED_PROVIDER_CREDENTIAL_HEADERS
    forwarded: dict[str, str] = {}
    for raw_name, raw_value in items:
        original_name = str(raw_name)
        normalized_name = original_name.casefold()
        if (
            normalized_name not in excluded
            and not normalized_name.startswith("x-ciel-runtime-")
            and raw_value is not None
        ):
            forwarded[original_name] = str(raw_value)
    return forwarded


__all__ = [
    "CONFIGURED_PROVIDER_CREDENTIAL_HEADERS",
    "HOP_BY_HOP_REQUEST_HEADERS",
    "project_end_to_end_request_headers",
]
