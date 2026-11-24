"""Running Work forks whose own transcript stopped growing (user 2026-11-24).

Prody n1200 2026-11-24: lease 6ebe7e68 (P1) stayed ``running`` with its slot
held while the fork's transcript ended at a Bash tool call (10:58:19Z) with no
result for 57+ minutes; no process of that call was alive and the ledger had
not changed since 10:31Z. The stall watch (``work_gate_check.stalled_forks``)
lists only parked forks, and nothing else looked at a running one.

The ledger records states only and the native observation records an agent's
status only, so the progress clock is the fork's own transcript: the file
``work_fork_api_retry`` already reads for running forks
(``<config>/projects/<slug>/<session>/subagents/agent-<host_id>.jsonl``). A
running, slot-holding fork is silent when that file has not been written for
``SILENT_MINUTES`` and its last assistant/user record does not close the turn
(a closed turn is ``fork_turn_watch``'s case; an API error record closes as
``error`` and is ``work_fork_api_retry``'s). Claude Code's Bash tool times out
after at most 10 minutes by default, so 30 minutes without one written record
is three of its longest foreground calls.

One silence is the lease plus the time of its last record: the router records
that mark on the item's note when main is told, so the same silence is not
listed again, and a fork that records again and then stops is a new silence.
Rows carry no transcript text: only the last record's kind and tool name.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping

SILENT_MINUTES = 30
_TOOL_NAME = re.compile(r"[^A-Za-z0-9_.:-]")


def transcript_dir(scope: str, leases: Mapping[str, Any]) -> Path | None:
    """The conversation's fork transcript directory, or None when unresolvable (Codex, unknown)."""
    from .work_fork_api_retry import _SESSION_ID, _subagents_for
    if not isinstance(scope, str) or not scope.startswith("claude:"):
        return None
    session_id = scope.split(":", 1)[1]
    if not _SESSION_ID.fullmatch(session_id):
        return None
    try:
        directory = _subagents_for(session_id, dict(leases))
    except Exception:  # noqa: BLE001 - an unresolvable scope lists no silent fork
        return None
    return directory if directory is not None and directory.is_dir() else None


def _last_record(path: Path) -> tuple[dict[str, Any] | None, float] | None:
    """(last assistant/user record, file modification time), or None when unreadable."""
    from .work_fork_api_retry import MAX_RECORD_BYTES, _tail_lines
    try:
        if path.is_symlink() or not path.is_file():
            return None
        grown = path.stat().st_mtime
        lines = _tail_lines(path)
    except OSError:
        return None
    last = None
    for line in lines:
        if not line.strip() or len(line) > MAX_RECORD_BYTES:
            continue
        try:
            row = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(row, dict) and row.get("type") in ("assistant", "user"):
            last = row
    return last, grown


def _kind(record: Mapping[str, Any]) -> str:
    """The last record's shape without its content: ``tool_use:<name>``, ``tool_result`` or ``<type>``."""
    message = record.get("message") if isinstance(record.get("message"), dict) else {}
    blocks = [block for block in (message.get("content") or []) if isinstance(block, dict)] \
        if isinstance(message.get("content"), list) else []
    tools = [block.get("name") for block in blocks if block.get("type") == "tool_use"]
    if tools:
        return "tool_use:" + _TOOL_NAME.sub("", str(tools[-1] or ""))[:80]
    if any(block.get("type") == "tool_result" for block in blocks):
        return "tool_result"
    return str(record.get("type"))


def _iso(stamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp))


def told(note: Any, mark: str) -> bool:
    """Whether any stall notice recorded on this note names this exact silence."""
    from .work_gate_check import STALL_TOLD
    return any(part.find(mark) >= 0 for part in str(note or "").split(STALL_TOLD)[1:])


