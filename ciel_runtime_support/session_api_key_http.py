"""REST for session-scoped API keys; the raw key goes in and never comes out.

Routes (JSON), dispatched by the router right after the body is parsed and
before any other handler, so no generic trace or handler sees the key:

- ``GET /ca/session-api-keys`` - sessions this router served recently
  (identity, runtime, provider, last activity) and the session keys it holds
  as metadata: session, provider, source, version, injected/expiry times and a
  per-process fingerprint.
- ``POST /ca/session-api-keys`` - ``{"action": "inject" | "replace" | "clear",
  "session": "claude:<id>", "provider": "...", "api_key": "...",
  "ttl_seconds": N, "expected_version": N}`` with an ``Idempotency-Key``
  header.  ``inject`` creates (expected version 0), ``replace`` and ``clear``
  need the current version, so a stale caller cannot undo a newer key.
- ``POST /ca/session-api-keys/handoff`` - the launcher stages the key its
  remote startup response returned for the launch it is about to start
  (admin token and loopback only; see session_api_key_vault).

Access: the router's normal check has already run (loopback, the admin token,
or a signed-in web session; a forwarded loopback request counts as external).
A mutation also needs the admin token, a signed-in same-origin web session, or
a same-origin loopback request; the handoff needs the admin token.  Every
change is audited with metadata only.  Nothing here is persistent: a router
restart drops every session key, and stored keys (``api-key``,
``set-api-key``, ``/ca/manage/apply``) are untouched.
"""

from __future__ import annotations

import hmac
import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .router_access import is_loopback_address, request_from_forwarding_proxy, router_request_bearer_token
from .session_api_key_ui import UI_PATH, render_session_keys_page
from .session_api_key_vault import (
    DEFAULT_TTL_SECONDS,
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    ROUTER_SESSION_ACTIVITY,
    ROUTER_SESSION_API_KEYS,
    SessionActivityRegistry,
    SessionApiKeyError,
    SessionApiKeyVault,
)

KEYS_PATH = "/ca/session-api-keys"
# Sent only by this machine's ctl and menu (session_api_key_client), never by the web page.
LOCAL_CLIENT_HEADER = "x-ciel-session-key-client"
HANDOFF_PATH = "/ca/session-api-keys/handoff"
PATHS = frozenset({KEYS_PATH, HANDOFF_PATH})
AUDIT_FILE = "management-audit.jsonl"
_SECRET_FIELDS = ("api_key", "key", "secret")
_STATUS = {"not_found": 404, "version_conflict": 409, "idempotency_conflict": 409, "handoff_exists": 409,
           "capacity": 429, "unauthorized": 401, "forbidden": 403}
PERSISTENT_NOTE = ("Session keys live in router memory for one session and provider; stored API keys "
                   "(api-key, set-api-key, set-api-keys, /ca/manage/apply) are persistent and unchanged.")
# The reset after an unreadable marker file needs this exact confirmation.
RESET_CONFIRM = "reset-session-key-state"
RESET_WARNING = ("Reset moves the unreadable session-key marker file aside (its bytes are kept) and writes "
                 "this router's current state. Sessions it was blocking will use their stored API key "
                 "again on their next request.")
UNREADABLE_NOTICE = ("session-key marker unreadable: reset required. Every session without a live session key "
                     "is blocked until an operator resets it (ciel-runtimectl session-api-key reset-state, "
                     "the admin page, REST action reset_state, or the menu).")
NO_PROOF_NOTICE = "no session-end proof: clear manually"


def _annotated(row: dict[str, Any], now: float) -> dict[str, Any]:
    """A key or loss row plus what a person needs to act on it (age, manual-clear notice)."""

    out = dict(row)
    since = float(row.get("injected_at") or row.get("lost_at") or now)
    out["age_seconds"] = max(0, int(now - since))
    if row.get("end_proof", "none") == "none":
        out["notice"] = NO_PROOF_NOTICE
    return out


