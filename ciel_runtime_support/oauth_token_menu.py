"""Prelaunch menu panel for this workspace's rotating Codex/Claude OAuth tokens.

The panel drives the same operations as ``ciel-runtime tokens`` so the menu
and the CLI can never disagree about what a token add, import or removal does.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from functools import partial

from ciel_runtime_support import oauth_login
from ciel_runtime_support.oauth_token_cli import default_import_path, run_tokens_command
from ciel_runtime_support.oauth_token_store import STATUS_ACTIVE, OAuthTokenStore

Prompt = Callable[[str, str], str]

_PROVIDER_LABELS = {"codex": "Codex", "claude": "Claude"}
TOKEN_ACTIONS = ("disable", "enable", "refresh", "remove")


def _token_row(token, now: float) -> str:
    state = token.status
    if token.status == STATUS_ACTIVE and token.exhausted_until > now:
        state = "limited"
    elif token.status == STATUS_ACTIVE and token.draining:
        state = "draining"
    usage = ", ".join(
        f"{name} {window.get('used_percent', 0):g}%" for name, window in token.usage.items()
    ) or "no usage yet"
    label = token.label or token.email or token.token_id
    return f"{_PROVIDER_LABELS.get(token.provider, token.provider)}  {label}  [{state} · {usage}]  {token.token_id}"


def token_count(workspace_state_dir: Path) -> int:
    try:
        return len(OAuthTokenStore(workspace_state_dir).snapshot().tokens)
    except Exception:
        return 0


def panel_rows(workspace_state_dir: Path) -> tuple[list[str], list[str]]:
    rows: list[str] = []
    values: list[str] = []
    try:
        tokens = OAuthTokenStore(workspace_state_dir).snapshot().tokens
    except Exception as exc:
        tokens = []
        rows.append(f"Token store unreadable: {type(exc).__name__}: {exc}")
        values.append("__info__")
    now = time.time()
    for token in tokens:
        rows.append(_token_row(token, now))
        values.append(f"token:{token.token_id}")
    if not tokens:
        rows.append("No tokens yet. Stored tokens rotate in Codex routed and Anthropic routed modes.")
        values.append("__info__")
    for provider, name in _PROVIDER_LABELS.items():
        rows.append(f"+ Sign in {name} (browser)")
        values.append(f"login:{provider}")
    for provider, name in _PROVIDER_LABELS.items():
        rows.append(f"+ Import {name} ({default_import_path(provider)})")
        values.append(f"import:{provider}")
    rows.append("Back")
    values.append("back")
    return rows, values


def apply(
    value: str,
    workspace_state_dir: Path,
    prompt: Prompt,
    *,
    output: Callable[[str], None] = print,
    run: Callable[..., int] = run_tokens_command,
) -> list[str]:
    """Run the selected panel action; return the messages to show."""

    messages: list[str] = []

    def collect(text: str) -> None:
        # The panel renders one message per row; the CLI's notes span lines.
        messages.extend(line for line in str(text).splitlines() if line.strip())

    try:
        _apply(value, workspace_state_dir, prompt, output, run, collect)
    except SystemExit as exc:
        # run_tokens_command reports user errors (unknown id, bad file) this way.
        messages.append(str(exc.code) if exc.code not in (None, 0) else "Done.")
    except Exception as exc:
        messages.append(f"OAuth token action failed: {type(exc).__name__}: {exc}")
    return messages


def _apply(
    value: str,
    workspace_state_dir: Path,
    prompt: Prompt,
    output: Callable[[str], None],
    run: Callable[..., int],
    collect: Callable[[str], None],
) -> None:
    kind, _, subject = value.partition(":")
    if kind == "login":
        label = prompt(f"Label for the new {_PROVIDER_LABELS[subject]} token (blank uses the account email)", "")
        # The browser sign-in prints its URL and waits for the callback, so
        # its progress goes straight to the terminal instead of the panel.
        args = ["login", subject, *(["--label", label] if label else [])]
        login = partial(
            oauth_login.login,
            redirect_input=lambda: prompt(
                "Press Enter once the browser says you are signed in, or paste the address it ended on (remote machine)",
                "",
            ),
        )
        run(args, workspace_state_dir, output=lambda line: (output(line), collect(line)), login=login)
    elif kind == "import":
        path = prompt(f"{_PROVIDER_LABELS[subject]} credentials file", str(default_import_path(subject)))
        if not path:
            return
        label = prompt("Label (blank uses the account email or file name)", "")
        args = ["import", subject, "--from", path, *(["--label", label] if label else [])]
        run(args, workspace_state_dir, output=collect)
    elif kind == "token":
        action = prompt(f"{subject}: {' / '.join(TOKEN_ACTIONS)}", "").strip().lower()
        if not action:
            return
        if action not in TOKEN_ACTIONS:
            collect(f"Unknown action {action!r}; expected one of {', '.join(TOKEN_ACTIONS)}.")
            return
        run([action, subject], workspace_state_dir, output=collect)


__all__ = ["TOKEN_ACTIONS", "apply", "panel_rows", "token_count"]
