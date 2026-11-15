"""Idle gate re-check: the side actor looks at blocked/waiting Work items on a timer.

User requirement 2026-11-15: when main's turn has ended and no forked session is
running, and Work items carry a blocker or a recorded wait, a side actor checks
every N minutes (default 5) whether each gate has cleared and tells main. N is
managed in the settings (menu, web dashboard, REST, ctl).

The host (ciel-work) owns the timer and the idle/no-fork condition. Each due
check is queued as one ordinary side event (``gate-check:<board digest>:<slot>``)
through the durable side inbox, so the existing native side fork decides it with
the normal decision contract: ``update`` + ``release_waits`` clears a wait it can
prove satisfied (the host then tells main or the waiting worker), ``advise``
reaches main for a blocker only main can lift, and ``answer`` records "nothing
changed" without waking anyone. The event names no external message, so it has
no source reply (``source_context: none_referenced``).
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Mapping

DEFAULT_MINUTES = 5
RANGE = (1, 1440)
KEY = "work_gate_check_minutes"
EVENT_PREFIX = "gate-check:"
MAX_GATES = 24


def minutes(config: Mapping[str, Any]) -> int:
    """The stored interval; 0 turns the check off."""
    value = (config.get("work_gate_check") or {}).get("minutes", DEFAULT_MINUTES)
    return value if type(value) is int and (value == 0 or RANGE[0] <= value <= RANGE[1]) else DEFAULT_MINUTES


def set_minutes(config: dict[str, Any], raw: str) -> None:
    token = str(raw or "").strip().lower()
    if token in ("", "default", "reset"):
        value = DEFAULT_MINUTES
    elif token in ("off", "0"):
        value = 0
    else:
        try:
            value = int(token)
        except ValueError:
            raise ValueError(f"gate check interval must be off or {RANGE[0]}-{RANGE[1]} minutes") from None
        if not RANGE[0] <= value <= RANGE[1]:
            raise ValueError(f"gate check interval must be off or {RANGE[0]}-{RANGE[1]} minutes")
    stored = dict(config.get("work_gate_check") or {})
    if value == DEFAULT_MINUTES:
        stored.pop("minutes", None)
    else:
        stored["minutes"] = value
    if stored:
        config["work_gate_check"] = stored
    else:
        config.pop("work_gate_check", None)


def gates(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Unfinished nodes with a recorded wait or a blocked status, active goal first."""
    by_id = {node["id"]: node for node in doc.get("nodes", [])}
    order = {node["id"]: index for index, node in enumerate(doc.get("nodes", []))}

    def root(node):
        seen = set()
        while node.get("parent_id") and node["id"] not in seen:
            seen.add(node["id"])
            node = by_id.get(node["parent_id"], node)
        return node

    rows = []
    for node in doc.get("nodes", []):
        if node.get("status") == "done":
            continue
        if not (node.get("waiting_on") or node.get("status") == "blocked"):
            continue
        goal = root(node)
        if goal.get("status") == "done":
            continue
        rows.append({"id": node["id"], "title": str(node.get("title") or "")[:200],
                     "waiting_on": str(node.get("waiting_on") or ""), "blocked": node.get("status") == "blocked",
                     "root_id": goal["id"], "root_title": str(goal.get("title") or "")[:200],
                     "evidence": str(node.get("evidence") or "")[-400:], "note": str(node.get("note") or "")[-400:]})
    active = doc.get("active_root")
    rows.sort(key=lambda row: (row["root_id"] != active, order.get(row["root_id"], 0), order.get(row["id"], 0)))
    return rows[:MAX_GATES]


def digest(rows: list[dict[str, Any]]) -> str:
    material = [[row["id"], row["waiting_on"], row["blocked"]] for row in rows]
    return hashlib.sha256(json.dumps(material, separators=(",", ":")).encode()).hexdigest()[:16]


def event_text(rows: list[dict[str, Any]], interval: int) -> str:
    lines = [
        "Ciel gate re-check (host timer, every %d min while main is idle and no Work fork runs)." % interval,
        "This is not an external message: no person sent it and there is no source to reply to.",
        "For each gate below, check with read-only means whether its condition is now satisfied",
        "(the referenced CI/build/deploy/git result, review or approval, a file or service state).",
        "- A recorded wait you can prove satisfied: action update on that node, kind request, with",
        "  release_waits [{\"id\": node id, \"waiting_on\": its exact text}]. The host clears it and",
        "  tells main or the waiting worker at once.",
        "- A blocker only main can lift, now resolvable: action advise on that node, summary saying",
        "  what changed and the evidence.",
        "- Nothing changed or you cannot verify it: action answer, kind status, summary \"no change\"",
        "  with what you checked. Never guess; never release on a mention alone.",
        "Gates:",
    ]
    for row in rows:
        what = ("waiting on: " + row["waiting_on"]) if row["waiting_on"] else "status blocked"
        lines.append(f"- {row['id']} ({row['title']}) in goal {row['root_id']} ({row['root_title']}): {what}")
        if row["evidence"]:
            lines.append("  last evidence: " + row["evidence"].replace("\n", " ")[:300])
    return "\n".join(lines)


def due(config: Mapping[str, Any], doc: Mapping[str, Any], state: Mapping[str, Any] | None,
        *, now: float | None = None) -> dict[str, Any] | None:
    """The gate-check event to queue now, or None.

    ``state`` is the last record ({"at", "digest"}). A changed gate set is due at
    once; an unchanged one is due when the interval has passed. The host checks
    the idle and no-fork condition before it calls this.
    """
    interval = minutes(config)
    if interval == 0:
        return None
    rows = gates(doc)
    if not rows:
        return None
    stamp = time.time() if now is None else now
    current = digest(rows)
    previous = state or {}
    if previous.get("digest") == current and stamp - float(previous.get("at") or 0) < interval * 60:
        return None
    slot = int(stamp // (interval * 60))
    text = event_text(rows, interval)
    # The text digest keeps one key per exact content: a retry of the same check
    # deduplicates, and a same-slot check with newer evidence is its own event.
    body = hashlib.sha256(text.encode()).hexdigest()[:8]
    return {"event_key": f"{EVENT_PREFIX}{current}:{slot}:{body}", "text": text,
            "digest": current, "at": stamp, "gates": [row["id"] for row in rows], "interval_minutes": interval}


__all__ = ["DEFAULT_MINUTES", "EVENT_PREFIX", "KEY", "RANGE", "digest", "due", "event_text", "gates", "minutes",
           "set_minutes"]
