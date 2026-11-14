"""Choose a session's own API key for exactly that session's requests.

Order for one local request (the Remote Bridge path never comes here and its
one-request key stays the highest-priority override):

1. The request's canonical identity: ``claude:<x-claude-code-session-id>`` on
   the Anthropic paths, ``codex:<session key>`` on the Responses/Codex paths.
   No identity, no session key.
2. If the router holds a key staged by the launch that is bound to this exact
   session (launch epoch, random messaging socket, launcher pid, proven by the
   session record Claude Code published for that socket), it moves into the
   vault now, once.
3. The vault key for that identity and the serving provider, if any, replaces
   the provider's stored keys in a request-local config copy.  A failover to
   another provider looks that provider up again; nothing falls back across
   sessions or providers.

The copy is marked so cooldown and rate-limit state for it stay in process
memory (``api_key_cooldown``, ``router_rate_limit_service``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .claude_socket_paths import canonical_socket_path
from .oauth_routing import codex_session_key
from .remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER, REQUEST_API_KEY_MARKER
from .session_api_key_vault import (
    IDENTITY_PATTERN,
    LOSS_CAUSES,
    ROUTER_SESSION_ACTIVITY,
    ROUTER_SESSION_API_KEYS,
    SessionActivityRegistry,
    SessionApiKeyVault,
    binding_digest,
    launcher_state,
)

class SessionApiKeyUnavailable(Exception):
    """This session's own key for this provider is gone and its policy is fail_closed."""

    status = 401
    error_type = "authentication_error"

    def __init__(self, session: str, provider: str, cause: str) -> None:
        self.session, self.provider, self.cause = session, provider, cause
        if cause == "state_unreadable":
            message = (
                f"Ciel Runtime: session-key marker unreadable: reset required. Requests for {provider} from "
                "sessions without a live session key are blocked and will not use the stored key until an "
                "operator resets the state (ciel-runtimectl session-api-key reset-state, the admin page, "
                "REST action reset_state, or the menu)."
            )
        else:
            message = (
                f"Ciel Runtime: the session API key for {provider} {LOSS_CAUSES.get(cause, cause)}; this session "
                "will not use the stored key. Re-inject a session key, or clear it (ciel-runtimectl "
                "session-api-key clear, the admin page, REST or the menu) to use the stored key."
            )
        super().__init__(message)


SESSION_API_KEY_SCOPE_MARKER = "_ciel_session_key_scope"
_BASE_FIELD = "_ciel_session_key_stored_config"
_ANTHROPIC_PATHS = frozenset({"/v1/messages", "/v1/messages/count_tokens"})
_CODEX_PATHS = frozenset({"/v1/responses", "/v1/responses/compact"})


def _header(headers: Any, name: str) -> str:
    try:
        value = headers.get(name)
        if value is None and hasattr(headers, "items"):
            value = next((item for key, item in headers.items() if str(key).lower() == name), None)
    except Exception:
        return ""
    return str(value or "").strip()


def request_identity(path: str, headers: Any, body: Any) -> str:
    """The canonical session identity of one request, or "" when it has none."""

    if path in _ANTHROPIC_PATHS:
        session = _header(headers, "x-claude-code-session-id")
        identity = f"claude:{session}" if session else ""
    elif path in _CODEX_PATHS or path.startswith("/backend-api/codex"):
        try:
            session = codex_session_key(headers, body if isinstance(body, dict) else {})
        except Exception:
            session = ""
        identity = f"codex:{session}" if session and session != "default" else ""
    else:
        identity = ""
    return identity if IDENTITY_PATTERN.match(identity) else ""


def session_scoped(config: dict[str, Any]) -> str:
    """The identity a request-local config copy carries a session key for."""

    value = config.get(SESSION_API_KEY_SCOPE_MARKER) if isinstance(config, dict) else None
    return value if isinstance(value, str) and IDENTITY_PATTERN.match(value) else ""