def _pop_secret(body: dict[str, Any]) -> str:
    """Take the key out of the parsed body so nothing after this sees it."""

    value = ""
    for field in _SECRET_FIELDS:
        candidate = body.pop(field, None)
        if isinstance(candidate, str) and not value:
            value = candidate
    return value


@dataclass(frozen=True, slots=True)
class SessionApiKeyHttpPorts:
    write_json: Callable[..., Any]
    admin_token: Callable[[], str]
    session_allowed: Callable[[Any], bool]
    workspace_state_dir: Callable[[], Path]
    current_provider: Callable[[], str]
    vault: SessionApiKeyVault = ROUTER_SESSION_API_KEYS
    activity: SessionActivityRegistry = ROUTER_SESSION_ACTIVITY
    clock: Callable[[], float] = time.time
    # session -> (pid, creation token) of its live process (session_api_key_liveness), or None.
    session_owner: Callable[[str], tuple[int, str] | None] = lambda _session: None
    # (handler, html) for the page at UI_PATH; None serves no page.
    write_html: Callable[[Any, str], Any] | None = None
    # The local-client credential (session_api_key_client.LocalClientTokenRepository); "" = none.
    local_client_token: Callable[[], str] = lambda: ""
    # binding -> the router's own non-secret launch scope when that binding is
    # this scope's (epoch + socket digest), else None.  A client-sent scope is
    # never used.
    launch_scope: Callable[[str], dict[str, Any] | None] = lambda _binding: None


