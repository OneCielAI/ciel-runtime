"""Prelaunch menu panel for session API keys (memory only, one session and provider).

It is a client of this workspace's router, like ``ciel-runtimectl
session-api-key``: every action goes through ``SessionApiKeyClient`` (loopback,
``/health`` workspace check, admin token).  A key is read only through the
menu's hidden prompt and goes straight into the request body; nothing here
stores, prints or logs it.  Stored API keys keep their own (persistent) menu
entry.  When no router is running there is nothing to inject into, and the
panel says so.
"""

from __future__ import annotations

import secrets
import time
from typing import Any, Callable

from .session_api_key_client import SessionApiKeyClient, SessionApiKeyClientError

Prompt = Callable[[str, str], str]
SecretPrompt = Callable[[str], str]


def _when(value: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(value)))
    except (TypeError, ValueError):
        return "-"


def summary(state: dict[str, Any] | None) -> str:
    if not state:
        return "router not running"
    health = state.get("health") or {}
    if health.get("state_unreadable"):
        return "marker unreadable: reset required"
    return f"{len(state.get('keys') or [])} key(s) · {health.get('rows', 0)} of {health.get('limit', 0)} records"


def panel_rows(state: dict[str, Any] | None, error: str = "") -> tuple[list[str], list[str]]:
    rows: list[str] = []
    values: list[str] = []
    if state is None:
        rows.append(f"Router not reachable: {error or 'start a session first'}")
        values.append("__info__")
        rows.append("Back")
        values.append("back")
        return rows, values
    health = state.get("health") or {}
    for notice in state.get("notices") or []:
        rows.append(f"! {notice}")
        values.append("__info__")
    if health.get("state_unreadable"):
        rows.append("Reset session-key state (confirmation required)")
        values.append("reset")
    rows.append(f"Records: {health.get('rows', 0)} of {health.get('limit', 0)}")
    values.append("__info__")
    for row in (state.get("keys") or []) + (state.get("losses") or []):
        notice = f" · {row['notice']} (age {int(row.get('age_seconds') or 0)}s)" if row.get("notice") else ""
        rows.append(f"{row.get('session')} · {row.get('provider')} · {row.get('state', 'active')} · "
                    f"v{row.get('version')} · {row.get('source')} · {row.get('fallback', 'fail_closed')}{notice}")
        values.append(f"key:{row.get('session')}|{row.get('provider')}|{row.get('version')}")
    for row in state.get("staged") or []:
        rows.append(f"staged, awaiting first request · {row.get('provider')} · expires {_when(row.get('expires_at'))}")
        values.append("__info__")
    for row in state.get("abandoned") or []:
        rows.append(f"abandoned: scope rebound by another launch · {row.get('provider')} · {_when(row.get('abandoned_at'))}")
        values.append("__info__")
    rows.append("+ Inject a session key (hidden prompt)")
    values.append("inject")
    rows.append("Back")
    values.append("back")
    return rows, values


def load(client: Callable[[], SessionApiKeyClient] | None) -> tuple[dict[str, Any] | None, str]:
    if client is None:
        return None, "no router client is wired into this menu"
    try:
        return client().state(), ""
    except SessionApiKeyClientError as exc:
        return None, f"{exc.code}: {exc}"


def _choose_session(state: dict[str, Any], prompt: Prompt) -> str:
    sessions = [str(row.get("session") or "") for row in state.get("sessions") or []]
    listing = ", ".join(f"{index + 1}={session}" for index, session in enumerate(sessions)) or "none listed"
    answer = prompt(f"Session ({listing}); number or claude:<id>", "1" if sessions else "").strip()
    if answer.isdigit() and 1 <= int(answer) <= len(sessions):
        return sessions[int(answer) - 1]
    return answer


def apply(value: str, client: Callable[[], SessionApiKeyClient] | None, prompt: Prompt,
          secret_prompt: SecretPrompt) -> list[str]:
    if client is None:
        return ["No router client is wired into this menu; nothing changed."]
    try:
        return _apply(value, client, prompt, secret_prompt)
    except SessionApiKeyClientError as exc:
        return [f"Session key {exc.code}: {exc}"]


def _apply(value: str, client: Callable[[], SessionApiKeyClient], prompt: Prompt,
           secret_prompt: SecretPrompt) -> list[str]:
    operation = secrets.token_hex(12)
    if value == "reset":
        state = client().state()
        reset = state.get("reset") or {}
        phrase = str(reset.get("confirm") or "")
        typed = prompt(f"{reset.get('warning') or ''} Type {phrase} to reset", "")
        if typed.strip() != phrase:
            return ["Not confirmed; nothing changed."]
        result = client().action({"action": "reset_state", "source": "menu", "confirm": phrase}, operation)
        return [f"Session-key state reset (was unreadable: {result.get('was_unreadable')}); "
                "sessions it blocked now use their stored key."]
    if value == "inject":
        state = client().state()
        session = _choose_session(state, prompt)
        if not session:
            return []
        provider = prompt("Provider", str(state.get("current_provider") or "")).strip()
        default_ttl = str((state.get("ttl_seconds") or {}).get("default") or 86400)
        ttl = prompt("TTL seconds", default_ttl).strip() or default_ttl
        fallback = prompt("When the key is lost: block or stored", "block").strip().lower()
        key = secret_prompt("Session API key (hidden)")
        if not key:
            return ["No key given; nothing changed."]
        body = {"action": "inject", "session": session, "provider": provider, "source": "menu",
                "ttl_seconds": int(ttl) if ttl.isdigit() else ttl, "expected_version": 0,
                "fallback": "stored" if fallback == "stored" else "fail_closed", "api_key": key}
        key = ""
        try:
            result = client().action(body, operation)
        finally:
            body["api_key"] = ""
        row = result.get("key") or {}
        return [f"Injected for {row.get('session')} · {row.get('provider')} · v{row.get('version')} "
                "(memory only; the stored key is unchanged)."]
    kind, _, subject = value.partition(":")
    if kind == "key":
        session, provider, version = (subject.split("|") + ["", "", ""])[:3]
        action = prompt(f"{session} · {provider}: replace / clear", "").strip().lower()
        if action == "clear":
            if prompt("Clear it? The session then uses its stored key (yes/no)", "no").strip().lower() not in ("y", "yes"):
                return ["Not cleared."]
            client().action({"action": "clear", "session": session, "provider": provider, "source": "menu",
                             "expected_version": int(version or 0)}, operation)
            return [f"Cleared {session} · {provider}; the stored key is in use."]
        if action == "replace":
            key = secret_prompt("New session API key (hidden)")
            if not key:
                return ["No key given; nothing changed."]
            body = {"action": "replace", "session": session, "provider": provider, "source": "menu",
                    "expected_version": int(version or 0), "api_key": key}
            key = ""
            try:
                result = client().action(body, operation)
            finally:
                body["api_key"] = ""
            return [f"Replaced {session} · {provider} · v{(result.get('key') or {}).get('version')}."]
        return [] if not action else [f"Unknown action {action!r}; expected replace or clear."]
    return []


__all__ = ["apply", "load", "panel_rows", "summary"]
