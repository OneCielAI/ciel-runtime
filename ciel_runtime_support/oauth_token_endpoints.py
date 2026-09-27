"""OAuth endpoints, token responses and credential files of Codex and Claude.

Constants are the ones the installed CLIs use (codex.exe, claude.exe; checked
2026-09-27): Codex signs in at auth.openai.com with client
``app_EMoamEEZ73f0CkXaXp7hrann`` and a ``localhost:1455/auth/callback``
redirect; Claude Code authorizes at claude.com/cai/oauth/authorize with client
``9d1c250a-e61b-44d9-88ed-5944d1962f5e``, a ``localhost:<port>/callback``
redirect, and exchanges and refreshes JSON bodies at
platform.claude.com/v1/oauth/token.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

from ciel_runtime_support.oauth_token_store import OAuthCredential


@dataclass(frozen=True, slots=True)
class OAuthProviderEndpoints:
    provider: str
    client_id: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    # Form-encoded (Codex) or JSON (Claude) token requests.
    json_body: bool
    callback_port: int  # 0 picks a free port
    callback_path: str
    extra_authorize: tuple[tuple[str, str], ...] = ()


CODEX = OAuthProviderEndpoints(
    provider="codex",
    client_id="app_EMoamEEZ73f0CkXaXp7hrann",
    authorize_url="https://auth.openai.com/oauth/authorize",
    token_url="https://auth.openai.com/oauth/token",
    scopes=("openid", "profile", "email", "offline_access"),
    json_body=False,
    callback_port=1455,
    callback_path="/auth/callback",
    extra_authorize=(
        ("id_token_add_organizations", "true"),
        ("codex_cli_simplified_flow", "true"),
        ("originator", "codex_cli_rs"),
        # A fresh login for every account: reusing the browser session would
        # hand out a second token family for the account already stored.
        ("prompt", "login"),
    ),
)

CLAUDE = OAuthProviderEndpoints(
    provider="claude",
    client_id="9d1c250a-e61b-44d9-88ed-5944d1962f5e",
    authorize_url="https://claude.com/cai/oauth/authorize",
    token_url="https://platform.claude.com/v1/oauth/token",
    scopes=("user:profile", "user:inference", "user:sessions:claude_code", "user:mcp_servers"),
    json_body=True,
    callback_port=0,
    callback_path="/callback",
    extra_authorize=(("code", "true"), ("prompt", "login")),
)

ENDPOINTS = {"codex": CODEX, "claude": CLAUDE}

# Refresh errors that mean the stored refresh token can never work again.
TERMINAL_REFRESH_ERRORS = (
    "invalid_grant",
    "refresh_token_reused",
    "refresh_token_expired",
    "refresh_token_invalidated",
    "invalid_token",
    "token_expired",
)


class OAuthHttpError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body

    @property
    def terminal(self) -> bool:
        text = self.body.lower()
        return self.status in (400, 401) and any(code in text for code in TERMINAL_REFRESH_ERRORS)


HttpPost = Callable[[str, bytes, dict[str, str], float], tuple[int, bytes]]


def urllib_post(url: str, data: bytes, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def token_url(endpoints: OAuthProviderEndpoints) -> str:
    """The token endpoint; ``CIEL_RUNTIME_OAUTH_TOKEN_URL_<PROVIDER>`` moves it (E2E)."""

    return os.environ.get(f"CIEL_RUNTIME_OAUTH_TOKEN_URL_{endpoints.provider.upper()}") or endpoints.token_url


def token_request(endpoints: OAuthProviderEndpoints, fields: dict[str, str], post: HttpPost = urllib_post) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "ciel-runtime"}
    if endpoints.json_body:
        body = json.dumps(fields).encode("utf-8")
        headers["Content-Type"] = "application/json"
    else:
        body = urllib.parse.urlencode(fields).encode("ascii")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    status, raw = post(token_url(endpoints), body, headers, 30.0)
    text = raw.decode("utf-8", "replace")
    if status != 200:
        raise OAuthHttpError(status, text)
    value = json.loads(text)
    if not isinstance(value, dict) or not value.get("access_token"):
        raise OAuthHttpError(status, "token response has no access_token")
    return value


def refresh_fields(endpoints: OAuthProviderEndpoints, credential: OAuthCredential) -> dict[str, str]:
    fields = {"grant_type": "refresh_token", "refresh_token": credential.refresh_token, "client_id": endpoints.client_id}
    if endpoints.json_body:
        fields["scope"] = " ".join(credential.scopes or endpoints.scopes)
    return fields


def jwt_claims(token: str) -> dict[str, Any]:
    """The unverified payload of a JWT; {} for anything else."""

    parts = str(token or "").split(".")
    if len(parts) != 3:
        return {}
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _codex_account(id_token: str, access_token: str) -> tuple[str, str]:
    for token in (id_token, access_token):
        claims = jwt_claims(token)
        auth = claims.get("https://api.openai.com/auth")
        auth = auth if isinstance(auth, dict) else {}
        profile = claims.get("https://api.openai.com/profile")
        profile = profile if isinstance(profile, dict) else {}
        account = str(auth.get("chatgpt_account_id") or "")
        email = str(claims.get("email") or profile.get("email") or "")
        if account or email:
            return account, email
    return "", ""


def credential_from_response(
    provider: str,
    payload: dict[str, Any],
    previous: OAuthCredential | None = None,
    *,
    now: float | None = None,
) -> tuple[OAuthCredential, str]:
    """Credential and account email from a token endpoint response."""

    now = time.time() if now is None else now
    access = str(payload.get("access_token") or "")
    refresh = str(payload.get("refresh_token") or (previous.refresh_token if previous else ""))
    id_token = str(payload.get("id_token") or (previous.id_token if previous else ""))
    expires_in = payload.get("expires_in")
    expires_at = now + float(expires_in) if isinstance(expires_in, (int, float)) else 0.0
    scopes = tuple(str(payload.get("scope") or "").split()) or (previous.scopes if previous else ())
    email = ""
    account_id = previous.account_id if previous else ""
    if provider == "codex":
        found_account, email = _codex_account(id_token, access)
        account_id = found_account or account_id
        expires_at = float(jwt_claims(access).get("exp") or 0) or expires_at
    else:
        account = payload.get("account")
        if isinstance(account, dict):
            email = str(account.get("email_address") or account.get("email") or "")
    credential = OAuthCredential(access, refresh, id_token, account_id, expires_at, scopes)
    return credential, email


def import_codex_auth(path: Path) -> tuple[OAuthCredential, str]:
    """Credential from a Codex ``auth.json`` (ChatGPT sign-in)."""

    value = json.loads(path.read_text(encoding="utf-8"))
    tokens = value.get("tokens") if isinstance(value, dict) else None
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise ValueError(f"{path} has no ChatGPT tokens (was Codex signed in with ChatGPT?)")
    access = str(tokens.get("access_token") or "")
    id_token = str(tokens.get("id_token") or "")
    account, email = _codex_account(id_token, access)
    credential = OAuthCredential(
        access_token=access,
        refresh_token=str(tokens.get("refresh_token") or ""),
        id_token=id_token,
        account_id=str(tokens.get("account_id") or account),
        expires_at=float(jwt_claims(access).get("exp") or 0),
    )
    return credential, email


def import_claude_credentials(path: Path) -> tuple[OAuthCredential, str]:
    """Credential from a Claude Code ``.credentials.json``."""

    value = json.loads(path.read_text(encoding="utf-8"))
    oauth = value.get("claudeAiOauth") if isinstance(value, dict) else None
    if not isinstance(oauth, dict) or not oauth.get("accessToken"):
        raise ValueError(f"{path} has no claudeAiOauth tokens (was Claude Code signed in with a subscription?)")
    expires = float(oauth.get("expiresAt") or 0)
    credential = OAuthCredential(
        access_token=str(oauth.get("accessToken") or ""),
        refresh_token=str(oauth.get("refreshToken") or ""),
        expires_at=expires / 1000.0 if expires > 1e12 else expires,
        scopes=tuple(str(scope) for scope in oauth.get("scopes") or ()),
    )
    return credential, ""


__all__ = [
    "CLAUDE",
    "CODEX",
    "ENDPOINTS",
    "OAuthHttpError",
    "OAuthProviderEndpoints",
    "credential_from_response",
    "import_claude_credentials",
    "import_codex_auth",
    "jwt_claims",
    "refresh_fields",
    "token_request",
    "token_url",
    "urllib_post",
]
