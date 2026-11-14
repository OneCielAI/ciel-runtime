"""Provider HTTP identity, IP-family selection, and connectivity policy."""

from __future__ import annotations

import contextlib
import contextvars
import http.client
import math
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from typing import Any, Callable

from ciel_runtime_support.providers.constants import OPENCODE_PROVIDER_NAMES


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
    if provider in {"codex", "kimi", "openai"}:
        return "ipv4-preferred"
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


class _GatewayNoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward a selected gateway's credentials to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "gateway redirect refused", headers, fp)


# ---- U1: one open bounded as a whole by the request's router-arrival deadline ----
# lead-rulings-independent-review-sse-retry-20261113.md, U1 option 1. Without a
# deadline nothing below is used and provider_urlopen makes its original call.

_OPEN_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "ciel_provider_open_deadline", default=None)


@contextlib.contextmanager
def open_deadline(deadline: float | None):
    """Publish a time.monotonic() deadline for the opens made in this context."""
    token = _OPEN_DEADLINE.set(deadline)
    try:
        yield
    finally:
        _OPEN_DEADLINE.reset(token)


def _left(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("upstream open passed the request's pre-output deadline")
    return left


class _DeadlineConnectionMixin:
    """Bound connect, tunnel/TLS, send and response headers by one deadline.

    DNS resolution remains outside this bound: CPython's ``getaddrinfo`` is a
    blocking call with no stdlib deadline API. Every resolved TCP address gets
    only the time still left. Once the first socket connects, one locked timer
    covers the proxy CONNECT tunnel, TLS handshake, request send and header
    parse. Disarm serializes with the callback, so a returned response can never
    be shut down by the pre-output timer.
    """

    _ciel_deadline: float = math.inf

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._ciel_fired = threading.Event()
        self._ciel_timer: threading.Timer | None = None
        self._ciel_timer_lock = threading.Lock()
        self._ciel_armed = False
        self._ciel_timer_socket = None

        def create_connection(address, timeout=None, source_address=None, **options):
            host, port = address
            errors: list[OSError] = []
            all_errors = bool(options.get("all_errors", False))
            # Match socket.create_connection, but recalculate the timeout for
            # each resolved address instead of giving each the original clip.
            for family, socktype, proto, _, sockaddr in socket.getaddrinfo(
                    host, port, 0, socket.SOCK_STREAM):
                sock = None
                try:
                    sock = socket.socket(family, socktype, proto)
                    left = _left(self._ciel_deadline)
                    if isinstance(timeout, (int, float)):
                        left = min(float(timeout), left)
                    sock.settimeout(left)
                    if source_address:
                        sock.bind(source_address)
                    sock.connect(sockaddr)
                    errors.clear()
                    self._arm(sock)
                    sock.settimeout(_left(self._ciel_deadline))
                    return sock
                except OSError as error:
                    if self._ciel_fired.is_set():
                        if sock is not None:
                            sock.close()
                        raise self._expired(error) from error
                    if not all_errors:
                        errors.clear()
                    errors.append(error)
                    if sock is not None:
                        sock.close()
            if errors:
                error = errors[0]
                errors.clear()
                raise error
            raise OSError("getaddrinfo returns an empty list")

        self._create_connection = create_connection

    def _expired(self, error: BaseException | None = None) -> TimeoutError:
        timeout = TimeoutError("upstream open passed the request's pre-output deadline")
        timeout.__cause__ = error
        return timeout

    def _arm(self, sock: Any) -> None:
        def expire() -> None:
            with self._ciel_timer_lock:
                if not self._ciel_armed:
                    return
                self._ciel_armed = False
                self._ciel_fired.set()
                target = self._ciel_timer_socket
                if target is not None:
                    try:
                        target.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        with self._ciel_timer_lock:
            self._ciel_timer_socket = sock
            self._ciel_armed = True
            timer = threading.Timer(max(0.0, self._ciel_deadline - time.monotonic()), expire)
            timer.daemon = True
            self._ciel_timer = timer
            timer.start()

    def _set_timer_socket(self, sock: Any) -> None:
        with self._ciel_timer_lock:
            if self._ciel_armed:
                self._ciel_timer_socket = sock

    def _disarm(self) -> bool:
        with self._ciel_timer_lock:
            expired = self._ciel_fired.is_set()
            self._ciel_armed = False
            self._ciel_timer_socket = None
            timer, self._ciel_timer = self._ciel_timer, None
        if timer is not None:
            timer.cancel()
        return expired

    def connect(self):
        try:
            left = _left(self._ciel_deadline)
            self.timeout = min(float(self.timeout), left) if isinstance(self.timeout, (int, float)) else left
            super().connect()
            self._set_timer_socket(self.sock)  # HTTPS has replaced TCP with its SSL socket
            self.sock.settimeout(_left(self._ciel_deadline))
        except (OSError, http.client.HTTPException) as error:
            expired = self._disarm()
            if expired or time.monotonic() >= self._ciel_deadline:
                raise self._expired(error) from error
            raise

    def send(self, data):
        try:
            return super().send(data)
        except OSError as error:
            if self._ciel_fired.is_set():
                raise self._expired(error) from error
            raise

    def getresponse(self):
        if self.sock is not None:
            try:
                self.sock.settimeout(_left(self._ciel_deadline))
            except TimeoutError as error:
                self._disarm()
                raise self._expired(error) from error
        try:
            response = super().getresponse()
        except (OSError, http.client.HTTPException) as error:
            expired = self._disarm()
            if expired or time.monotonic() >= self._ciel_deadline:
                raise self._expired(error) from error
            raise
        expired = self._disarm()  # serialized: callback cannot touch a returned response
        if expired:
            response.close()
            raise self._expired()
        return response

    def close(self):
        self._disarm()
        super().close()


class _DeadlineHTTPConnection(_DeadlineConnectionMixin, http.client.HTTPConnection):
    pass


class _DeadlineHTTPSConnection(_DeadlineConnectionMixin, http.client.HTTPSConnection):
    pass


def _deadline_class(base, deadline):
    return type(base.__name__, (base,), {"_ciel_deadline": deadline})


class _DeadlineHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, deadline: float):
        super().__init__()
        self._deadline = deadline

    def http_open(self, req):
        return self.do_open(_deadline_class(_DeadlineHTTPConnection, self._deadline), req)


