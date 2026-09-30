"""``ciel-runtime tokens``: manage this workspace's Codex and Claude OAuth tokens."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import time
from typing import Callable

from ciel_runtime_support.oauth_token_endpoints import import_claude_credentials, import_codex_auth
from ciel_runtime_support.oauth_token_refresh import OAuthTokenRefresher
from ciel_runtime_support.oauth_token_store import (
    PROVIDERS,
    STATUS_ACTIVE,
    STATUS_DISABLED,
    OAuthTokenState,
    OAuthTokenStore,
)

USAGE = """Usage: ciel-runtime tokens <command>

  list [--json]                     tokens of this workspace with usage and status
  login codex|claude [--label L]    sign in with the browser and store a new token
  import codex|claude [--from PATH] [--label L]
                                    store the token of a signed-in CLI
                                    (default ~/.codex/auth.json or ~/.claude/.credentials.json)
  refresh ID                        refresh a token now
  enable ID | disable ID            put a token in or out of rotation
  remove ID                         delete a token from this workspace

Stored tokens are used only in the Codex routed and Anthropic routed modes."""

REDIRECT_PROMPT = (
    "Press Enter once the browser says you are signed in. If the browser runs on another "
    "machine and ends on a page that cannot load, paste that page's address here: "
)

IMPORT_WARNING = (
    "Note: the imported token is the one the {cli} CLI keeps using and refreshing.\n"
    "If both refresh it, the provider can revoke one side{detail}.\n"
    "Use `ciel-runtime tokens login {provider}` for a token of its own."
)


def default_import_path(provider: str) -> Path:
    if provider == "codex":
        home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
        return Path(home) / "auth.json"
    home = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(home) / ".credentials.json"


def _option(args: list[str], name: str) -> str:
    for index, item in enumerate(args):
        if item == name and index + 1 < len(args):
            return args[index + 1]
        if item.startswith(name + "="):
            return item.split("=", 1)[1]
    return ""


def _provider(args: list[str]) -> str:
    provider = args[0] if args else ""
    if provider not in PROVIDERS:
        raise SystemExit(f"Expected a provider: {' | '.join(PROVIDERS)}\n\n{USAGE}")
    return provider


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"
    return f"{seconds // 86400}d{seconds % 86400 // 3600}h"


def describe(token: OAuthTokenState, now: float) -> str:
    usage = ", ".join(
        f"{name} {window.get('used_percent', 0):g}%"
        + (f" (reset in {_ago(window['reset_at'] - now)})" if window.get("reset_at") else "")
        for name, window in token.usage.items()
    ) or "no usage seen yet"
    state = token.status
    if token.status == STATUS_ACTIVE and token.exhausted_until > now:
        state = f"limited, back in {_ago(token.exhausted_until - now)}"
    elif token.status == STATUS_ACTIVE and token.draining:
        state = "draining"
    expiry = f"expires in {_ago(token.expires_at - now)}" if token.expires_at > now else ("expired" if token.expires_at else "")
    who = f" <{token.email}>" if token.email else ""
    lines = [f"{token.token_id}  {token.provider}  {token.label}{who}", f"    {state}; {usage}"]
    if expiry:
        lines[-1] += f"; access token {expiry}"
    if token.last_error:
        lines.append(f"    last error: {token.last_error}")
    return "\n".join(lines)


def run_tokens_command(
    args: list[str],
    workspace_state_dir: Path,
    *,
    output: Callable[[str], None] = print,
    login: Callable[..., tuple] | None = None,
) -> int:
    store = OAuthTokenStore(workspace_state_dir)
    command, rest = (args[0], args[1:]) if args else ("list", [])
    if command in ("-h", "--help", "help"):
        output(USAGE)
        return 0
    if command == "list":
        snapshot = store.snapshot()
        if "--json" in rest:
            output(json.dumps({"tokens": [asdict(token) for token in snapshot.tokens]}, indent=2))
            return 0
        if not snapshot.tokens:
            output(f"No OAuth tokens in this workspace ({workspace_state_dir}).\n\n{USAGE}")
            return 0
        now = time.time()
        output("\n".join(describe(token, now) for token in snapshot.tokens))
        return 0
    if command == "login":
        provider = _provider(rest)
        if login is None:
            from functools import partial
            import sys

            from ciel_runtime_support.oauth_login import login as login_flow

            # Interactive: accept the pasted redirect so a remote sign-in works.
            login = partial(login_flow, redirect_input=lambda: input(REDIRECT_PROMPT)) if sys.stdin.isatty() else login_flow
        credential, email = login(provider, output=output)
        token = store.add(provider, credential, label=_option(rest, "--label") or email, email=email, source="login")
        output(f"Stored {token.token_id} ({email or provider}).")
        return 0
    if command == "import":
        provider = _provider(rest)
        path = Path(_option(rest, "--from") or default_import_path(provider)).expanduser()
        reader = import_codex_auth if provider == "codex" else import_claude_credentials
        credential, email = reader(path)
        token = store.add(provider, credential, label=_option(rest, "--label") or email or path.name, email=email, source=f"import:{path}")
        output(f"Stored {token.token_id} from {path}.")
        output(
            IMPORT_WARNING.format(
                cli="Codex" if provider == "codex" else "Claude Code",
                detail=" (Codex refresh tokens are single use)" if provider == "codex" else "",
                provider=provider,
            )
        )
        return 0
    token_id = rest[0] if rest else ""
    if command in ("remove", "enable", "disable", "refresh") and not token_id:
        raise SystemExit(f"Expected a token id.\n\n{USAGE}")
    if command == "remove":
        if not store.remove(token_id):
            raise SystemExit(f"No token {token_id} in this workspace.")
        output(f"Removed {token_id}.")
        return 0
    if command in ("enable", "disable"):
        with store.transaction() as snapshot:
            token = snapshot.get(token_id)
            if token is None:
                raise SystemExit(f"No token {token_id} in this workspace.")
            token.status = STATUS_ACTIVE if command == "enable" else STATUS_DISABLED
            if command == "enable":
                token.last_error = ""
        output(f"{token_id} {command}d.")
        return 0
    if command == "refresh":
        outcome = OAuthTokenRefresher(store).refresh(token_id, force=True)
        output(f"{token_id}: {outcome.detail}")
        return 0 if outcome.refreshed else 1
    raise SystemExit(USAGE)


__all__ = ["REDIRECT_PROMPT", "USAGE", "default_import_path", "describe", "run_tokens_command"]
