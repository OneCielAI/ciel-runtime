"""Gate re-check thread cursors (Andy 2026-11-18, user design).

The periodic gate re-check reads only what changed in each gate's source
thread, never the whole thread. Per session the router keeps, outside the board
(so no approval scope digest changes):

- ``threads``: one row per Walkie thread (conversation + root) with the newest
  message id the host has seen in it (``last_seen``), advanced by every side
  event of that thread the router commits and by every gate-check read;
- ``nodes``: Work node id -> thread key, recorded when a side decision creates
  or updates that node from a Walkie event. The thread comes from the host's
  own classification of the event (side_reply_connector.source_read), never
  from the model.

Each tick the host reads ``threads.read`` after ``last_seen`` once per thread
(gates sharing a thread share the read), and the router queues one side event
for the whole tick with only the new messages under each gate, or none when no
gate has anything new.
"""
from __future__ import annotations

import json
import re
import time
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

MESSAGE_ID = re.compile(r"^[1-9]\d{0,17}$")
MAX_THREADS = 2000
MAX_NODES = 5000
# What one tick hands the fork per thread: the newest new messages, clipped.
MESSAGES_PER_THREAD = 30
MESSAGE_CHARS = 400


def _path(state_dir: Path, identity: str) -> Path:
    return Path(state_dir) / "sessions" / sha256(identity.encode()).hexdigest()[:24] / "gate-threads.json"


def thread_key(conversation_id: str, root_id: str) -> str:
    return str(conversation_id) + "|" + str(root_id)


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = None
    if not isinstance(value, dict):
        value = {}
    threads = value.get("threads") if isinstance(value.get("threads"), dict) else {}
    nodes = value.get("nodes") if isinstance(value.get("nodes"), dict) else {}
    return {"threads": threads, "nodes": nodes}


def _write(path: Path, value: dict[str, Any]) -> None:
    for name, limit in (("threads", MAX_THREADS), ("nodes", MAX_NODES)):
        rows = value[name]
        if len(rows) > limit:
            # Oldest first by insertion order: dicts keep it.
            for key in list(rows)[: len(rows) - limit]:
                rows.pop(key, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _newer(candidate: Any, current: Any) -> bool:
    if not isinstance(candidate, str) or not MESSAGE_ID.fullmatch(candidate):
        return False
    if not isinstance(current, str) or not MESSAGE_ID.fullmatch(current):
        return True
    return int(candidate) > int(current)


def _change(state_dir: Path, identity: str, apply) -> Any:
    from .channel_message_repository import exclusive_file_lock
    path = _path(state_dir, identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path):
        value = _read(path)
        result = apply(value)
        _write(path, value)
        return result


def record_event(state_dir: Path, identity: str, source: Mapping[str, Any] | None, result: Mapping[str, Any] | None) -> None:
    """A committed side event of one Walkie thread: advance its cursor, and bind
    the Work node the decision created or updated to that thread."""
    if not isinstance(source, Mapping) or source.get("classification") != "thread":
        return
    conversation, root = source.get("conversation_id"), source.get("root_id")
    message = source.get("target_message_id")
    if not isinstance(conversation, str) or not MESSAGE_ID.fullmatch(str(root or "")):
        return
    key = thread_key(conversation, root)
    item = (result or {}).get("item_id") if (result or {}).get("action") in ("create", "update") else None

    def apply(value):
        row = value["threads"].get(key) or {"conversation_id": conversation, "root_id": root,
                                             "server": str(source.get("server") or "walkie")[:100]}
        if _newer(message, row.get("last_seen")):
            row["last_seen"] = message
        row["at"] = time.time()
        value["threads"].pop(key, None)
        value["threads"][key] = row
        if isinstance(item, str) and 0 < len(item) <= 200:
            value["nodes"].pop(item, None)
            value["nodes"][item] = key
    _change(state_dir, identity, apply)


def bind(state_dir: Path, identity: str, node_id: str, conversation_id: str, root_id: str, *,
         last_seen: str | None = None, server: str = "walkie") -> None:
    """Bind a node to a thread directly (fixtures and migrations)."""
    record_event(state_dir, identity, {"classification": "thread", "conversation_id": conversation_id,
                                       "root_id": root_id, "target_message_id": last_seen, "server": server},
                 {"action": "update", "item_id": node_id})


def source_bindings(state_dir: Path, identity: str, source: Mapping[str, Any]) -> dict[str, list[str]]:
    """Scope local candidates by the exact conversation and thread pair."""
    bindings = _read(_path(state_dir, identity))["nodes"]
    key = thread_key(source['conversation_id'], source['root_id'])
    prefix = source['conversation_id'] + '|'
    return {"matches": [node for node, bound in bindings.items() if bound == key],
            "excluded": [node for node, bound in bindings.items() if isinstance(bound, str) and bound != key
                         and (bound.startswith(prefix) or bound.endswith('|' + source['root_id']))]}


def threads_for(state_dir: Path, identity: str, rows: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Gate rows grouped by their bound thread: {key: {..., "after", "gates": [ids]}}.

    A gate is bound through its own node, else through its goal.
    """
    value = _read(_path(state_dir, identity))
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = value["nodes"].get(row["id"]) or value["nodes"].get(row.get("root_id"))
        thread = value["threads"].get(key) if key else None
        if not thread:
            continue
        entry = grouped.setdefault(key, {"key": key, "server": thread.get("server") or "walkie",
                                         "conversation_id": thread["conversation_id"], "root_id": thread["root_id"],
                                         "after": thread.get("last_seen") or str(thread["root_id"]), "gates": []})
        entry["gates"].append(row["id"])
    return grouped


def advance(state_dir: Path, identity: str, newest: Mapping[str, str]) -> None:
    """After a check: each thread's cursor moves to the newest message read."""
    def apply(value):
        for key, message in newest.items():
            row = value["threads"].get(key)
            if row is not None and _newer(message, row.get("last_seen")):
                row["last_seen"] = message
                row["at"] = time.time()
    _change(state_dir, identity, apply)


def clean_reads(raw: Any, planned: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """The host's thread reads, bounded and checked against the plan.

    Only planned keys count; message ids must be newer than the planned cursor
    and in ascending order; text is clipped. A read the host could not make is
    kept as ``error`` (its gates are checked as gates without a thread).
    """
    reads: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, list):
        return reads
    for entry in raw[:MAX_THREADS]:
        if not isinstance(entry, Mapping) or entry.get("key") not in planned:
            continue
        key = entry["key"]
        cursor = planned[key]["after"]
        if entry.get("error"):
            reads[key] = {"error": str(entry["error"])[:200], "messages": [], "newest": None}
            continue
        messages, previous = [], cursor
        for message in entry.get("messages") or []:
            if not isinstance(message, Mapping) or not _newer(message.get("id"), previous):
                continue
            previous = message["id"]
            messages.append({"id": message["id"], "sender": str(message.get("sender") or "")[:80],
                             "text": " ".join(str(message.get("text") or "").split())[:MESSAGE_CHARS]})
        reads[key] = {"messages": messages, "newest": previous if previous != cursor else None,
                      "more": entry.get("more") is True}
    return reads


__all__ = ["MESSAGES_PER_THREAD", "advance", "bind", "clean_reads", "record_event", "thread_key", "threads_for"]