class _DeadlineHTTPSHandler(urllib.request.HTTPSHandler):
    """Same default SSL context and hostname check as urlopen's HTTPSHandler."""

    def __init__(self, deadline: float):
        super().__init__()
        self._deadline = deadline

    def https_open(self, req):
        return self.do_open(_deadline_class(_DeadlineHTTPSConnection, self._deadline), req,
                            context=self._context)


def _request_deadline(provider: str | None, config: dict[str, Any] | None) -> float | None:
    explicit = _OPEN_DEADLINE.get()
    if explicit is not None:
        return explicit
    if provider != "cielairouter":
        return None
    from ciel_runtime_support.transient_retry import open_deadline as retry_open_deadline

    return retry_open_deadline(config, provider)


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

    gateway_request = provider == "cielairouter" and isinstance(config, dict) and bool(config.get("ciel_gateway_route"))
    deadline = _request_deadline(provider, config)

    def open_request() -> Any:
        with socket_ip_family_policy(policy):
            if deadline is not None:
                # Same handlers urlopen would use, plus the deadline-aware HTTP(S)
                # connections; the gateway keeps its no-redirect handler.
                handlers = [_DeadlineHTTPHandler(deadline), _DeadlineHTTPSHandler(deadline)]
                if gateway_request:
                    handlers.append(_GatewayNoRedirect())
                return urllib.request.build_opener(*handlers).open(request, timeout=min(timeout, _left(deadline))
                                                                   if timeout else _left(deadline))
            if gateway_request:
                return urllib.request.build_opener(_GatewayNoRedirect()).open(request, timeout=timeout)
            return urllib.request.urlopen(request, timeout=timeout)

    if gateway_request:
        # A CielAiRouter request routed to a selected gateway: its failures move
        # later requests to another gateway (cielairouter_gateway_overlay).
        from ciel_runtime_support.cielairouter_gateway_overlay import observed_gateway_open

        return observed_gateway_open(request, config, open_request)
    return open_request()


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
