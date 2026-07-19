"""Provider HTTP identity, IP-family selection, and connectivity policy."""

from __future__ import annotations

import contextlib
import os
import socket
import threading
import urllib.parse
import urllib.request
from collections.abc import Iterable
from typing import Any, Callable


DEFAULT_UPSTREAM_USER_AGENT = "claude-cli"
IP_FAMILY_ALIASES = {
    "": "auto",
    "auto": "auto",
    "default": "auto",
    "system": "auto",
    "any": "auto",
    "4": "ipv4",
    "v4": "ipv4",
    "ipv4": "ipv4",
    "inet": "ipv4",
    "6": "ipv6",
    "v6": "ipv6",
    "ipv6": "ipv6",
    "inet6": "ipv6",
    "prefer4": "ipv4-preferred",
    "prefer-v4": "ipv4-preferred",
    "preferred4": "ipv4-preferred",
    "ipv4-preferred": "ipv4-preferred",
    "ipv4_preferred": "ipv4-preferred",
    "prefer-ipv4": "ipv4-preferred",
    "prefer6": "ipv6-preferred",
    "prefer-v6": "ipv6-preferred",
    "preferred6": "ipv6-preferred",
    "ipv6-preferred": "ipv6-preferred",
    "ipv6_preferred": "ipv6-preferred",
    "prefer-ipv6": "ipv6-preferred",
}
IP_FAMILY_CHOICES = ("auto", "ipv4", "ipv6", "ipv4-preferred", "ipv6-preferred")
OPENCODE_PROVIDER_NAMES = ("opencode", "opencode-go")

_IP_FAMILY_LOCK = threading.Lock()


def upstream_user_agent(environ: dict[str, str] | None = None) -> str:
    values = os.environ if environ is None else environ
    configured = str(values.get("CIEL_RUNTIME_UPSTREAM_USER_AGENT") or "").strip()
    return configured or DEFAULT_UPSTREAM_USER_AGENT


def with_upstream_user_agent(headers: dict[str, str] | None = None) -> dict[str, str]:
    result = dict(headers or {})
    if not any(str(key).lower() == "user-agent" for key in result):
        result["user-agent"] = upstream_user_agent()
    return result


def normalize_ip_family(value: Any, default: str = "auto") -> str:
    text = str(value if value is not None else default).strip().lower().replace("_", "-")
    if family := IP_FAMILY_ALIASES.get(text):
        return family
    raise SystemExit(f"ip_family must be one of: {', '.join(IP_FAMILY_CHOICES)}")


def default_provider_ip_family(provider: str) -> str:
    return "ipv6-preferred" if provider in OPENCODE_PROVIDER_NAMES else "auto"


def provider_ip_family(provider: str | None, config: dict[str, Any] | None) -> str:
    if not provider or not isinstance(config, dict):
        return "auto"
    return normalize_ip_family(config.get("ip_family"), default_provider_ip_family(provider))


@contextlib.contextmanager
def socket_ip_family_policy(ip_family: str) -> Iterable[None]:
    policy = normalize_ip_family(ip_family)
    if policy == "auto":
        yield
        return
    strict = policy in ("ipv4", "ipv6")
    desired = socket.AF_INET6 if policy in ("ipv6", "ipv6-preferred") else socket.AF_INET
    with _IP_FAMILY_LOCK:
        original_getaddrinfo = socket.getaddrinfo

        def filtered_getaddrinfo(
            host: Any,
            port: Any,
            family: int = 0,
            type: int = 0,
            proto: int = 0,
            flags: int = 0,
        ) -> Any:
            infos = original_getaddrinfo(host, port, family, type, proto, flags)
            matches = [info for info in infos if info and info[0] == desired]
            if strict:
                if not matches:
                    raise socket.gaierror(socket.EAI_NONAME, f"no {policy} address for {host}")
                return matches
            others = [info for info in infos if not info or info[0] != desired]
            return sorted(matches, key=lambda info: (0 if info[0] == desired else 1, info[0])) + others

        socket.getaddrinfo = filtered_getaddrinfo
        try:
            yield
        finally:
            socket.getaddrinfo = original_getaddrinfo


def provider_urlopen(
    request: urllib.request.Request,
    timeout: float,
    provider: str | None,
    config: dict[str, Any] | None,
    log: Callable[[str, str], None],
) -> Any:
    policy = provider_ip_family(provider, config)
    if policy != "auto":
        log("DEBUG", f"upstream_ip_family provider={provider or ''} policy={policy}")
    with socket_ip_family_policy(policy):
        return urllib.request.urlopen(request, timeout=timeout)


def ip_family_connectivity(
    host: str,
    port: int,
    family: int,
    timeout: float = 1.5,
) -> tuple[bool, str]:
    try:
        infos = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    except Exception as error:
        return False, f"dns:{type(error).__name__}"
    if not infos:
        return False, "dns:no-address"
    last_error = ""
    for address_family, socktype, protocol, _canonical_name, address in infos:
        connection = socket.socket(address_family, socktype, protocol)
        connection.settimeout(timeout)
        try:
            connection.connect(address)
            return True, "connect:ok"
        except Exception as error:
            last_error = f"connect:{type(error).__name__}"
        finally:
            try:
                connection.close()
            except Exception:
                pass
    return False, last_error or "connect:failed"


def provider_ip_family_probe_lines(
    provider: str,
    config: dict[str, Any],
    default_base_url: Callable[[str], str | None],
) -> list[str]:
    if provider not in OPENCODE_PROVIDER_NAMES:
        return []
    base = str(config.get("base_url") or default_base_url(provider) or "").strip()
    parsed = urllib.parse.urlparse(base)
    if not parsed.hostname:
        return [f"IP family: {provider_ip_family(provider, config)}; probe unavailable (base URL host missing)"]
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    ipv4_ok, ipv4_reason = ip_family_connectivity(parsed.hostname, port, socket.AF_INET)
    ipv6_ok, ipv6_reason = ip_family_connectivity(parsed.hostname, port, socket.AF_INET6)
    return [
        f"IP family: {provider_ip_family(provider, config)}",
        f"IPv4 connectivity: {'OK' if ipv4_ok else 'FAIL'} ({ipv4_reason})",
        f"IPv6 connectivity: {'OK' if ipv6_ok else 'FAIL'} ({ipv6_reason})",
    ]
