"""Router-process memory for API keys scoped to one session and one provider.

A key held here is used only for requests whose canonical session identity
(``claude:<id>`` or ``codex:<id>``) and serving provider both match exactly.
It never enters the configuration, the environment, a file, a log or a
response: the vault cannot be serialized, ``repr`` shows counts only, and
callers receive metadata (source, provider, session, version, times and a
per-process HMAC fingerprint).  Entries expire after a bounded TTL; clearing,
expiry and process exit overwrite the stored bytes before dropping them.

Every change carries a version so a stale caller cannot replace or clear a
newer key, and an idempotency key so a retried request is answered from the
first result instead of being applied twice.

A launch may also stage one key for the session it is about to start.  The
staged key is bound to that launch's epoch and random messaging socket and
moves into the vault only when the router has bound that exact launch to its
session through Claude Code's own published session record.
"""

from __future__ import annotations

import atexit
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import Any, Callable

IDENTITY_PATTERN = re.compile(r"^(claude|codex):[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
PROVIDER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SOURCES = frozenset({"ctl", "rest", "dashboard", "menu", "remote-instructions", "remote-memory"})
MIN_TTL_SECONDS = 60
# Every key has a TTL; a direct inject without one gets the longest, so a
# forgotten key still ends (and then blocks as expired, under fail_closed).
DEFAULT_TTL_SECONDS = 24 * 3600
MAX_TTL_SECONDS = 24 * 3600
MAX_KEY_LENGTH = 4096
MIN_KEY_LENGTH = 8
MAX_ENTRIES = 256
MAX_HANDOFFS = 32
MAX_USED_BINDINGS = 1024
MAX_ABANDONED = 32
# Live keys plus recorded losses: a new key is refused at this bound, so no
# loss is ever pruned (session_api_key_markers.MAX_ROWS).
MAX_STATE_ROWS = 512
FALLBACK_FAIL_CLOSED = "fail_closed"
FALLBACK_STORED = "stored"
FALLBACKS = frozenset({FALLBACK_FAIL_CLOSED, FALLBACK_STORED})
# Why a session key is gone without an operator clear (the explicit error names it).
LOSS_CAUSES = {"expired": "expired", "restart": "was lost on router restart",
               "abandoned": "was abandoned (scope rebound by another launch)",
               "state_unreadable": "state is unreadable; an operator must reset it"}
IDEMPOTENCY_SECONDS = 900
MAX_IDEMPOTENCY = 512
COOLDOWN_MAX_SECONDS = 90_000.0


class SessionApiKeyError(ValueError):
    """A rejected vault operation; ``code`` is stable for REST and CLI callers."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def canonical_identity(value: Any) -> str:
    text = str(value or "").strip()
    if not IDENTITY_PATTERN.match(text):
        raise SessionApiKeyError("invalid_session", "session must be claude:<id> or codex:<id>")
    return text


def canonical_provider(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not PROVIDER_PATTERN.match(text):
        raise SessionApiKeyError("invalid_provider", "provider name is invalid")
    return text


def validated_key(value: Any) -> bytearray:
    if not isinstance(value, (str, bytes, bytearray)):
        raise SessionApiKeyError("invalid_key", "api key must be text")
    raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    raw = raw.strip()
    if not MIN_KEY_LENGTH <= len(raw) <= MAX_KEY_LENGTH:
        raise SessionApiKeyError("invalid_key", "api key length is out of range")
    if any(byte < 0x21 or byte > 0x7E for byte in raw):
        raise SessionApiKeyError("invalid_key", "api key must be printable ASCII without spaces")
    return bytearray(raw)


def bounded_ttl(value: Any, *, default: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        raise SessionApiKeyError("invalid_ttl", "ttl must be whole seconds") from None
    if not MIN_TTL_SECONDS <= seconds <= maximum:
        raise SessionApiKeyError("invalid_ttl", f"ttl must be {MIN_TTL_SECONDS}-{maximum} seconds")
    return seconds


def launcher_state(pid: int, identity: str) -> str:
    """``alive``, ``ended`` (exited, or the pid now names another process), or ``unknown``.

    A session's end is proven by its launcher's pid *and* creation token
    (workspace_router_selection._process_identity), never by the pid alone:
    a reused pid must neither keep a row alive forever nor end a live one.
    An inspection failure is ``unknown`` and ends nothing.
    """

    if int(pid or 0) <= 0 or not identity:
        return "unknown"
    from .workspace_router_selection import _process_identity

    current = _process_identity(int(pid))
    if current is None:
        return "unknown"
    return "alive" if current == identity else "ended"


def binding_digest(socket_path: str, epoch: str) -> str:
    """The non-secret launch binding as stored: never the raw socket path."""

    return hashlib.sha256(f"{epoch}\0{socket_path}".encode("utf-8")).hexdigest()


def _wipe(buffer: bytearray) -> None:
    for index in range(len(buffer)):
        buffer[index] = 0
    buffer.clear()


@dataclass(frozen=True, slots=True)
class SessionApiKeyMetadata:
    session: str
    provider: str
    source: str
    version: int
    injected_at: float
    expires_at: float
    fingerprint: str
    fallback: str = "fail_closed"
    state: str = "active"
    # How the session's end is proven: "launch" (its launcher), "session_record"
    # (Claude Code's own record), or "none" (clear manually).
    end_proof: str = "none"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonical_fallback(value: Any) -> str:
    """What implicit loss of the key does: fail closed (default) or use the stored key."""

    text = str(value or FALLBACK_FAIL_CLOSED).strip().lower()
    if text not in FALLBACKS:
        raise SessionApiKeyError("invalid_fallback", "fallback must be fail_closed or stored")
    return text


class _Entry:
    __slots__ = ("session", "provider", "secret", "source", "version", "injected_at", "expires_at",
                 "fingerprint", "cooldown_until", "fallback", "launcher_pid", "launcher_identity", "end_proof")

    def __init__(self, session: str, provider: str, secret: bytearray, source: str, version: int,
                 injected_at: float, expires_at: float, fingerprint: str,
                 fallback: str = "fail_closed") -> None:
        self.session, self.provider, self.secret = session, provider, secret
        self.source, self.version = source, version
        self.injected_at, self.expires_at, self.fingerprint = injected_at, expires_at, fingerprint
        self.cooldown_until = 0.0
        self.fallback = fallback
        # The launcher that bound this session (launch handoff only): its exit
        # ends the session, and with it the restart marker.
        self.launcher_pid = 0
        self.launcher_identity = ""
        self.end_proof = "none"

    def own(self, pid: int, identity: str, proof: str) -> None:
        """Tie this key's life to the process that proves its session's end."""

        if int(pid or 0) > 0 and identity:
            self.launcher_pid, self.launcher_identity, self.end_proof = int(pid), str(identity), proof

    def metadata(self) -> SessionApiKeyMetadata:
        return SessionApiKeyMetadata(self.session, self.provider, self.source, self.version,
                                     self.injected_at, self.expires_at, self.fingerprint, self.fallback,
                                     end_proof=self.end_proof)

    def __repr__(self) -> str:
        return f"_Entry(session={self.session!r}, provider={self.provider!r}, version={self.version})"


class _Handoff:
    __slots__ = ("binding", "provider", "secret", "source", "ttl_seconds", "launcher_pid", "expires_at",
                 "fingerprint", "staged_at", "scope", "fallback", "launcher_identity")

    def __init__(self, binding: str, provider: str, secret: bytearray, source: str, ttl_seconds: int,
                 launcher_pid: int, expires_at: float, fingerprint: str = "", staged_at: float = 0.0) -> None:
        self.binding, self.provider, self.secret, self.source = binding, provider, secret, source
        self.ttl_seconds, self.launcher_pid, self.expires_at = ttl_seconds, launcher_pid, expires_at
        self.fingerprint, self.staged_at = fingerprint, staged_at
        self.scope: dict[str, Any] = {}
        self.fallback = FALLBACK_FAIL_CLOSED
        self.launcher_identity = ""

    def status(self) -> dict[str, Any]:
        """Metadata only: the launch's key is staged and waits for its session's first request."""

        return {"state": "staged_awaiting_first_request", "provider": self.provider, "source": self.source,
                "launcher_pid": self.launcher_pid, "staged_at": self.staged_at, "expires_at": self.expires_at,
                "fingerprint": self.fingerprint}

    def __repr__(self) -> str:
        return f"_Handoff(provider={self.provider!r})"


class SessionApiKeyVault:
    """Lock-protected, TTL-bounded, memory-only session keys (see module doc)."""

    def __init__(self, *, clock: Callable[[], float] = time.time, salt: bytes | None = None,
                 log: Callable[[str, str], None] | None = None) -> None:
        self._clock = clock
        self._salt = salt or secrets.token_bytes(32)
        self._log = log or (lambda _level, _message: None)
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], _Entry] = {}
        self._handoffs: dict[str, _Handoff] = {}
        # Bindings ever staged (first writer wins) and staged keys a rebound
        # scope abandoned: metadata plus the non-secret scope, no key.
        self._used_bindings: OrderedDict[str, float] = OrderedDict()
        self._abandoned: OrderedDict[str, dict[str, Any]] = OrderedDict()
        # Keys gone without an operator clear (expired, lost on restart):
        # metadata only.  A fail_closed loss blocks the session and provider
        # until an operator clears it or injects again.
        self._losses: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
        # session_api_key_markers.SessionKeyMarkerStore once the router attaches it.
        self._markers: Any | None = None
        # True while the marker file could not be read: every session without
        # a live key is blocked until an operator resets the state.
        self._markers_unreadable = False
        self._last_sweep = float("-inf")
        self._idempotency: OrderedDict[str, tuple[str, float, dict[str, Any]]] = OrderedDict()
        # () -> the epoch of the launch that holds this router's workspace
        # scope now ("" when unknown); set by the router (set_scope_probe).
        self._scope_probe: Callable[[], str] | None = None
        self._version = 0

    # -- never serialized ------------------------------------------------------
    def __reduce__(self) -> Any:
        raise TypeError("SessionApiKeyVault cannot be serialized")

    def __reduce_ex__(self, _protocol: Any) -> Any:
        raise TypeError("SessionApiKeyVault cannot be serialized")

    def __getstate__(self) -> Any:
        raise TypeError("SessionApiKeyVault cannot be serialized")

    def __repr__(self) -> str:
        return f"SessionApiKeyVault(entries={len(self._entries)}, handoffs={len(self._handoffs)})"

    def set_log(self, log: Callable[[str, str], None]) -> None:
        self._log = log

    # -- restart markers (router process only) ---------------------------------
    def attach_markers(self, store: Any, *,
                       launcher_state: Callable[[int, str], str] = launcher_state) -> int:
        """Restore what an earlier router process lost, then keep ``store`` current.

        A key that was live when that process ended is lost on restart; one
        that had expired by now counts as expired; a recorded loss stays.  A
        row ends only when the launcher that bound that exact session has
        exited (session end) - never by age.  An unreadable file blocks every
        session without a live key until ``reset_marker_state``; it is not
        overwritten meanwhile.  Returns the number of losses restored.
        """

        from .session_api_key_markers import UNREADABLE

        restored = 0
        status, rows = store.load()
        with self._lock:
            now = self._clock()
            self._markers = store
            if status == UNREADABLE:
                self._markers_unreadable = True
                self._log("WARN", "session_api_key_markers_blocking reason=state_unreadable")
                return 0
            for row in rows:
                try:
                    session, provider = canonical_identity(row.get("session")), canonical_provider(row.get("provider"))
                    fallback = canonical_fallback(row.get("fallback"))
                except SessionApiKeyError:
                    # A row the vault cannot place may cover any session.
                    self._markers_unreadable = True
                    continue
                pid, owner = int(row.get("launcher_pid") or 0), str(row.get("launcher_identity") or "")
                if launcher_state(pid, owner) == "ended" or (session, provider) in self._entries:
                    continue
                if row.get("state") == "active":
                    cause = "expired" if float(row.get("expires_at") or 0) <= now else "restart"
                else:
                    cause = str(row.get("cause") or "")
                    if cause not in LOSS_CAUSES:
                        self._markers_unreadable = True
                        continue
                self._record_loss(session, provider, cause, fallback, int(row.get("version") or 0),
                                  str(row.get("source") or ""), launcher_pid=pid, launcher_identity=owner,
                                  end_proof=str(row.get("end_proof") or "none"),
                                  injected_at=float(row.get("injected_at") or row.get("lost_at") or now))
                restored += 1
            self._persist_locked()
        if restored:
            self._log("INFO", f"session_api_key_markers_restored losses={restored}")
        return restored

    def reset_marker_state(self, source: str) -> dict[str, Any]:
        """Operator reset after an unreadable marker file: quarantine it and write the current state."""

        with self._lock:
            store, was = self._markers, self._markers_unreadable
            if store is not None and was:
                store.quarantine()
            self._markers_unreadable = False
            self._persist_locked()
        self._log("WARN", f"session_api_key_markers_reset source={source} was_unreadable={int(was)}")
        return {"ok": True, "action": "reset_state", "was_unreadable": was}

    def state_unreadable(self) -> bool:
        with self._lock:
            return self._markers_unreadable

    def health(self) -> dict[str, Any]:
        """Router-wide state for every status surface (no key, no fingerprint of a loss, no binding)."""

        with self._lock:
            self._expire(self._clock())
            rows = len(self._entries) + len(self._losses) + len(self._handoffs)
            return {"state_unreadable": self._markers_unreadable, "rows": rows, "limit": MAX_STATE_ROWS,
                    "markers_attached": self._markers is not None}

    def _capacity_message_locked(self) -> str:
        rows = len(self._entries) + len(self._losses) + len(self._handoffs)
        return (f"session-key records full ({rows} of {MAX_STATE_ROWS}); nothing was pruned. Free rows by "
                "clearing the keys and blocks of sessions that have ended (ciel-runtimectl session-api-key list "
                "then clear, the Session API keys page, REST action clear, or the menu); rows of a launched "
                "session also end on their own when its process exits.")

    def _capacity_locked(self) -> None:
        if len(self._entries) + len(self._losses) >= MAX_STATE_ROWS:
            raise SessionApiKeyError("capacity", self._capacity_message_locked())

    def _marker_rows_locked(self) -> list[dict[str, Any]]:
        rows = [{"session": e.session, "provider": e.provider, "state": "active", "fallback": e.fallback,
                 "version": e.version, "source": e.source, "expires_at": e.expires_at,
                 "launcher_pid": e.launcher_pid, "launcher_identity": e.launcher_identity,
                 "end_proof": e.end_proof, "injected_at": e.injected_at}
                for e in self._entries.values()]
        rows += [{name: loss[name] for name in ("session", "provider", "state", "fallback", "cause", "version",
                                               "source", "lost_at", "launcher_pid", "launcher_identity",
                                               "end_proof", "injected_at")
                  if name in loss}
                 for loss in self._losses.values()]
        return rows

    def _persist_locked(self) -> None:
        store = self._markers
        if store is None or self._markers_unreadable:
            # Never overwrite a file that could not be read: it stays the
            # evidence and keeps the next process failing closed.
            return
        try:
            store.save(self._marker_rows_locked())
        except Exception as exc:  # noqa: BLE001 - a marker failure never breaks a request
            self._log("WARN", f"session_api_key_markers_save_failed error={type(exc).__name__}")

    def fingerprint(self, secret: bytes | bytearray) -> str:
        return hmac.new(self._salt, bytes(secret), hashlib.sha256).hexdigest()[:16]

    # -- internal (lock held) --------------------------------------------------
    def _next_version(self) -> int:
        self._version += 1
        return self._version

    def _drop(self, key: tuple[str, str], reason: str) -> _Entry | None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            _wipe(entry.secret)
            self._log("INFO", f"session_api_key_dropped session={entry.session} provider={entry.provider} "
                              f"version={entry.version} fingerprint={entry.fingerprint} reason={reason}")
            if reason in LOSS_CAUSES:
                self._record_loss(entry.session, entry.provider, reason, entry.fallback, entry.version,
                                  entry.source, launcher_pid=entry.launcher_pid,
                                  launcher_identity=entry.launcher_identity, end_proof=entry.end_proof,
                                  injected_at=entry.injected_at)
            else:
                self._persist_locked()
        return entry

    def _record_loss(self, session: str, provider: str, cause: str, fallback: str, version: int,
                     source: str, *, launcher_pid: int = 0, launcher_identity: str = "",
                     end_proof: str = "none", injected_at: float | None = None) -> None:
        now = self._clock()
        self._losses[(session, provider)] = {
            "session": session, "provider": provider, "source": source, "version": version,
            "fallback": fallback, "cause": cause, "lost_at": now, "launcher_pid": int(launcher_pid),
            "launcher_identity": str(launcher_identity or ""),
            "end_proof": end_proof if launcher_identity else "none",
            "injected_at": float(injected_at if injected_at is not None else now),
            "state": "fell_back_to_stored" if fallback == FALLBACK_STORED else f"blocked_{cause}"}
        self._losses.move_to_end((session, provider))
        self._persist_locked()

    def _expire(self, now: float) -> None:
        for key in [key for key, entry in self._entries.items() if entry.expires_at <= now]:
            self._drop(key, "expired")
        for binding in [b for b, handoff in self._handoffs.items() if handoff.expires_at <= now]:
            handoff = self._handoffs.pop(binding)
            _wipe(handoff.secret)
            self._log("INFO", f"session_api_key_handoff_dropped provider={handoff.provider} reason=expired")
        for binding in [b for b, row in self._abandoned.items() if float(row.get("expires_at") or 0) <= now]:
            self._abandoned.pop(binding, None)
        for token in [t for t, (_d, at, _r) in self._idempotency.items() if at + IDEMPOTENCY_SECONDS <= now]:
            self._idempotency.pop(token, None)

    def _replay(self, idempotency_key: str, request_digest: str) -> dict[str, Any] | None:
        if not idempotency_key:
            raise SessionApiKeyError("idempotency_required", "an Idempotency-Key is required")
        if len(idempotency_key) > 128 or not re.match(r"^[A-Za-z0-9._:-]+$", idempotency_key):
            raise SessionApiKeyError("invalid_idempotency_key", "Idempotency-Key is invalid")
        recorded = self._idempotency.get(idempotency_key)
        if recorded is None:
            return None
        digest, _at, result = recorded
        if not hmac.compare_digest(digest, request_digest):
            raise SessionApiKeyError("idempotency_conflict", "Idempotency-Key was used for another request")
        return {**result, "replayed": True}

    def _remember(self, idempotency_key: str, request_digest: str, result: dict[str, Any], now: float) -> None:
        self._idempotency[idempotency_key] = (request_digest, now, dict(result))
        while len(self._idempotency) > MAX_IDEMPOTENCY:
            self._idempotency.popitem(last=False)

    def _request_digest(self, *parts: Any) -> str:
        return hmac.new(self._salt, "\0".join(str(part) for part in parts).encode("utf-8"),
                        hashlib.sha256).hexdigest()

    # -- public operations -----------------------------------------------------
    def put(self, session: Any, provider: Any, key: Any, *, source: str, expected_version: int,
            idempotency_key: str, ttl_seconds: Any = None, fallback: Any = None,
            owner: tuple[int, str] | None = None) -> dict[str, Any]:
        """Inject (``expected_version`` 0) or replace (the current version) one key.

        ``fallback`` decides what implicit loss (expiry, router restart) does:
        ``fail_closed`` (default) blocks the session and provider with an
        explicit error; ``stored`` returns to the stored key.  Injecting again
        ends an earlier loss.
        """

        session, provider = canonical_identity(session), canonical_provider(provider)
        if source not in SOURCES:
            raise SessionApiKeyError("invalid_source", "source is invalid")
        ttl = bounded_ttl(ttl_seconds, default=DEFAULT_TTL_SECONDS, maximum=MAX_TTL_SECONDS)
        policy = canonical_fallback(fallback)
        try:
            expected = int(expected_version)
        except (TypeError, ValueError):
            raise SessionApiKeyError("invalid_version", "expected version must be a number") from None
        secret = validated_key(key)
        fingerprint = self.fingerprint(secret)
        digest = self._request_digest("put", session, provider, fingerprint, expected, ttl, source, policy)
        with self._lock:
            now = self._clock()
            self._expire(now)
            try:
                replay = self._replay(str(idempotency_key or ""), digest)
            except SessionApiKeyError:
                _wipe(secret)
                raise
            if replay is not None:
                _wipe(secret)
                return replay
            slot = (session, provider)
            current = self._entries.get(slot)
            current_version = current.version if current is not None else 0
            if expected != current_version:
                _wipe(secret)
                raise SessionApiKeyError(
                    "version_conflict",
                    f"expected version {expected} but the current version is {current_version}",
                )
            if current is None and slot not in self._losses:
                try:
                    if len(self._entries) >= MAX_ENTRIES:
                        raise SessionApiKeyError("capacity", self._capacity_message_locked())
                    self._capacity_locked()
                except SessionApiKeyError:
                    _wipe(secret)
                    raise
            if current is not None:
                self._drop(slot, "replaced")
            entry = _Entry(session, provider, secret, source, self._next_version(), now, now + ttl, fingerprint,
                           policy)
            if owner is None and current is not None and current.end_proof != "none":
                owner = (current.launcher_pid, current.launcher_identity)
                entry.own(*owner, current.end_proof)
            elif owner is not None:
                # The session's own process, from Claude Code's published record.
                entry.own(*owner, "session_record")
            self._entries[slot] = entry
            self._losses.pop(slot, None)
            self._persist_locked()
            result = {"ok": True, "action": "replace" if current is not None else "inject",
                      "key": entry.metadata().as_dict()}
            self._remember(str(idempotency_key), digest, result, now)
        self._log("INFO", f"session_api_key_{result['action']} session={session} provider={provider} "
                          f"source={source} version={entry.version} fingerprint={fingerprint} ttl={ttl}")
        return result

    def clear(self, session: Any, provider: Any, *, expected_version: int, idempotency_key: str,
              source: str) -> dict[str, Any]:
        session, provider = canonical_identity(session), canonical_provider(provider)
        try:
            expected = int(expected_version)
        except (TypeError, ValueError):
            raise SessionApiKeyError("invalid_version", "expected version must be a number") from None
        digest = self._request_digest("clear", session, provider, expected, source)
        with self._lock:
            now = self._clock()
            self._expire(now)
            replay = self._replay(str(idempotency_key or ""), digest)
            if replay is not None:
                return replay
            current = self._entries.get((session, provider))
            loss = self._losses.get((session, provider))
            if current is None and loss is None:
                raise SessionApiKeyError("not_found", "no session key for this session and provider")
            current_version = current.version if current is not None else int(loss["version"])
            if expected != current_version:
                raise SessionApiKeyError(
                    "version_conflict",
                    f"expected version {expected} but the current version is {current_version}",
                )
            # An operator clear is the deliberate way back to the stored key.
            if current is not None:
                metadata = current.metadata().as_dict()
                self._drop((session, provider), f"cleared_by_{source}")
            else:
                metadata = {name: loss[name] for name in ("session", "provider", "source", "version", "fallback")}
            self._losses.pop((session, provider), None)
            self._persist_locked()
            metadata["state"] = "cleared_stored_key_in_use"
            result = {"ok": True, "action": "clear", "key": metadata}
            self._remember(str(idempotency_key), digest, result, now)
        return result

    def key_for(self, session: str, provider: str) -> str | None:
        """The live key for exactly this session and provider, else None."""

        with self._lock:
            now = self._clock()
            entry = self._entries.get((session, provider))
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._drop((session, provider), "expired")
                return None
            return entry.secret.decode("ascii")

    def resolve(self, session: str, provider: str) -> tuple[str, str]:
        """``("key", key)``, ``("blocked", cause)``, or ``("stored", "")`` for one session and provider.

        ``blocked`` means a fail_closed key was lost without an operator
        clear; the router answers with an explicit error naming the cause.
        """

        key = self.key_for(session, provider)
        if key:
            return "key", key
        with self._lock:
            loss = self._losses.get((session, provider))
            if loss is not None and loss["fallback"] == FALLBACK_FAIL_CLOSED:
                return "blocked", str(loss["cause"])
            if self._markers_unreadable and loss is None:
                # The unreadable file may have covered this session.
                return "blocked", "state_unreadable"
        return "stored", ""

    def record_loss(self, session: Any, provider: Any, cause: str, *, fallback: Any, version: int,
                    source: str) -> None:
        """Record a loss the vault did not see itself (a restart marker, an abandoned launch key)."""

        if cause not in LOSS_CAUSES:
            raise SessionApiKeyError("invalid_cause", "unknown loss cause")
        with self._lock:
            slot = (canonical_identity(session), canonical_provider(provider))
            if slot in self._entries:
                return
            self._record_loss(slot[0], slot[1], cause, canonical_fallback(fallback), int(version), str(source))

    def losses(self) -> list[dict[str, Any]]:
        """Keys gone without an operator clear, metadata only (expiry applied first)."""

        with self._lock:
            self._expire(self._clock())
            return [dict(row) for row in self._losses.values()]

    def metadata(self, session: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            self._expire(self._clock())
            return [entry.metadata().as_dict() for entry in sorted(
                self._entries.values(), key=lambda item: (item.session, item.provider))
                if session is None or entry.session == session]

    def register_cooldown(self, session: str, provider: str, seconds: float) -> float:
        with self._lock:
            entry = self._entries.get((session, provider))
            if entry is None:
                return 0.0
            entry.cooldown_until = self._clock() + max(1.0, min(float(seconds), COOLDOWN_MAX_SECONDS))
            return entry.cooldown_until

    def cooldown_until(self, session: str, provider: str) -> float:
        with self._lock:
            entry = self._entries.get((session, provider))
            return entry.cooldown_until if entry is not None else 0.0

    # -- launch handoff --------------------------------------------------------
    def stage_handoff(self, binding: str, provider: Any, key: Any, *, source: str, ttl_seconds: Any,
                      launcher_pid: int, scope: dict[str, Any] | None = None,
                      fallback: Any = None, launcher_identity: str = "") -> dict[str, Any]:
        """Hold one launch's key until its session's first request; lives min(key TTL, launcher alive).

        First writer wins: a binding is accepted once for the router's life,
        and any later writer for it - before or after the session claimed it -
        gets the same refusal as a binding that never existed.  ``scope`` is
        the non-secret launch scope (epoch, socket, cwd, start) the binding
        was checked against; it lets a rebound scope abandon the key.
        """

        provider = canonical_provider(provider)
        if source not in {"remote-instructions", "remote-memory"}:
            raise SessionApiKeyError("invalid_source", "only remote startup sources may stage a launch key")
        if not re.match(r"^[0-9a-f]{64}$", str(binding or "")):
            raise SessionApiKeyError("handoff_refused", "the launch handoff was refused")
        ttl = bounded_ttl(ttl_seconds, default=DEFAULT_TTL_SECONDS, maximum=MAX_TTL_SECONDS)
        policy = canonical_fallback(fallback)
        secret = validated_key(key)
        fingerprint = self.fingerprint(secret)
        with self._lock:
            now = self._clock()
            self._expire(now)
            if binding in self._used_bindings:
                _wipe(secret)
                raise SessionApiKeyError("handoff_refused", "the launch handoff was refused")
            if len(self._handoffs) >= MAX_HANDOFFS or (
                    len(self._entries) + len(self._losses) + len(self._handoffs) >= MAX_STATE_ROWS):
                _wipe(secret)
                raise SessionApiKeyError("capacity", self._capacity_message_locked())
            # The key's own expiry bounds the wait; the launcher's exit ends it
            # earlier (sweep_ended).  No separate fixed window.
            handoff = _Handoff(binding, provider, secret, source, ttl, int(launcher_pid), now + ttl,
                               fingerprint, now)
            handoff.fallback = policy
            handoff.launcher_identity = str(launcher_identity or "")
            handoff.scope = dict(scope or {})
            self._handoffs[binding] = handoff
            self._used_bindings[binding] = now
            while len(self._used_bindings) > MAX_USED_BINDINGS:
                self._used_bindings.popitem(last=False)
        self._log("INFO", f"session_api_key_handoff_staged provider={provider} source={source} "
                          f"fingerprint={fingerprint} launcher_pid={int(launcher_pid)} expires_in={ttl}")
        return {"ok": True, "action": "stage", "provider": provider, "source": source,
                "fingerprint": fingerprint, "expires_in": ttl, "staged": handoff.status()}

    def consume_handoff(self, binding: str, session: str, launcher_pid: int,
                        launcher_identity: str = "") -> dict[str, Any] | None:
        """Move the staged key of exactly this launch into the vault, once.

        The launcher must match by pid and, when the stage recorded one, by
        creation token: a process that reused the pid is not that launcher.
        """

        with self._lock:
            now = self._clock()
            self._expire(now)
            handoff = self._handoffs.get(binding)
            if handoff is None:
                return None
            if handoff.launcher_pid != int(launcher_pid or 0):
                return None
            if handoff.launcher_identity and not hmac.compare_digest(handoff.launcher_identity,
                                                                     str(launcher_identity or "")):
                return None
            self._handoffs.pop(binding)
            slot = (session, handoff.provider)
            if slot in self._entries:
                _wipe(handoff.secret)
                self._log("WARN", f"session_api_key_handoff_dropped session={session} "
                                  f"provider={handoff.provider} reason=session_key_exists")
                return None
            fingerprint = self.fingerprint(handoff.secret)
            entry = _Entry(session, handoff.provider, handoff.secret, handoff.source, self._next_version(),
                           now, now + handoff.ttl_seconds, fingerprint, handoff.fallback)
            entry.own(handoff.launcher_pid, handoff.launcher_identity, "launch")
            self._entries[slot] = entry
            self._losses.pop(slot, None)
            self._persist_locked()
        self._log("INFO", f"session_api_key_inject session={session} provider={entry.provider} "
                          f"source={entry.source} version={entry.version} fingerprint={fingerprint} via=launch_binding")
        return entry.metadata().as_dict()

    def sweep_ended(self, state: Callable[[int, str], str] = launcher_state, *,
                    min_interval: float = 10.0,
                    session_owner: Callable[[str], tuple[int, str] | None] | None = None) -> int:
        """End what belongs to sessions whose owning process is proven gone (pid + creation token).

        A staged key dies with its launcher; a live key or a recorded loss ends
        with its session (no loss is recorded: the session is over).  The
        owner is the launcher for a launch key, or - for a direct inject - the
        session's own process from Claude Code's record, captured by
        ``session_owner`` while it is live.  Staged keys are checked on every
        call, keys and losses at most every ``min_interval`` seconds.
        ``unknown`` ends nothing; a row with no owner stays until an operator
        clears it or the key's TTL turns it into an expired block.
        Returns the number of records ended.
        """

        with self._lock:
            now = self._clock()
            full = now - self._last_sweep >= min_interval
            if full:
                self._last_sweep = now
            owners = {(h.launcher_pid, h.launcher_identity) for h in self._handoffs.values()}
            unowned: set[str] = set()
            if full:
                owners |= {(e.launcher_pid, e.launcher_identity) for e in self._entries.values()}
                owners |= {(int(row.get("launcher_pid") or 0), str(row.get("launcher_identity") or ""))
                           for row in self._losses.values()}
                unowned = ({e.session for e in self._entries.values() if e.end_proof == "none"}
                           | {row["session"] for row in self._losses.values() if row.get("end_proof") == "none"})
        # Process and file inspection happen outside the lock.
        captured = {}
        if session_owner is not None:
            for session in unowned:
                try:
                    owner = session_owner(session)
                except Exception:  # noqa: BLE001 - no proof is "unknown", never an end
                    owner = None
                if owner is not None:
                    captured[session] = owner
        if captured:
            with self._lock:
                for entry in self._entries.values():
                    if entry.end_proof == "none" and entry.session in captured:
                        entry.own(*captured[entry.session], "session_record")
                for row in self._losses.values():
                    if row.get("end_proof") == "none" and row["session"] in captured:
                        row["launcher_pid"], row["launcher_identity"] = captured[row["session"]]
                        row["end_proof"] = "session_record"
                self._persist_locked()
        ended = {owner for owner in owners if owner[0] > 0 and state(*owner) == "ended"}
        if not ended:
            return 0
        dropped_handoffs: list[_Handoff] = []
        count = 0
        with self._lock:
            for binding, handoff in list(self._handoffs.items()):
                if (handoff.launcher_pid, handoff.launcher_identity) in ended:
                    dropped_handoffs.append(self._handoffs.pop(binding))
            if full:
                for slot, entry in list(self._entries.items()):
                    if (entry.launcher_pid, entry.launcher_identity) in ended:
                        self._drop(slot, "session_ended")
                        count += 1
                for slot, row in list(self._losses.items()):
                    if (int(row.get("launcher_pid") or 0), str(row.get("launcher_identity") or "")) in ended:
                        self._losses.pop(slot)
                        count += 1
                if count:
                    self._persist_locked()
        for handoff in dropped_handoffs:
            provider = handoff.provider
            _wipe(handoff.secret)
            self._log("INFO", f"session_api_key_handoff_dropped provider={provider} reason=launcher_gone "
                              f"launcher_pid={handoff.launcher_pid}")
        if count:
            self._log("INFO", f"session_api_key_session_ended records={count}")
        return count + len(dropped_handoffs)

    def has_handoffs(self) -> bool:
        with self._lock:
            return bool(self._handoffs)

    def set_scope_probe(self, probe: Callable[[], str] | None) -> None:
        """``probe()`` names the launch epoch that holds the workspace scope now (router wiring)."""

        self._scope_probe = probe

    def refresh_abandoned(self) -> int:
        """Apply a rebound scope now: every status read and request calls this first."""

        probe = self._scope_probe
        with self._lock:
            if probe is None or not self._handoffs:
                return 0
        try:
            epoch = str(probe() or "")
        except Exception:  # noqa: BLE001 - an unreadable scope abandons nothing
            return 0
        return self.abandon_rebound(epoch) if epoch else 0

    def claim_abandoned(self, session: str, provider: str, scope_session: Callable[[dict[str, Any]], str]) -> bool:
        """Tie an abandoned launch key to ``session`` when that launch's published record names it.

        ``scope_session(scope)`` returns the session id Claude Code published
        for the abandoned launch's socket (the same proof the claim would have
        used).  A match records the loss once and forgets the scope, so an
        operator clear afterwards sticks.
        """

        session, provider = canonical_identity(session), canonical_provider(provider)
        with self._lock:
            candidates = [(binding, dict(row["_scope"])) for binding, row in self._abandoned.items()
                          if row.get("provider") == provider and row.get("_scope")]
        if not candidates or not session.startswith("claude:"):
            return False
        uuid = session.partition(":")[2]
        for binding, scope in candidates:
            try:
                published = str(scope_session(scope) or "")
            except Exception:  # noqa: BLE001 - no proof, no mapping
                continue
            if not published or published != uuid:
                continue
            with self._lock:
                row = self._abandoned.get(binding)
                if row is None or not row.pop("_scope", None):
                    return False
                row["session"] = session
                if (session, provider) not in self._entries:
                    self._record_loss(session, provider, "abandoned", str(row.get("fallback") or FALLBACK_FAIL_CLOSED),
                                      self._next_version(), str(row.get("source") or ""),
                                      injected_at=float(row.get("staged_at") or 0))
            self._log("WARN", f"session_api_key_abandoned_session session={session} provider={provider}")
            return True
        return False

    def staged(self) -> list[dict[str, Any]]:
        """Launch keys staged and still awaiting their session's first request (metadata only)."""

        self.refresh_abandoned()
        with self._lock:
            self._expire(self._clock())
            return [handoff.status() for handoff in sorted(self._handoffs.values(), key=lambda h: h.staged_at)]

    def abandon_rebound(self, current_epoch: str) -> int:
        """Abandon staged keys whose launch no longer holds the scope.

        The scope names one launch per workspace; once another launch has
        bound it, an earlier launch's staged key can never be claimed.  The
        key is dropped at once; a metadata record (and the non-secret scope,
        for the explicit error) stays until the key would have expired.
        """

        dropped: list[_Handoff] = []
        with self._lock:
            now = self._clock()
            for binding, handoff in list(self._handoffs.items()):
                epoch = str(handoff.scope.get("epoch") or "")
                if epoch and current_epoch and epoch != current_epoch:
                    dropped.append(self._handoffs.pop(binding))
            for handoff in dropped:
                self._abandoned[handoff.binding] = {
                    **handoff.status(), "state": "abandoned_scope_rebound", "abandoned_at": now,
                    "fallback": handoff.fallback, "_scope": dict(handoff.scope)}
                _wipe(handoff.secret)
            while len(self._abandoned) > MAX_ABANDONED:
                self._abandoned.popitem(last=False)
        for handoff in dropped:
            self._log("WARN", f"session_api_key_handoff_abandoned provider={handoff.provider} "
                              f"reason=scope_rebound launcher_pid={handoff.launcher_pid} "
                              f"fingerprint={handoff.fingerprint}")
        return len(dropped)

    def abandoned(self) -> list[dict[str, Any]]:
        """Staged keys a rebound scope abandoned, metadata only (no key, no scope)."""

        self.refresh_abandoned()
        with self._lock:
            self._expire(self._clock())
            return [{name: value for name, value in row.items() if name != "_scope"}
                    for row in self._abandoned.values()]

    def abandoned_scopes(self, provider: str) -> list[dict[str, Any]]:
        """The non-secret launch scopes of abandoned keys for ``provider`` (router use only)."""

        with self._lock:
            self._expire(self._clock())
            return [dict(row["_scope"]) for row in self._abandoned.values()
                    if row["provider"] == provider and row.get("_scope")]

    def pending_handoff(self, binding: str) -> bool:
        with self._lock:
            self._expire(self._clock())
            return binding in self._handoffs

    def clear_all(self) -> None:
        with self._lock:
            for key in list(self._entries):
                entry = self._entries.pop(key)
                _wipe(entry.secret)
            for binding in list(self._handoffs):
                _wipe(self._handoffs.pop(binding).secret)
            self._abandoned.clear()
            self._losses.clear()
            self._idempotency.clear()


