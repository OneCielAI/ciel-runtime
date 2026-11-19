"""Reworks granted past max_rework, per Work item (approval workflow escalation).

When a workflow step reaches its rework limit the goal is escalated, and main
may choose another model for one more rework (spec 4.8). The grant lives in a
sidecar beside the device ledger, not in the ledger's lease rows: the ledger's
lease fields are validated exactly by every installed build, and an older
router on the same machine must keep reading the ledger.
"""
from __future__ import annotations

import json
from pathlib import Path

FILE = "work-parallel-rework-grants.json"
MAX_GRANT = 10


def _path(ledger_path) -> Path:
    return Path(ledger_path).with_name(FILE)


def _key(scope: str, item_id: str) -> str:
    return str(scope) + "\0" + str(item_id)


def grant(ledger_path, scope: str, item_id: str) -> int:
    """Extra reworks granted to this conversation's item; 0 when none or unreadable."""
    try:
        value = json.loads(_path(ledger_path).read_text(encoding="utf-8")).get(_key(scope, item_id), 0)
    except (OSError, ValueError, AttributeError):
        return 0
    return value if type(value) is int and 0 <= value <= MAX_GRANT else 0


def add(ledger_path, scope: str, item_id: str) -> int:
    from .channel_message_repository import exclusive_file_lock
    path = _path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        value = min(MAX_GRANT, int(data.get(_key(scope, item_id)) or 0) + 1)
        data[_key(scope, item_id)] = value
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.replace(path)
    return value


__all__ = ["FILE", "add", "grant"]
