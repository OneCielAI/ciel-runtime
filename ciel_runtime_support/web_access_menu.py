"""Prelaunch menu panel for web access: admin API token rotation and web accounts.

The same stores back the web admin page (web_access_http), so an account
added or a token rotated here is what a remote browser or API client sees.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from ciel_runtime_support.web_accounts import WebAccountError, WebAccountStore

Prompt = Callable[[str, str], str]
SecretPrompt = Callable[[str], str]
ACCOUNT_ACTIONS = ("reset", "remove")


def default_token_repository() -> Any:
    from ciel_runtime_support.router_access import RouterExternalTokenRepository
    from ciel_runtime_support.runtime_paths import CONFIG_DIR, ROUTER_EXTERNAL_TOKEN_PATH

    return RouterExternalTokenRepository(path=ROUTER_EXTERNAL_TOKEN_PATH, config_dir=CONFIG_DIR, environ=os.environ)


def default_accounts() -> WebAccountStore:
    from ciel_runtime_support.web_access_http import default_accounts as accounts

    return accounts()


def summary(accounts: WebAccountStore) -> str:
    try:
        count = len(accounts.list_accounts())
    except Exception:
        count = 0
    return f"{count} account{'s' if count != 1 else ''} · admin token"


def panel_rows(accounts: WebAccountStore, token_repository: Any) -> tuple[list[str], list[str]]:
    rows: list[str] = []
    values: list[str] = []
    token = ""
    try:
        token = token_repository.get()
    except Exception:
        pass
    rows.append(f"Rotate admin API token  [{'…' + token[-4:] if token else 'none yet'}]")
    values.append("rotate")
    for account in accounts.list_accounts():
        rows.append(f"{account['email']}  [{account['active_sessions']} session(s)]")
        values.append(f"account:{account['email']}")
    rows.append("+ Add web account")
    values.append("add")
    rows.append("Sign out every web session")
    values.append("revoke")
    rows.append("Back")
    values.append("back")
    return rows, values


def _new_password(secret_prompt: SecretPrompt) -> str:
    first = secret_prompt("New password (8+ characters)")
    if not first:
        return ""
    if secret_prompt("Repeat the password") != first:
        raise WebAccountError("The passwords do not match.")
    return first


def apply(
    value: str,
    accounts: WebAccountStore,
    token_repository: Any,
    prompt: Prompt,
    secret_prompt: SecretPrompt,
) -> list[str]:
    try:
        return _apply(value, accounts, token_repository, prompt, secret_prompt)
    except (WebAccountError, RuntimeError, OSError) as exc:
        return [str(exc)]


def _apply(
    value: str,
    accounts: WebAccountStore,
    token_repository: Any,
    prompt: Prompt,
    secret_prompt: SecretPrompt,
) -> list[str]:
    kind, _, subject = value.partition(":")
    if kind == "rotate":
        if prompt("Rotate the admin API token? Remote clients using the old one stop working (yes/no)", "no").lower() not in ("y", "yes"):
            return ["Admin token unchanged."]
        token = token_repository.rotate()
        return ["New admin API token (shown once; send it as Authorization: Bearer <token>):", token]
    if kind == "add":
        email = prompt("Email for the new web account", "")
        if not email:
            return []
        password = _new_password(secret_prompt)
        if not password:
            return []
        return [f"Added web account {accounts.add(email, password)}."]
    if kind == "revoke":
        return [f"Signed out {accounts.revoke_all_sessions()} web session(s)."]
    if kind == "account":
        action = prompt(f"{subject}: {' / '.join(ACCOUNT_ACTIONS)}", "").strip().lower()
        if not action:
            return []
        if action == "reset":
            password = _new_password(secret_prompt)
            if not password:
                return []
            return [f"Password reset for {accounts.set_password(subject, password)}; its sessions were signed out."]
        if action == "remove":
            return [f"Removed web account {accounts.remove(subject)}."]
        return [f"Unknown action {action!r}; expected one of {', '.join(ACCOUNT_ACTIONS)}."]
    return []


__all__ = ["ACCOUNT_ACTIONS", "apply", "default_accounts", "default_token_repository", "panel_rows", "summary"]

