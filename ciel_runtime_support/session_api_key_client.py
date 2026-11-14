"""Loopback client for the router's session-key REST (launcher and ctl).

It only talks to a loopback router, first checks that ``/health`` names the
expected workspace, sends the router admin token, never follows a redirect,
and never logs a request body.  Responses carry metadata only.
"""

from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .router_access import is_loopback_address
from .session_api_key_http import HANDOFF_PATH, KEYS_PATH, LOCAL_CLIENT_HEADER


class SessionApiKeyClientError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.code, self.status = code, status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _opener() -> urllib.request.OpenerDirector:
    # No proxy (a loopback router never goes through one) and no redirects.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


@dataclass(frozen=True, slots=True)
class SessionApiKeyClient:
    base_url: str
    admin_token: Callable[[], str]
    expected_workspace: str
    canonical_workspace: Callable[[str], str]
    timeout: float = 5.0
    opener: Callable[[], Any] = _opener
    # This machine's local-client credential: it lets the router record ctl/menu as the source.
    local_client_token: Callable[[], str] = lambda: ""

    def _url(self, path: str) -> str:
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme != "http" or not is_loopback_address(parsed.hostname):
            raise SessionApiKeyClientError("not_loopback", "the router address is not a loopback HTTP address")
        return f"{self.base_url.rstrip('/')}{path}"

    def _open(self, request: urllib.request.Request) -> tuple[int, dict[str, Any]]:
        try:
            with self.opener().open(request, timeout=self.timeout) as response:
                status, raw = response.status, response.read(1_048_576)
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read(1_048_576)
        except (OSError, ValueError) as exc:
            raise SessionApiKeyClientError("router_unreachable", f"router request failed: {type(exc).__name__}") from None
        if 300 <= status < 400:
            raise SessionApiKeyClientError("redirect_refused", "the router answered with a redirect", status)
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeError, ValueError):
            raise SessionApiKeyClientError("bad_response", "the router answer is not JSON", status) from None
        return status, payload if isinstance(payload, dict) else {}

    def verify_workspace(self) -> None:
        status, payload = self._open(urllib.request.Request(self._url("/health"), method="GET"))
        if status != 200:
            raise SessionApiKeyClientError("router_unhealthy", f"router /health answered {status}", status)
        workspace = str(payload.get("workspace") or "")
        if not workspace or self.canonical_workspace(workspace) != self.canonical_workspace(self.expected_workspace):
            raise SessionApiKeyClientError("workspace_mismatch", "the router on this port serves another workspace")

    def _headers(self, idempotency_key: str = "") -> dict[str, str]:
        token = self.admin_token()
        if not token:
            raise SessionApiKeyClientError("no_admin_token", "the router admin token is unavailable")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                   "Accept": "application/json"}
        local = self.local_client_token()
        if local:
            headers[LOCAL_CLIENT_HEADER] = local
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def state(self) -> dict[str, Any]:
        self.verify_workspace()
        status, payload = self._open(urllib.request.Request(self._url(KEYS_PATH), headers=self._headers(),
                                                            method="GET"))
        if status != 200:
            raise SessionApiKeyClientError(str(payload.get("error") or "rejected"),
                                           str(payload.get("message") or f"HTTP {status}"), status)
        return payload

    def _post(self, path: str, body: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        self.verify_workspace()
        data = json.dumps(body).encode("utf-8")
        body.clear()  # the only copy left is the encoded request
        request = urllib.request.Request(self._url(path), data=data, headers=self._headers(idempotency_key),
                                         method="POST")
        status, payload = self._open(request)
        if status != 200 or not payload.get("ok"):
            raise SessionApiKeyClientError(str(payload.get("error") or "rejected"),
                                           str(payload.get("message") or f"HTTP {status}"), status)
        return payload

    def action(self, body: dict[str, Any], idempotency_key: str = "") -> dict[str, Any]:
        return self._post(KEYS_PATH, body, idempotency_key or secrets.token_hex(16))

    def handoff(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._post(HANDOFF_PATH, body, secrets.token_hex(16))


__all__ = ["SessionApiKeyClient", "SessionApiKeyClientError"]