@dataclass(frozen=True, slots=True)
class SessionApiKeyRouting:
    vault: SessionApiKeyVault = ROUTER_SESSION_API_KEYS
    activity: SessionActivityRegistry = ROUTER_SESSION_ACTIVITY
    # (headers, body) -> {"session_id", "epoch", "socket", "launcher_pid"} for
    # a main-session request of the launch bound through its published record.
    launch_binding: Callable[[Any, Any], dict[str, Any] | None] = lambda _headers, _body: None
    log: Callable[[str, str], None] = lambda _level, _message: None
    # (pid, creation token) -> alive | ended | unknown (session_api_key_vault.launcher_state).
    launcher_state: Callable[[int, str], str] = launcher_state
    # session -> (pid, creation token) of its one live process, from Claude
    # Code's own record (session_api_key_liveness.session_owner); None = unknown.
    session_owner: Callable[[str], tuple[int, str] | None] | None = None
    # abandoned launch scope -> the session id Claude Code published for its
    # socket ("" when none); the same proof the launch's own claim uses.
    scope_session: Callable[[dict[str, Any]], str] = lambda _scope: ""

    def identity(self, path: str, headers: Any, body: Any, provider: str) -> str:
        identity = request_identity(path, headers, body)
        if not identity:
            return ""
        self.activity.record(identity, provider)
        # Ended sessions free their records here (throttled inside the vault);
        # direct injects get their session's own process as end proof.
        self.vault.sweep_ended(self.launcher_state, session_owner=self.session_owner)
        # A launch that lost the workspace scope can never claim its staged
        # key: it is abandoned now, and its session gets the explicit error.
        self.vault.refresh_abandoned()
        if identity.startswith("claude:"):
            if self.vault.has_handoffs():
                self._consume_launch_key(identity, headers, body)
            try:
                self.vault.claim_abandoned(identity, provider, self.scope_session)
            except Exception as exc:  # noqa: BLE001 - mapping is evidence work; never fail the request
                self.log("WARN", f"session_api_key_abandoned_mapping_failed error={type(exc).__name__}")
        return identity

    def _consume_launch_key(self, identity: str, headers: Any, body: Any) -> None:
        try:
            bound = self.launch_binding(headers, body)
        except Exception as exc:
            self.log("WARN", f"session_api_key_launch_binding_failed error={type(exc).__name__}")
            return
        if not bound or f"claude:{bound.get('session_id')}" != identity:
            return
        socket, epoch = str(bound.get("socket") or ""), str(bound.get("epoch") or "")
        if not socket or not epoch:
            return
        self.vault.consume_handoff(binding_digest(canonical_socket_path(socket), epoch), identity,
                                   int(bound.get("launcher_pid") or 0), str(bound.get("launcher_identity") or ""))

    def overlay(self, identity: str, provider: str, config: dict[str, Any]) -> dict[str, Any]:
        """``config`` itself, or a request-local copy carrying this session's key."""

        if not isinstance(config, dict):
            return config
        base = config
        if SESSION_API_KEY_SCOPE_MARKER in config:
            # An earlier overlay of this request: start again from the stored
            # provider config it was made from, never from the session key.
            original = config.get(_BASE_FIELD)
            base = original if isinstance(original, dict) else {
                name: value for name, value in config.items()
                if name not in {SESSION_API_KEY_SCOPE_MARKER, _BASE_FIELD, "api_key", "api_keys"}
            }
        if not identity or base.get(REQUEST_API_KEY_MARKER) is True or base.get(REMOTE_BRIDGE_CONFIG_MARKER) is True:
            return base
        outcome, value = self.vault.resolve(identity, provider)
        if outcome == "blocked":
            # Never a silent switch to the stored key and never a bare upstream 401.
            raise SessionApiKeyUnavailable(identity, provider, value)
        if outcome != "key":
            return base
        return {**base, "api_key": value, "api_keys": [value], SESSION_API_KEY_SCOPE_MARKER: identity,
                _BASE_FIELD: base}

    def register_cooldown(self, provider: str, config: dict[str, Any], seconds: float) -> bool:
        identity = session_scoped(config)
        if not identity:
            return False
        self.vault.register_cooldown(identity, provider, seconds)
        return True

    def cooldown_until(self, provider: str, config: dict[str, Any]) -> float | None:
        identity = session_scoped(config)
        return self.vault.cooldown_until(identity, provider) if identity else None


ROUTER_SESSION_API_KEY_ROUTING = SessionApiKeyRouting()


__all__ = [
    "ROUTER_SESSION_API_KEY_ROUTING",
    "SESSION_API_KEY_SCOPE_MARKER",
    "SessionApiKeyRouting",
    "SessionApiKeyUnavailable",
    "request_identity",
    "session_scoped",
]
