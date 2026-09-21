"""Muse Code account login for the credential menu.

Muse keeps its Meta credential in ``~/.config/muse/auth.json``
(``{"schema_version": 1, "providers": {...}}``; inside WSL on Windows). Muse's
own ``muse login`` runs the device flow - approve a code in the browser - and
stores the token there; ``muse logout`` removes it, and its help text states
that ``META_API_KEY`` always takes priority over the stored account login. The
API-key menu offers login, status and logout here so the token can be stored
*before* the first session instead of during it; the launch itself keeps
stripping ``META_API_KEY`` for this flow (``env -u`` in the WSL prefix) so the
device login is never shadowed by the injected key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

MUSE_CREDENTIAL_RELATIVE = ".config/muse/auth.json"
MUSE_CREDENTIAL_ACTIONS = ("login", "logout", "status")
# Launcher-side WSL calls must not hang on a wedged mount (see muse_mcp).
WSL_CREDENTIAL_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True, slots=True)
class MuseCredentialStatus:
    configured: bool
    providers: tuple[str, ...] = ()
    error: str = ""

    def label(self) -> str:
        if self.error:
            return f"unknown ({self.error})"
        if not self.configured:
            return "login required"
        return "connected: " + ", ".join(self.providers or ("meta",))


def credential_status_from_text(text: str | None) -> MuseCredentialStatus:
    """Parse ``auth.json`` text; unreadable content never raises."""

    normalized = str(text or "").strip()
    if not normalized:
        return MuseCredentialStatus(False)
    try:
        payload = json.loads(normalized)
    except ValueError:
        return MuseCredentialStatus(False, error="unreadable credential file")
    if not isinstance(payload, Mapping):
        return MuseCredentialStatus(False, error="unreadable credential file")
    providers = payload.get("providers")
    names = (
        tuple(sorted(str(name) for name in providers))
        if isinstance(providers, Mapping)
        else ()
    )
    return MuseCredentialStatus(bool(names), names)


def credential_argv(executable: Any, action: str) -> list[str]:
    """``muse login|logout`` through the same launcher prefix the runtime uses."""

    argv = [str(getattr(executable, "command", "") or "")]
    argv.extend(str(value) for value in getattr(executable, "prefix_args", ()))
    argv.append(str(action))
    return argv


def read_credential_text(
    executable: Any,
    *,
    run: Callable[..., Any],
    timeout: float = WSL_CREDENTIAL_TIMEOUT_SECONDS,
) -> str:
    """Read Muse's credential file from wherever Muse actually runs."""

    if str(getattr(executable, "platform", "") or "").lower() == "wsl":
        wsl = str(getattr(executable, "command", "") or "")
        if not wsl:
            return ""
        result = run(
            [
                wsl,
                "-e",
                "sh",
                "-lc",
                f"cat $HOME/{MUSE_CREDENTIAL_RELATIVE} 2>/dev/null || true",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(Path.home()),
            timeout=timeout,
        )
        return str(getattr(result, "stdout", "") or "")
    try:
        return (Path.home() / MUSE_CREDENTIAL_RELATIVE).read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return ""


def muse_oauth_action(
    action: str,
    *,
    discover: Callable[[], Any],
    run: Callable[..., Any],
    call: Callable[[Sequence[str]], int],
    print_line: Callable[..., None] = print,
) -> list[str]:
    """Run one account action for the menu and report the resulting status."""

    if action not in MUSE_CREDENTIAL_ACTIONS:
        return [f"unknown Muse account action {action!r}"]
    executable = discover()
    if executable is None:
        return [
            "Muse Code was not found (or WSL did not answer), so the account "
            "login cannot run yet."
        ]
    argv = credential_argv(executable, action)
    if action in {"login", "logout"}:
        print_line("Running: " + " ".join(argv), flush=True)
        code = int(call(argv))
        if code != 0:
            return [f"muse {action} exited with code {code}."]
    status = credential_status_from_text(read_credential_text(executable, run=run))
    verb = "logout" if action == "logout" else "login"
    return [f"Muse account {verb}: {status.label()}."]


__all__ = [
    "MUSE_CREDENTIAL_ACTIONS",
    "MUSE_CREDENTIAL_RELATIVE",
    "MuseCredentialStatus",
    "credential_argv",
    "credential_status_from_text",
    "muse_oauth_action",
    "read_credential_text",
]
