"""Web sign-in, the admin page, and the REST API for OAuth tokens and access.

Routes (all JSON unless noted):

- ``GET /ca/login`` (HTML) and ``POST /ca/auth/login`` / ``/ca/auth/logout``
  are reachable without authentication.
- ``GET /ca/admin`` (HTML), ``GET|POST /ca/oauth/tokens``,
  ``GET|POST /ca/access`` and the management API ``/ca/manage/*``
  (remote_management_http) sit behind the router's normal access check:
  loopback, the admin bearer token, or a signed-in web session.

Everything reads and writes the same stores as the CLI and the menu, and the
router re-reads the token store on every request, so a change made here while
agents are running applies to their next request.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Callable

from ciel_runtime_support import oauth_login
from ciel_runtime_support.oauth_token_endpoints import import_claude_credentials, import_codex_auth
from ciel_runtime_support.oauth_token_refresh import OAuthTokenRefresher
from ciel_runtime_support.oauth_token_store import PROVIDERS, STATUS_ACTIVE, STATUS_DISABLED, OAuthTokenStore
from ciel_runtime_support.web_accounts import WebAccountError, WebAccountStore
from ciel_runtime_support.web_access_ui import render_admin_page, render_login_page

SESSION_COOKIE = "ciel_session"
PUBLIC_POST_PATHS = frozenset({"/ca/auth/login", "/ca/auth/logout"})
PENDING_SIGN_IN_SECONDS = oauth_login.LOGIN_TIMEOUT_SECONDS
_FAILED_LOGIN_DELAY_SECONDS = 0.5

# The router rebuilds its request services per request, so started web
# sign-ins live at module level for the life of the process.
_PENDING: dict[str, oauth_login.PendingSignIn] = {}
_PENDING_LOCK = threading.Lock()


def session_token(handler: Any) -> str:
    try:
        cookie = SimpleCookie(str(handler.headers.get("cookie") or ""))
    except Exception:
        return ""
    morsel = cookie.get(SESSION_COOKIE)
    return morsel.value if morsel is not None else ""


def _same_origin(handler: Any) -> bool:
    origin = str(handler.headers.get("origin") or "").strip()
    if not origin:
        # SameSite=Strict already keeps the cookie off cross-site requests.
        return True
    host = str(handler.headers.get("host") or "").strip().lower()
    return urllib.parse.urlparse(origin).netloc.lower() == host


def token_row(token: Any, now: float) -> dict[str, Any]:
    row = asdict(token)
    state = token.status
    if token.status == STATUS_ACTIVE and token.exhausted_until > now:
        state = "limited"
    elif token.status == STATUS_ACTIVE and token.draining:
        state = "draining"
    row["state"] = state
    return row


@dataclass(frozen=True, slots=True)
class WebAccessPorts:
    write_json: Callable[..., Any]
    write_html: Callable[[Any, str], Any]
    accounts: Callable[[], WebAccountStore]
    workspace_state_dir: Callable[[], Path]
    admin_token: Any  # RouterExternalTokenRepository
    external_access_enabled: Callable[[], bool]
    token_post: Callable[..., Any] | None = None
    # remote_management_http.RemoteManagementController for /ca/manage/*.
    management: Any | None = None
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True, slots=True)
class WebAccessHttpController:
    ports: WebAccessPorts

    # -- authentication ------------------------------------------------------
    def session_allowed(self, handler: Any) -> bool:
        email = self.ports.accounts().session_email(session_token(handler))
        if email is None:
            return False
        if str(getattr(handler, "command", "GET")) not in ("GET", "HEAD") and not _same_origin(handler):
            return False
        return True

    def handle_public_get(self, handler: Any, path: str) -> bool:
        if path != "/ca/login":
            return False
        query = urllib.parse.parse_qs(urllib.parse.urlparse(str(getattr(handler, "path", ""))).query)
        target = (query.get("next") or ["/ca/admin"])[0]
        self.ports.write_html(handler, render_login_page(_safe_next(target), self.ports.accounts().has_accounts()))
        return True

    def handle_get(self, handler: Any, path: str) -> bool:
        if path == "/ca/admin":
            self.ports.write_html(handler, render_admin_page())
            return True
        if path == "/ca/oauth/tokens":
            self.ports.write_json(handler, self._token_list())
            return True
        if path == "/ca/access":
            self.ports.write_json(handler, self._access_status(handler))
            return True
        if self.ports.management is not None and path.startswith("/ca/manage/"):
            return bool(self.ports.management.handle_get(handler, path))
        return False

    def handle_post(self, handler: Any, path: str, body: dict[str, Any]) -> bool:
        if path == "/ca/auth/login":
            self._login(handler, body)
            return True
        if path == "/ca/auth/logout":
            token = session_token(handler)
            if token:
                self.ports.accounts().revoke_session(token)
            self._write_with_cookie(handler, {"ok": True}, 200, "", 0)
            return True
        if path == "/ca/oauth/tokens":
            self._respond(handler, lambda: self._token_action(body))
            return True
        if path == "/ca/access":
            self._respond(handler, lambda: self._access_action(handler, body))
            return True
        if self.ports.management is not None and path.startswith("/ca/manage/"):
            return bool(self.ports.management.handle_post(handler, path, body))
        return False

    # -- responses -----------------------------------------------------------
    def _respond(self, handler: Any, action: Callable[[], dict[str, Any]]) -> None:
        try:
            payload = action()
        except (WebAccountError, ValueError, KeyError, RuntimeError, OSError) as exc:
            self.ports.write_json(handler, {"ok": False, "error": type(exc).__name__, "message": str(exc)}, 400)
            return
        self.ports.write_json(handler, {"ok": True, **payload})

    def _write_with_cookie(self, handler: Any, payload: dict[str, Any], status: int, token: str, max_age: int) -> None:
        body = json.dumps(payload).encode("utf-8")
        secure = str(handler.headers.get("x-forwarded-proto") or "").lower() == "https"
        cookie = f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}" + ("; Secure" if secure else "")
        handler.send_response(status)
        handler.send_header("content-type", "application/json")
        handler.send_header("cache-control", "no-store")
        handler.send_header("set-cookie", cookie)
        handler.send_header("content-length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _login(self, handler: Any, body: dict[str, Any]) -> None:
        accounts = self.ports.accounts()
        email = accounts.verify(str(body.get("email") or ""), str(body.get("password") or ""))
        if email is None:
            self.ports.sleep(_FAILED_LOGIN_DELAY_SECONDS)
            self.ports.write_json(handler, {"ok": False, "error": "invalid_credentials", "message": "Wrong email or password."}, 401)
            return
        token = accounts.create_session(email)
        from ciel_runtime_support.web_accounts import SESSION_TTL_SECONDS

        payload = {"ok": True, "email": email, "next": _safe_next(str(body.get("next") or "/ca/admin"))}
        self._write_with_cookie(handler, payload, 200, token, int(SESSION_TTL_SECONDS))

    # -- OAuth tokens --------------------------------------------------------
    def _store(self) -> OAuthTokenStore:
        return OAuthTokenStore(self.ports.workspace_state_dir())

    def _token_list(self) -> dict[str, Any]:
        now = self.ports.clock()
        with _PENDING_LOCK:
            _prune_pending(now)
            pending = [
                {"sign_in_id": key, "provider": item.provider, "started_at": item.created_at}
                for key, item in _PENDING.items()
            ]
        return {
            "ok": True,
            "tokens": [token_row(token, now) for token in self._store().snapshot().tokens],
            "pending_sign_ins": pending,
            "providers": list(PROVIDERS),
        }

    def _token_action(self, body: dict[str, Any]) -> dict[str, Any]:
        action = str(body.get("action") or "")
        store = self._store()
        if action == "sign_in_start":
            provider = _provider(body)
            pending = oauth_login.begin_sign_in(provider, clock=self.ports.clock)
            with _PENDING_LOCK:
                _prune_pending(self.ports.clock())
                _PENDING[pending.state] = pending
            return {"sign_in_id": pending.state, "authorize_url": pending.url, "expires_in": int(PENDING_SIGN_IN_SECONDS)}
        if action == "sign_in_finish":
            with _PENDING_LOCK:
                _prune_pending(self.ports.clock())
                pending = _PENDING.pop(str(body.get("sign_in_id") or ""), None)
            if pending is None:
                raise ValueError("This sign-in expired or is unknown; start it again.")
            result = oauth_login.parse_pasted_redirect(str(body.get("redirect") or ""))
            kwargs = {"post": self.ports.token_post} if self.ports.token_post is not None else {}
            credential, email = oauth_login.finish_sign_in(pending, result, **kwargs)
            token = store.add(pending.provider, credential, label=str(body.get("label") or "") or email, email=email, source="web-sign-in")
            return {"token": token_row(token, self.ports.clock())}
        if action == "import":
            provider = _provider(body)
            content = str(body.get("content") or "")
            if not content.strip():
                raise ValueError("Paste the contents of auth.json or .credentials.json.")
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "credentials.json"
                path.write_text(content, encoding="utf-8")
                reader = import_codex_auth if provider == "codex" else import_claude_credentials
                credential, email = reader(path)
            token = store.add(provider, credential, label=str(body.get("label") or "") or email or provider, email=email, source="web-import")
            return {"token": token_row(token, self.ports.clock())}
        token_id = str(body.get("token_id") or "")
        if action == "update":
            with store.transaction() as snapshot:
                token = snapshot.get(token_id)
                if token is None:
                    raise KeyError(f"No token {token_id} in this workspace.")
                if "label" in body:
                    token.label = str(body.get("label") or "").strip() or token.token_id
                if "enabled" in body:
                    token.status = STATUS_ACTIVE if bool(body.get("enabled")) else STATUS_DISABLED
                    if token.status == STATUS_ACTIVE:
                        token.last_error = ""
                row = token_row(token, self.ports.clock())
            return {"token": row}
        if action == "refresh":
            outcome = OAuthTokenRefresher(store).refresh(token_id, force=True)
            return {"refreshed": bool(outcome.refreshed), "detail": outcome.detail}
        if action == "remove":
            if not store.remove(token_id):
                raise KeyError(f"No token {token_id} in this workspace.")
            return {"removed": token_id}
        raise ValueError(f"Unknown action {action!r}.")

    # -- access (accounts, admin token) --------------------------------------
    def _access_status(self, handler: Any) -> dict[str, Any]:
        token = self.ports.admin_token.get()
        return {
            "ok": True,
            "signed_in_as": self.ports.accounts().session_email(session_token(handler)),
            "external_access": bool(self.ports.external_access_enabled()),
            "admin_token": {"configured": bool(token), "hint": f"…{token[-4:]}" if token else ""},
            "accounts": self.ports.accounts().list_accounts(),
        }

    def _access_action(self, handler: Any, body: dict[str, Any]) -> dict[str, Any]:
        action = str(body.get("action") or "")
        accounts = self.ports.accounts()
        if action == "add_account":
            return {"email": accounts.add(str(body.get("email") or ""), str(body.get("password") or ""))}
        if action == "reset_password":
            return {"email": accounts.set_password(str(body.get("email") or ""), str(body.get("password") or ""))}
        if action == "remove_account":
            return {"email": accounts.remove(str(body.get("email") or ""))}
        if action == "revoke_sessions":
            return {"revoked": accounts.revoke_all_sessions()}
        if action == "rotate_admin_token":
            # Shown once; the old token stops working immediately.
            return {"admin_token": self.ports.admin_token.rotate()}
        raise ValueError(f"Unknown action {action!r}.")


def _provider(body: dict[str, Any]) -> str:
    provider = str(body.get("provider") or "")
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {', '.join(PROVIDERS)}")
    return provider


def _prune_pending(now: float) -> None:
    for key in [key for key, item in _PENDING.items() if now - item.created_at > PENDING_SIGN_IN_SECONDS]:
        _PENDING.pop(key, None)


def _safe_next(target: str) -> str:
    # Only same-site paths: never an absolute URL or a scheme-relative one.
    return target if target.startswith("/") and not target.startswith("//") else "/ca/admin"




def accounts_directory() -> Path:
    from ciel_runtime_support.runtime_paths import CONFIG_DIR

    return Path(CONFIG_DIR) / "web-access"


def default_accounts() -> WebAccountStore:
    return WebAccountStore(accounts_directory())


def request_has_web_session(handler: Any) -> bool:
    """The router's access check: a live session cookie from a web account."""

    try:
        email = default_accounts().session_email(session_token(handler))
    except Exception:
        return False
    if email is None:
        return False
    return str(getattr(handler, "command", "GET")) in ("GET", "HEAD") or _same_origin(handler)


def default_controller(
    write_json: Callable[..., Any],
    write_text: Callable[..., Any],
    admin_token: Any,
    external_access_enabled: Callable[..., bool],
    management: Any | None = None,
) -> WebAccessHttpController:
    from ciel_runtime_support.runtime_paths import WORKSPACE_STATE_DIR

    return WebAccessHttpController(
        WebAccessPorts(
            write_json=write_json,
            write_html=lambda handler, page: write_text(handler, page, 200, "text/html; charset=utf-8"),
            accounts=default_accounts,
            workspace_state_dir=lambda: Path(WORKSPACE_STATE_DIR),
            admin_token=admin_token,
            external_access_enabled=lambda: bool(external_access_enabled(None)),
            management=management,
        )
    )


__all__ = [
    "PUBLIC_POST_PATHS",
    "SESSION_COOKIE",
    "WebAccessHttpController",
    "WebAccessPorts",
    "accounts_directory",
    "default_accounts",
    "default_controller",
    "request_has_web_session",
    "session_token",
    "token_row",
]
