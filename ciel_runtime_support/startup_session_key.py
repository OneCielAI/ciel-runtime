"""A provider key returned by the remote startup connections, held for one launch.

Remote instructions may answer with dedicated response headers, and the remote
memory manifest may carry one dedicated object.  Nothing is read from the
downloaded markdown or any other file.

Instruction response headers::

    X-Ciel-Session-Api-Key: <key>
    X-Ciel-Session-Api-Key-Provider: <provider>
    X-Ciel-Session-Api-Key-Expires-In: <seconds>   (or -Expires-At: <unix seconds>)
    X-Ciel-Session-Api-Key-Binding: <the X-Ciel-Launch-Binding value this launch sent>
    X-Ciel-Session-Api-Key-Fallback: fail_closed | stored   (optional; default fail_closed)

Memory manifest field (removed from the manifest before it is parsed)::

    "session_api_key": {"provider": "...", "api_key": "...",
                        "expires_in": 3600, "binding": "<launch binding>",
                        "fallback": "fail_closed" | "stored"}

``fallback`` is what an implicit loss of the key does (expiry, restart, an
abandoned launch): ``fail_closed`` blocks the session with an explicit error,
``stored`` lets it use the provider's stored key.

Accepted only during a launch, from the configured origin with authorization
configured, over HTTPS (plain HTTP only to a loopback test server), echoing
this launch's random binding, with a bounded expiry.  The key stays in this
launcher's memory until the launch hands it to its router, which moves it into
the session vault once the launch is bound to its session; it is never written
to the instructions, the memory tree, state files, the environment or argv.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable

from .router_access import is_loopback_address
from .session_api_key_vault import (
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    FALLBACK_FAIL_CLOSED,
    SessionApiKeyError,
    canonical_fallback,
    canonical_provider,
    validated_key,
)

LAUNCH_BINDING_HEADER = "X-Ciel-Launch-Binding"
KEY_HEADER = "X-Ciel-Session-Api-Key"
PROVIDER_HEADER = "X-Ciel-Session-Api-Key-Provider"
EXPIRES_IN_HEADER = "X-Ciel-Session-Api-Key-Expires-In"
EXPIRES_AT_HEADER = "X-Ciel-Session-Api-Key-Expires-At"
BINDING_HEADER = "X-Ciel-Session-Api-Key-Binding"
FALLBACK_HEADER = "X-Ciel-Session-Api-Key-Fallback"
MANIFEST_FIELD = "session_api_key"


class StartupSessionKeyRejected(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _wipe(buffer: bytearray) -> None:
    for index in range(len(buffer)):
        buffer[index] = 0
    buffer.clear()


@dataclass(slots=True)
class StartupSessionKey:
    provider: str
    secret: bytearray = field(repr=False)
    expires_at: float
    source: str
    fallback: str = FALLBACK_FAIL_CLOSED

    def ttl_seconds(self, now: float) -> int:
        return int(self.expires_at - now)

    def wipe(self) -> None:
        _wipe(self.secret)

    def __reduce__(self) -> Any:
        raise TypeError("StartupSessionKey cannot be serialized")


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urllib.parse.urlparse(url)
    port = parsed.port or {"https": 443, "http": 80}.get(parsed.scheme)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), port


def _header(headers: Any, name: str) -> str:
    try:
        value = headers.get(name)
    except Exception:
        return ""
    return str(value or "").strip()


def _expiry(expires_in: Any, expires_at: Any, now: float) -> float:
    if expires_in not in (None, ""):
        try:
            moment = now + int(str(expires_in).strip())
        except ValueError:
            raise StartupSessionKeyRejected("expiry_malformed") from None
    elif expires_at not in (None, ""):
        try:
            moment = float(str(expires_at).strip())
        except ValueError:
            raise StartupSessionKeyRejected("expiry_malformed") from None
    else:
        raise StartupSessionKeyRejected("expiry_missing")
    if moment - now < MIN_TTL_SECONDS:
        raise StartupSessionKeyRejected("expired")
    if moment - now > MAX_TTL_SECONDS:
        raise StartupSessionKeyRejected("expiry_too_far")
    return moment


def _check_transport(configured_url: str, final_url: str, authorized: bool, binding: str, nonce: str) -> None:
    if not nonce or not binding or not hmac.compare_digest(binding, nonce):
        raise StartupSessionKeyRejected("binding_mismatch")
    if not authorized:
        raise StartupSessionKeyRejected("unauthenticated_origin")
    configured, final = _origin(configured_url), _origin(final_url or configured_url)
    if configured != final:
        raise StartupSessionKeyRejected("origin_changed")
    scheme, host, _port = final
    if scheme != "https" and not (scheme == "http" and is_loopback_address(host)):
        raise StartupSessionKeyRejected("not_https")


def _candidate(provider: Any, key: Any, expires_at: float, source: str, fallback: Any = None) -> StartupSessionKey:
    try:
        policy = canonical_fallback(fallback)
        return StartupSessionKey(canonical_provider(provider), validated_key(key), expires_at, source, policy)
    except SessionApiKeyError as exc:
        raise StartupSessionKeyRejected(exc.code) from None


class StartupSessionKeyHolder:
    """This launcher's one pending startup key (process memory only)."""

    def __init__(self, *, clock: Callable[[], float] = time.time,
                 log: Callable[[str, str], None] = lambda _level, _message: None) -> None:
        self._clock, self._log = clock, log
        self._lock = threading.Lock()
        self._nonce = ""
        self._key: StartupSessionKey | None = None
        self._stager: Callable[[str, str, bool], str] | None = None

    def __reduce__(self) -> Any:
        raise TypeError("StartupSessionKeyHolder cannot be serialized")

    def set_log(self, log: Callable[[str, str], None]) -> None:
        self._log = log

    def set_stager(self, stager: Callable[[str, str, bool], str]) -> None:
        """``stager(provider, socket_path, routed)`` hands the key to the router."""

        self._stager = stager

    def stage(self, provider: str, socket_path: str, routed: bool) -> str:
        """Hand the held key, if any, to this launch's router: "none", "staged" or "refused:<why>"."""

        if self._stager is None:
            self.discard("no_router_handoff")
            return "none"
        outcome = self._stager(provider, socket_path, routed)
        if outcome != "none":
            self._log("INFO" if outcome == "staged" else "WARN",
                      f"startup_session_key_handoff provider={provider} outcome={outcome}")
        return outcome

    def begin_launch(self) -> str:
        """A fresh random binding for this launch; any earlier key is dropped."""

        with self._lock:
            if self._key is not None:
                self._key.wipe()
                self._key = None
            self._nonce = secrets.token_urlsafe(24)
            return self._nonce

    def launch_binding(self) -> str:
        with self._lock:
            return self._nonce

    def _offer(self, build: Callable[[str, float], StartupSessionKey], source: str) -> bool:
        with self._lock:
            nonce, now = self._nonce, self._clock()
            try:
                candidate = build(nonce, now)
            except StartupSessionKeyRejected as exc:
                self._log("WARN", f"startup_session_key_rejected source={source} reason={exc.reason}")
                return False
            if self._key is not None:
                candidate.wipe()
                self._log("INFO", f"startup_session_key_ignored source={source} reason=already_held")
                return False
            self._key = candidate
        self._log("INFO", f"startup_session_key_accepted source={source} provider={candidate.provider} "
                          f"expires_in={candidate.ttl_seconds(now)}")
        return True

    def accept_headers(self, headers: Any, *, configured_url: str, final_url: str, authorized: bool) -> bool:
        if not _header(headers, KEY_HEADER):
            return False

        def build(nonce: str, now: float) -> StartupSessionKey:
            _check_transport(configured_url, final_url, authorized, _header(headers, BINDING_HEADER), nonce)
            moment = _expiry(_header(headers, EXPIRES_IN_HEADER), _header(headers, EXPIRES_AT_HEADER), now)
            return _candidate(_header(headers, PROVIDER_HEADER), _header(headers, KEY_HEADER), moment,
                              "remote-instructions", _header(headers, FALLBACK_HEADER) or None)

        return self._offer(build, "remote-instructions")

    def accept_manifest_field(self, value: Any, *, configured_url: str, final_url: str, authorized: bool) -> bool:
        def build(nonce: str, now: float) -> StartupSessionKey:
            if not isinstance(value, dict):
                raise StartupSessionKeyRejected("malformed")
            _check_transport(configured_url, final_url, authorized, str(value.get("binding") or ""), nonce)
            moment = _expiry(value.get("expires_in"), value.get("expires_at"), now)
            return _candidate(value.get("provider"), value.get("api_key"), moment, "remote-memory",
                              value.get("fallback"))

        return self._offer(build, "remote-memory")

    def available_for(self, provider: str) -> bool:
        with self._lock:
            return bool(self._key is not None and self._key.provider == provider
                        and self._key.expires_at - self._clock() >= MIN_TTL_SECONDS)

    def take(self) -> StartupSessionKey | None:
        """The held key, once; the caller wipes it after use."""

        with self._lock:
            key, self._key, self._nonce = self._key, None, ""
            return key

    def discard(self, reason: str) -> None:
        with self._lock:
            key, self._key, self._nonce = self._key, None, ""
        if key is not None:
            provider = key.provider
            key.wipe()
            self._log("INFO", f"startup_session_key_discarded provider={provider} reason={reason}")


STARTUP_SESSION_KEYS = StartupSessionKeyHolder()


def blocker_requires_api_key(line: str) -> bool:
    """The readiness wording for a missing key (provider_readiness_context)."""

    lowered = str(line).lower()
    return "requires" in lowered and "api key" in lowered


def transient_key_satisfies(blockers: list[str], provider: str, *,
                            requires_api_key: Callable[[str], bool] = blocker_requires_api_key,
                            holder: StartupSessionKeyHolder = STARTUP_SESSION_KEYS) -> bool:
    """Whether the only launch blockers are a missing key the held startup key provides."""

    if not blockers or not holder.available_for(provider):
        return False
    return all(requires_api_key(line) for line in blockers)


__all__ = [
    "BINDING_HEADER",
    "EXPIRES_AT_HEADER",
    "EXPIRES_IN_HEADER",
    "FALLBACK_HEADER",
    "KEY_HEADER",
    "LAUNCH_BINDING_HEADER",
    "MANIFEST_FIELD",
    "PROVIDER_HEADER",
    "STARTUP_SESSION_KEYS",
    "StartupSessionKey",
    "StartupSessionKeyHolder",
    "StartupSessionKeyRejected",
    "transient_key_satisfies",
]
