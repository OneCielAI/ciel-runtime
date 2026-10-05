"""Keep an imported OAuth token and the CLI credential file it came from in step.

A token imported from Claude Code's ``.credentials.json`` or Codex's
``auth.json`` is one sign-in shared by two refreshers: the CLI and the router.
Refresh tokens rotate, so whichever side refreshes second presents a refresh
token the provider already replaced.  On sarah-ai (2026-10-04) the router
refreshed the imported Claude token at 10:57 UTC; at 11:02 Claude Code's
credential file was empty and every turn answered "Login expired · Please run
/login" while the router still held a working token.

So inside the per-token refresh lock the refresher first adopts a credential
the CLI refreshed on its own (the file's refresh token differs from the
stored one), and after its own refresh writes the new tokens back into the
file, keeping every other field the CLI stores there.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from ciel_runtime_support.oauth_token_endpoints import import_claude_credentials, import_codex_auth
from ciel_runtime_support.oauth_token_store import OAuthCredential, OAuthTokenState

IMPORT_SOURCE_PREFIX = "import:"


def source_path(token: OAuthTokenState) -> Path | None:
    source = str(token.source or "")
    if not source.startswith(IMPORT_SOURCE_PREFIX):
        return None
    path = source[len(IMPORT_SOURCE_PREFIX):].strip()
    return Path(path) if path else None


def read_source(token: OAuthTokenState) -> OAuthCredential | None:
    """The credential the CLI holds now, or None when it has no usable one."""

    path = source_path(token)
    if path is None or token.provider not in ("claude", "codex"):
        return None
    try:
        if token.provider == "claude":
            credential, _email = import_claude_credentials(path)
        else:
            credential, _email = import_codex_auth(path)
    except (OSError, ValueError):
        return None
    return credential if credential.access_token and credential.refresh_token else None


def newer_in_source(token: OAuthTokenState, stored: OAuthCredential) -> OAuthCredential | None:
    """The CLI's credential when the CLI refreshed since the router last did."""

    current = read_source(token)
    if current is None or current.refresh_token == stored.refresh_token:
        return None
    return current


def write_source(token: OAuthTokenState, credential: OAuthCredential, *, now: float) -> bool:
    """Write refreshed tokens into the CLI's file; False when there is none to update."""

    path = source_path(token)
    if path is None or token.provider not in ("claude", "codex"):
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(value, dict):
        return False
    if token.provider == "claude":
        if not _update_claude(value, credential):
            return False
    elif not _update_codex(value, credential, now):
        return False
    _replace_file(path, json.dumps(value, indent=2) + "\n")
    return True


def _update_claude(value: dict[str, Any], credential: OAuthCredential) -> bool:
    oauth = value.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return False
    oauth["accessToken"] = credential.access_token
    oauth["refreshToken"] = credential.refresh_token
    if credential.expires_at:
        oauth["expiresAt"] = int(credential.expires_at * 1000)
    if credential.scopes:
        oauth["scopes"] = list(credential.scopes)
    return True


def _update_codex(value: dict[str, Any], credential: OAuthCredential, now: float) -> bool:
    tokens = value.get("tokens")
    if not isinstance(tokens, dict):
        return False
    tokens["access_token"] = credential.access_token
    tokens["refresh_token"] = credential.refresh_token
    if credential.id_token:
        tokens["id_token"] = credential.id_token
    if credential.account_id:
        tokens["account_id"] = credential.account_id
    value["last_refresh"] = datetime.fromtimestamp(now, timezone.utc).isoformat().replace("+00:00", "Z")
    return True


def _replace_file(path: Path, text: str) -> None:
    """Atomic replace that keeps the file private (both CLIs write 0600)."""

    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = 0o600
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        if os.name != "nt":
            os.chmod(temp, mode or 0o600)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


__all__ = [
    "IMPORT_SOURCE_PREFIX",
    "newer_in_source",
    "read_source",
    "source_path",
    "write_source",
]
