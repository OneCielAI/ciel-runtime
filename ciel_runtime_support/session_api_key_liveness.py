"""Session-end proof for a session key injected directly (ctl, REST, web, menu).

A key staged by a launch ends with its launcher (pid + creation token).  A key
injected directly into ``claude:<uuid>`` has no launcher, so its end is tied
to the session's own process as Claude Code publishes it: the one
``<config dir>/sessions/<pid>.json`` record whose ``sessionId`` is that uuid,
whose ``procStart`` is the process creation token (the same token
``workspace_router_selection._process_identity`` reads, compared directly in
``claude_session_handoff``).  The owner is captured only while exactly one
such record names a live process; it is kept afterwards, so the record may
vanish when the process ends without losing the proof.

No proof (a Codex session, no record, several live records, unreadable
process) means "unknown": the row is never ended for it and is shown as
"no session-end proof: clear manually".
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Iterable

from .claude_session_handoff import session_records
from .session_api_key_vault import launcher_state


def claude_session_dirs(extra: Iterable[str | Path] = ()) -> list[Path]:
    """Where Claude Code publishes session records for this router's user."""

    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    roots = [Path(configured)] if configured else []
    roots.append(Path.home() / ".claude")
    roots.extend(Path(item) for item in extra if item)
    seen: list[Path] = []
    for root in roots:
        directory = root / "sessions"
        if directory not in seen:
            seen.append(directory)
    return seen


def session_owner(session: str, directories: Callable[[], list[Path]] = claude_session_dirs,
                  state: Callable[[int, str], str] = launcher_state) -> tuple[int, str] | None:
    """``(pid, creation token)`` of the one live process that owns ``session``, else None."""

    runtime, _, native = str(session or "").partition(":")
    if runtime != "claude" or not native:
        return None
    owners: set[tuple[int, str]] = set()
    for directory in directories():
        for record in session_records(directory):
            if str(record.get("sessionId") or "") != native:
                continue
            owner = (int(record["pid"]), str(record["procStart"]))
            if state(*owner) == "alive":
                owners.add(owner)
    return owners.pop() if len(owners) == 1 else None


__all__ = ["claude_session_dirs", "session_owner"]
