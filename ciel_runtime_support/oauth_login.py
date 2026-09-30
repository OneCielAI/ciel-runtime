"""Browser sign-in (authorization code + PKCE) that adds a token to the store.

Each sign-in creates its own session and refresh token family, independent of
the one the Codex or Claude CLI keeps for itself, so the store and the CLI
never refresh the same refresh token.
"""

from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import socket
import threading
from typing import Any, Callable
import urllib.parse
import webbrowser

from ciel_runtime_support.oauth_token_endpoints import (
    ENDPOINTS,
    HttpPost,
    OAuthProviderEndpoints,
    credential_from_response,
    token_request,
    urllib_post,
)
from ciel_runtime_support.oauth_token_store import OAuthCredential

LOGIN_TIMEOUT_SECONDS = 300.0
_DONE_PAGE = (
    b"<!doctype html><meta charset=utf-8><title>ciel-runtime</title>"
    b"<p style='font:16px system-ui;margin:3em'>Signed in. You can close this tab and return to the terminal.</p>"
)


def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorize_url(endpoints: OAuthProviderEndpoints, redirect_uri: str, state: str, challenge: str) -> str:
    params = [
        ("response_type", "code"),
        ("client_id", endpoints.client_id),
        ("redirect_uri", redirect_uri),
        ("scope", " ".join(endpoints.scopes)),
        ("code_challenge", challenge),
        ("code_challenge_method", "S256"),
        *endpoints.extra_authorize,
        ("state", state),
    ]
    return endpoints.authorize_url + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


class LoopbackCallback:
    """Waits for the authorization redirect on 127.0.0.1."""

    def __init__(self, port: int, path: str) -> None:
        self.path = path
        self.result: dict[str, str] = {}
        self._received = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass

            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != owner.path:
                    self.send_response(404)
                    self.end_headers()
                    return
                query = urllib.parse.parse_qs(parsed.query)
                owner.result = {key: values[0] for key, values in query.items() if values}
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_DONE_PAGE)
                owner._received.set()

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = int(self.server.server_address[1])
        threading.Thread(target=self.server.serve_forever, name="ciel-oauth-callback", daemon=True).start()

    @property
    def received(self) -> bool:
        return self._received.is_set()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def wait(self, timeout: float) -> dict[str, str]:
        try:
            if not self._received.wait(timeout):
                raise TimeoutError("no sign-in redirect arrived before the timeout")
            return self.result
        finally:
            self.close()


def parse_pasted_redirect(text: str) -> dict[str, str]:
    """Read ``code`` and ``state`` from what the user copied after signing in.

    On a remote machine the provider redirects the user's own browser to
    ``localhost`` there, which never reaches this process; the address the
    browser ends on still carries the code. Accepts the full URL, its query
    string, or the ``code#state`` form some sign-in pages display.
    """

    value = text.strip()
    if not value:
        return {}
    query = urllib.parse.urlparse(value).query if "://" in value else value.lstrip("?")
    if "=" not in query and "#" in value:
        code, _, state = value.partition("#")
        return {"code": code.strip(), "state": state.strip()}
    return {key: values[0] for key, values in urllib.parse.parse_qs(query).items() if values}


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def login(
    provider: str,
    *,
    open_browser: Callable[[str], Any] = webbrowser.open,
    post: HttpPost = urllib_post,
    output: Callable[[str], None] = print,
    timeout: float = LOGIN_TIMEOUT_SECONDS,
    redirect_input: Callable[[], str] | None = None,
) -> tuple[OAuthCredential, str]:
    """Sign in with PKCE.

    Without ``redirect_input`` the loopback callback must receive the
    redirect. With it (interactive use) the user presses Enter once the
    browser shows the sign-in finished, or pastes the address the browser
    ended on when the browser runs on another machine; a busy fixed callback
    port then only disables the automatic path.
    """

    endpoints = ENDPOINTS[provider]
    callback: LoopbackCallback | None = None
    if not endpoints.callback_port or _port_free(endpoints.callback_port):
        callback = LoopbackCallback(endpoints.callback_port, endpoints.callback_path)
    elif redirect_input is None:
        raise RuntimeError(
            f"port {endpoints.callback_port} is in use (another {provider} sign-in may be open); close it and retry"
        )
    port = callback.port if callback is not None else endpoints.callback_port
    redirect_uri = f"http://localhost:{port}{endpoints.callback_path}"
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    url = authorize_url(endpoints, redirect_uri, state, challenge)
    output(f"Sign in to {provider} in the browser. If it does not open, visit:\n{url}")
    try:
        open_browser(url)
    except Exception:
        pass
    if redirect_input is None:
        assert callback is not None
        result = callback.wait(timeout)
    else:
        try:
            pasted = redirect_input()
            result = dict(callback.result) if callback is not None and callback.received else parse_pasted_redirect(pasted)
        finally:
            if callback is not None:
                callback.close()
        if not result:
            raise RuntimeError("sign-in cancelled: no redirect arrived and nothing was pasted")
    if result.get("error"):
        raise RuntimeError(f"sign-in failed: {result.get('error')} {result.get('error_description', '')}".strip())
    if result.get("state") != state:
        raise RuntimeError("sign-in failed: the redirect state does not match this request")
    code = result.get("code") or ""
    if not code:
        raise RuntimeError("sign-in failed: the redirect carried no authorization code")
    fields = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": endpoints.client_id,
        "code_verifier": verifier,
    }
    if endpoints.json_body:
        fields["state"] = state
    payload = token_request(endpoints, fields, post)
    return credential_from_response(provider, payload)


__all__ = [
    "parse_pasted_redirect","LoopbackCallback", "authorize_url", "login", "pkce_pair"]