class SessionApiKeyHttpController:
    def __init__(self, ports: SessionApiKeyHttpPorts) -> None:
        self.ports = ports

    # -- access ----------------------------------------------------------------
    def _admin(self, handler: Any) -> bool:
        expected = self.ports.admin_token()
        supplied = router_request_bearer_token(handler)
        return bool(expected and supplied and hmac.compare_digest(expected, supplied))

    @staticmethod
    def _local(handler: Any) -> bool:
        try:
            return is_loopback_address(str(handler.client_address[0])) and not request_from_forwarding_proxy(handler)
        except Exception:
            return False

    @staticmethod
    def _same_origin(handler: Any) -> bool:
        origin = str(handler.headers.get("origin") or "").strip()
        if not origin:
            return True
        host = str(handler.headers.get("host") or "").strip().lower()
        return urllib.parse.urlparse(origin).netloc.lower() == host

    def _web_session(self, handler: Any) -> bool:
        if not self._same_origin(handler):
            return False
        try:
            return bool(self.ports.session_allowed(handler))
        except Exception:
            return False

    def _authorized(self, handler: Any) -> bool:
        """The admin token or a signed-in same-origin web session - loopback alone is not enough."""

        return self._admin(handler) or self._web_session(handler)

    def _local_client(self, handler: Any) -> bool:
        """This machine's own ctl or menu: loopback, unforwarded, the admin token and the local-client credential.

        The credential is a file only local processes of this OS user can
        read; the REST and web surfaces never have it.  It labels the audit
        source; it is not a defence against that same OS user.
        """

        expected = self.ports.local_client_token()
        supplied = str(handler.headers.get(LOCAL_CLIENT_HEADER) or "").strip()
        return bool(self._local(handler) and self._admin(handler) and expected and supplied
                    and hmac.compare_digest(expected, supplied))

    def _refuse(self, handler: Any) -> None:
        self.ports.write_json(handler, {"ok": False, "error": "unauthorized",
                                        "message": "the admin token or a signed-in web session is required"}, 401)

    # -- routing -----------------------------------------------------------------
    def handle_get(self, handler: Any, path: str) -> bool:
        if path == UI_PATH and self.ports.write_html is not None:
            # The page holds no data; every call it makes needs a session.
            self.ports.write_html(handler, render_session_keys_page())
            return True
        if path != KEYS_PATH:
            return False
        if not self._authorized(handler):
            self._refuse(handler)
            return True
        self.ports.write_json(handler, {"ok": True, **self.state()})
        return True

    def handle_post(self, handler: Any, path: str, body: dict[str, Any]) -> bool:
        if path not in PATHS:
            return False
        secret = _pop_secret(body) if isinstance(body, dict) else ""
        if path == HANDOFF_PATH and isinstance(body, dict):
            body["action"] = "handoff"
        try:
            if path == HANDOFF_PATH:
                result = self._handoff(handler, body, secret)
            else:
                result = self._action(handler, body, secret)
        except SessionApiKeyError as exc:
            self._audit(handler, body, {"ok": False, "error": exc.code})
            self.ports.write_json(handler, {"ok": False, "error": exc.code, "message": str(exc)},
                                  _STATUS.get(exc.code, 400))
            return True
        finally:
            secret = ""
        self._audit(handler, body, result)
        self.ports.write_json(handler, {**result, **self.state()})
        return True

    # -- operations --------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        now = self.ports.clock()
        vault = self.ports.vault
        keys = [_annotated(row, now) for row in vault.metadata()]
        losses = [_annotated(row, now) for row in vault.losses()]
        health = vault.health()
        notices = [UNREADABLE_NOTICE] if health["state_unreadable"] else []
        if health["rows"] >= health["limit"]:
            notices.append(f"session-key records full ({health['rows']} of {health['limit']}): clear the keys "
                           "of ended sessions before injecting another.")
        by_session: dict[str, list[dict[str, Any]]] = {}
        for item in keys + losses:
            by_session.setdefault(item["session"], []).append(item)
        sessions = []
        seen = set()
        for row in self.ports.activity.sessions():
            seen.add(row["session"])
            sessions.append({**row, "keys": by_session.get(row["session"], [])})
        for session, rows in by_session.items():
            if session not in seen:
                sessions.append({"session": session, "runtime": session.split(":", 1)[0], "provider": "",
                                 "last_activity": None, "keys": rows})
        return {"sessions": sessions, "keys": keys, "losses": losses, "staged": vault.staged(),
                "abandoned": vault.abandoned(), "health": health, "notices": notices,
                "reset": {"confirm": RESET_CONFIRM, "warning": RESET_WARNING},
                "current_provider": self.ports.current_provider(),
                "ttl_seconds": {"min": MIN_TTL_SECONDS, "max": MAX_TTL_SECONDS, "default": DEFAULT_TTL_SECONDS},
                "persistent_note": PERSISTENT_NOTE}

    @staticmethod
    def _idempotency_key(handler: Any, body: dict[str, Any]) -> str:
        return str(handler.headers.get("idempotency-key") or body.get("idempotency_key") or "").strip()

    def _source(self, handler: Any, body: dict[str, Any]) -> str:
        """Server-derived: ``ctl``/``menu`` only with the local-client credential, else rest or dashboard."""

        requested = str(body.get("source") or "").strip()
        if requested in {"ctl", "menu"} and self._local_client(handler):
            return requested
        return "rest" if self._admin(handler) else "dashboard"

    def _action(self, handler: Any, body: dict[str, Any], secret: str) -> dict[str, Any]:
        if not self._authorized(handler):
            raise SessionApiKeyError("unauthorized", "the admin token or a signed-in same-origin web session is required")
        action = str(body.get("action") or "").strip().lower()
        idempotency = self._idempotency_key(handler, body)
        source = self._source(handler, body)
        if action in {"inject", "replace"}:
            if not secret:
                raise SessionApiKeyError("invalid_key", "api_key is required")
            expected = body.get("expected_version", 0 if action == "inject" else None)
            if action == "replace" and expected in (None, "", 0, "0"):
                raise SessionApiKeyError("invalid_version", "replace needs the current expected_version")
            if action == "inject" and str(expected) != "0":
                raise SessionApiKeyError("invalid_version", "inject creates a key; use replace for an existing one")
            try:
                owner = self.ports.session_owner(str(body.get("session") or ""))
            except Exception:  # noqa: BLE001 - no proof is "unknown"; the row is then cleared manually
                owner = None
            return self.ports.vault.put(body.get("session"), body.get("provider"), secret, source=source,
                                        expected_version=expected, idempotency_key=idempotency,
                                        ttl_seconds=body.get("ttl_seconds"), fallback=body.get("fallback"),
                                        owner=owner)
        if action == "clear":
            if secret:
                raise SessionApiKeyError("invalid_request", "clear takes no api_key")
            expected = body.get("expected_version")
            if expected in (None, ""):
                raise SessionApiKeyError("invalid_version", "clear needs the current expected_version")
            return self.ports.vault.clear(body.get("session"), body.get("provider"), expected_version=expected,
                                          idempotency_key=idempotency, source=source)
        if action == "reset_state":
            # Router-wide and it unblocks sessions, so it needs the exact confirmation.
            if secret:
                raise SessionApiKeyError("invalid_request", "reset_state takes no api_key")
            if str(body.get("confirm") or "") != RESET_CONFIRM:
                raise SessionApiKeyError("confirmation_required", RESET_WARNING
                                         + f" Send confirm: {RESET_CONFIRM!r} to proceed.")
            return self.ports.vault.reset_marker_state(source)
        raise SessionApiKeyError("invalid_action", "action must be inject, replace, clear or reset_state")

    def _handoff(self, handler: Any, body: dict[str, Any], secret: str) -> dict[str, Any]:
        if not (self._local(handler) and self._admin(handler)):
            raise SessionApiKeyError("forbidden", "the launch handoff needs the local admin token")
        if not secret:
            raise SessionApiKeyError("invalid_key", "api_key is required")
        try:
            launcher_pid = int(body.get("launcher_pid") or 0)
        except (TypeError, ValueError):
            launcher_pid = 0
        if launcher_pid <= 0:
            raise SessionApiKeyError("invalid_request", "launcher_pid is required")
        identity = str(body.get("launcher_identity") or "")
        if not re.fullmatch(r"[0-9]{1,32}", identity):
            raise SessionApiKeyError("invalid_request", "launcher_identity is required")
        binding = str(body.get("binding") or "")
        try:
            scope = self.ports.launch_scope(binding)
        except Exception:  # noqa: BLE001 - no scope only means the key cannot be marked abandoned
            scope = None
        return self.ports.vault.stage_handoff(binding, body.get("provider"), secret,
                                              source=str(body.get("source") or ""),
                                              ttl_seconds=body.get("ttl_seconds"), launcher_pid=launcher_pid,
                                              launcher_identity=identity, fallback=body.get("fallback"),
                                              scope=scope if isinstance(scope, dict) else None)

    def _audit(self, handler: Any, body: dict[str, Any], result: dict[str, Any]) -> None:
        key = result.get("key") if isinstance(result.get("key"), dict) else {}
        try:
            origin = str(handler.client_address[0])
        except Exception:
            origin = ""
        record = {
            "time": self.ports.clock(),
            "kind": "session_api_key",
            "origin": origin,
            "action": str(body.get("action") or "")[:16],
            "session": str(key.get("session") or body.get("session") or "")[:210],
            "provider": str(key.get("provider") or result.get("provider") or body.get("provider") or "")[:64],
            "source": str(key.get("source") or result.get("source") or ""),
            "version": key.get("version"),
            "fingerprint": key.get("fingerprint") or result.get("fingerprint"),
            "ok": bool(result.get("ok")),
            "error": result.get("error"),
            "replayed": bool(result.get("replayed")),
        }
        path = self.ports.workspace_state_dir() / AUDIT_FILE
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        except OSError:
            pass


__all__ = [
    "HANDOFF_PATH",
    "KEYS_PATH",
    "PATHS",
    "PERSISTENT_NOTE",
    "SessionApiKeyHttpController",
    "SessionApiKeyHttpPorts",
]