@dataclass(slots=True)
class _Activity:
    session: str
    runtime: str
    provider: str
    last_seen: float


class SessionActivityRegistry:
    """Sessions this router has served recently: identity, provider and time only."""

    def __init__(self, *, clock: Callable[[], float] = time.time, limit: int = 128,
                 retention_seconds: float = 24 * 3600) -> None:
        self._clock, self._limit, self._retention = clock, limit, retention_seconds
        self._lock = threading.Lock()
        self._items: OrderedDict[str, _Activity] = OrderedDict()

    def record(self, session: str, provider: str) -> None:
        if not IDENTITY_PATTERN.match(session or ""):
            return
        with self._lock:
            self._items.pop(session, None)
            self._items[session] = _Activity(session, session.split(":", 1)[0], provider, self._clock())
            while len(self._items) > self._limit:
                self._items.popitem(last=False)

    def sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            cutoff = self._clock() - self._retention
            for session in [s for s, item in self._items.items() if item.last_seen < cutoff]:
                self._items.pop(session, None)
            return [{"session": item.session, "runtime": item.runtime, "provider": item.provider,
                     "last_activity": item.last_seen} for item in reversed(self._items.values())]


ROUTER_SESSION_API_KEYS = SessionApiKeyVault()
ROUTER_SESSION_ACTIVITY = SessionActivityRegistry()
atexit.register(ROUTER_SESSION_API_KEYS.clear_all)


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "MAX_TTL_SECONDS",
    "ROUTER_SESSION_ACTIVITY",
    "ROUTER_SESSION_API_KEYS",
    "SessionActivityRegistry",
    "SessionApiKeyError",
    "SessionApiKeyMetadata",
    "SessionApiKeyVault",
    "binding_digest",
    "canonical_identity",
    "canonical_provider",
]
