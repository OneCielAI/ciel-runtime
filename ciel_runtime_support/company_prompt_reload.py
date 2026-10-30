"""Restart the session when Walkie announces another company prompt (Walkie 53255).

Walkie events carry ``data.agent_context.company_prompt = {version, sha8,
source}`` (checked 2026-10-14).  When that differs from the company prompt
this workspace's instruction files were written with, the router queues a
resumed session restart that waits for the running turn however long it
takes (``wait_turn``); the launcher fetches the instruction files again just
before it relaunches the CLI (``runtime_launch.prepare_restart``), so the
conversation continues on the new prompt.  One request per announced prompt
per retry window keeps a prompt the instruction endpoint does not serve yet
from restarting the session over and over.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ciel_runtime_support import runtime_input_gateway
from ciel_runtime_support.remote_instructions import auto_reload_enabled, held_company_prompt

COMPANY_PROMPT_RESTART_SOURCE = "company-prompt"
RETRY_SECONDS = 1800.0
STATE_FILE = "company-prompt-reload.json"

Log = Callable[[str, str], None]


def announced_company_prompt(raw_event: str) -> dict[str, str]:
    """``{version, sha8}`` from a Walkie event, or ``{}``."""

    try:
        event = json.loads(raw_event)
    except (TypeError, ValueError):
        return {}
    data = event.get("data") if isinstance(event, dict) else None
    context = data.get("agent_context") if isinstance(data, dict) else None
    prompt = context.get("company_prompt") if isinstance(context, dict) else None
    if not isinstance(prompt, dict):
        return {}
    sha8 = str(prompt.get("sha8") or "").strip().lower()[:8]
    if len(sha8) < 8:
        return {}
    return {"version": str(prompt.get("version") or "").strip(), "sha8": sha8}


@dataclass(eq=False)
class CompanyPromptReloader:
    load_config: Callable[[], dict[str, Any]]
    held: Callable[[], dict[str, Any]]
    queue_restart: Callable[[str], dict[str, Any]]
    state_dir: Path
    log: Log
    clock: Callable[[], float] = time.time
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def observe(self, raw_event: str) -> dict[str, Any] | None:
        """Queue one restart when the event announces a company prompt this workspace does not hold."""

        announced = announced_company_prompt(raw_event)
        if not announced or not auto_reload_enabled(self.load_config()):
            return None
        held = self.held()
        held_digest = str(held.get("company_prompt_sha256") or "").strip().lower()
        held_sha8 = held_digest[:8]
        # An announcement is not proof of the prompt currently held by this
        # workspace.  In particular, legacy instruction state may have no
        # prompt marker at all; restarting on that unknown state creates an
        # endless restart loop when the endpoint keeps returning unmarked 304s.
        if len(held_digest) < 8:
            self.log(
                "WARN",
                f"company_prompt_reload_skipped announced={announced['sha8']} "
                "reason=held_company_prompt_unknown",
            )
            return None
        if held_sha8 == announced["sha8"]:
            return None
        with self._lock:
            now = self.clock()
            state = self._read()
            if state.get("sha8") == announced["sha8"] and now - float(state.get("requested_at") or 0.0) < RETRY_SECONDS:
                return None
            reason = (
                f"company prompt v{announced['version'] or '?'} {announced['sha8']} announced; "
                f"held v{held.get('company_prompt_version') or '?'} {held_sha8 or '-'}"
            )
            result = self.queue_restart(reason)
            self._write({
                "sha8": announced["sha8"],
                "version": announced["version"],
                "held_sha8": held_sha8,
                "requested_at": now,
                "queued": bool(result.get("queued")),
                "detail": str(result.get("detail") or ""),
            })
        self.log(
            "INFO" if result.get("queued") else "WARN",
            f"company_prompt_reload_requested announced=v{announced['version'] or '?'}:{announced['sha8']} "
            f"held=v{held.get('company_prompt_version') or '?'}:{held_sha8 or '-'} "
            f"queued={str(bool(result.get('queued'))).lower()} detail={result.get('detail') or '-'}",
        )
        return result

    def __call__(self, raw_event: str) -> None:
        try:
            self.observe(raw_event)
        except Exception as exc:  # noqa: BLE001 - event delivery never fails on a reload check
            self.log("WARN", f"company_prompt_reload_failed error={type(exc).__name__}: {exc}")

    def _path(self) -> Path:
        return self.state_dir / STATE_FILE

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self._path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, value: dict[str, Any]) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)


_LISTENER: list[CompanyPromptReloader] = []


def start_router_listener(
    load_config: Callable[[], dict[str, Any]],
    state_dir: Path,
    workspace: Path,
    queue_restart: Callable[[str], dict[str, Any]],
    log: Log,
) -> None:
    """Watch admitted external events for a new company prompt (router process only)."""

    stop_router_listener()
    reloader = CompanyPromptReloader(load_config, lambda: held_company_prompt(state_dir, workspace), queue_restart, state_dir, log)
    _LISTENER.append(reloader)
    runtime_input_gateway.EXTERNAL_EVENT_LISTENERS.append(reloader)


def stop_router_listener() -> None:
    while _LISTENER:
        reloader = _LISTENER.pop()
        if reloader in runtime_input_gateway.EXTERNAL_EVENT_LISTENERS:
            runtime_input_gateway.EXTERNAL_EVENT_LISTENERS.remove(reloader)


__all__ = [
    "COMPANY_PROMPT_RESTART_SOURCE",
    "CompanyPromptReloader",
    "announced_company_prompt",
    "start_router_listener",
    "stop_router_listener",
]