def _silences(leases: Mapping[str, Any], scope: str, doc: Mapping[str, Any], directory: Path | None,
              stamp: float, minutes: float) -> list[tuple[Mapping[str, Any], Mapping[str, Any], list, dict[str, Any]]]:
    """(lease, item, item chain, facts) for every silent running fork of this conversation."""
    from .agent_turn_events import claude_record_closing_reason
    from .fork_turn_watch import _parse_time
    from .work_fork_api_retry import _AGENT_ID
    if directory is None or not directory.is_dir():
        return []
    by_id = {node["id"]: node for node in doc.get("nodes", [])}
    out = []
    for lease in leases.values():
        if (not isinstance(lease, dict) or lease.get("scope") != scope or lease.get("state") != "running"
                or lease.get("slot") is not True or not isinstance(lease.get("host_id"), str)
                or not _AGENT_ID.fullmatch(lease["host_id"])):
            continue
        node = by_id.get(lease.get("item_id"))
        if node is None or node.get("status") == "done":
            continue
        chain, current, seen = [], node, set()
        while current is not None and current["id"] not in seen:
            seen.add(current["id"])
            chain.append(current)
            current = by_id.get(current.get("parent_id")) if current.get("parent_id") else None
        if chain[-1].get("status") == "done":
            continue
        read = _last_record(directory / f"agent-{lease['host_id']}.jsonl")
        if read is None or read[0] is None:
            continue
        record, grown = read
        if stamp - grown < minutes * 60 or claude_record_closing_reason(record) is not None:
            continue
        at = _parse_time(record.get("timestamp")) or grown
        out.append((lease, node, chain, {"since": grown, "mark": "lease " + str(lease["id"]) + " silent since " + _iso(at),
                                         "since_iso": _iso(at), "last": _kind(record)}))
    return out


def silent_forks(leases: Mapping[str, Any], scope: str, doc: Mapping[str, Any], *, now: float | None = None,
                 priority: Callable[[Mapping[str, Any]], str] | None = None, minutes: float = SILENT_MINUTES,
                 directory: Path | None = None, resolve: bool = True) -> list[dict[str, Any]]:
    """Silent running forks not told yet, as stall rows (``kind`` "silent") for the gate check.

    ``directory`` is the conversation's fork transcript directory; with
    ``resolve`` (the default) and no directory it is resolved from the router's
    scope (``transcript_dir``).
    """
    stamp = time.time() if now is None else now
    if directory is None and resolve:
        directory = transcript_dir(scope, leases)
    priority_of = priority or (lambda goal: str(goal.get("priority") or ""))
    from .work_gate_check import PARKED_NOTE_CHARS, PARKED_QUESTION_CHARS, _rank
    ranks: dict[str, str] = {}
    rows = []
    for lease, node, chain, facts in _silences(leases, scope, doc, directory, stamp, minutes):
        if told(node.get("note"), facts["mark"]):
            continue
        goal = chain[-1]
        if goal["id"] not in ranks:
            try:
                ranks[goal["id"]] = str(priority_of(goal) or "")
            except Exception:  # noqa: BLE001 - an unreadable P0 marker keeps the canonical priority
                ranks[goal["id"]] = str(goal.get("priority") or "")
        rows.append({"lease_id": str(lease["id"]), "item_id": node["id"], "title": str(node.get("title") or "")[:200],
                     "root_id": goal["id"], "root_title": str(goal.get("title") or "")[:200],
                     "priority": ranks[goal["id"]], "kind": "silent", "gate_id": None, "waiting_on": "",
                     "question": str(lease.get("evidence") or "")[:PARKED_QUESTION_CHARS],
                     "since": facts["since"], "minutes": int((stamp - facts["since"]) // 60),
                     "threshold_minutes": int(minutes),
                     "hours": round((stamp - facts["since"]) / 3600, 1),
                     "mark": facts["mark"], "last": facts["last"],
                     "progress": str(node.get("progress") or "")[-PARKED_NOTE_CHARS:]})
    active = doc.get("active_root")
    rows.sort(key=lambda row: (_rank(row["priority"]), row["root_id"] != active, row["since"], row["lease_id"]))
    return rows


def silent_projection(ledger_document: Mapping[str, Any], board: Mapping[str, Any], scope: str, *,
                      now: float | None = None, minutes: float = SILENT_MINUTES,
                      directory: Path | None = None, resolve: bool = False) -> list[dict[str, Any]] | None:
    """The router's own re-read at resolve time: every silence now, told or not; None when unreadable."""
    leases = ledger_document.get("leases") or {}
    if directory is None and resolve:
        directory = transcript_dir(scope, leases)
    if directory is None or not directory.is_dir():
        return None
    stamp = time.time() if now is None else now
    return [{"lease_id": str(lease["id"]), "item_id": node["id"], "mark": facts["mark"],
             "since": facts["since_iso"], "last": facts["last"]}
            for lease, node, _chain, facts in _silences(leases, scope, board, directory, stamp, minutes)]


__all__ = ["SILENT_MINUTES", "silent_forks", "silent_projection", "told", "transcript_dir"]
