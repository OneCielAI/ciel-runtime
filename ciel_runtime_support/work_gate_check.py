"""Idle gate re-check: the side actor looks at blocked/waiting Work items on a timer.

User requirement 2026-11-15: when main's turn has ended and no forked session is
running, and Work items carry a blocker or a recorded wait, a side actor checks
every N minutes (default 5) whether each gate has cleared and tells main. N is
managed in the settings (menu, web dashboard, REST, ctl).

The host (ciel-work) owns the timer and the main-idle condition. Stage 3
(2026-11-17): the check also runs while Work forks run and with side intake
off. Each round the host also re-pumps dispatch (a parked worker whose wait is
cleared resumes there), reads the per-priority diagnosis and tells main at most
once per changed diagnosis; with side intake off that host round is all that
runs, and it is recorded (``host_report``). Each due
check is queued as one ordinary side event (``gate-check:<board digest>:<slot>``)
through the durable side inbox, so the existing native side fork decides it with
the normal decision contract: ``update`` + ``release_waits`` clears a wait it can
prove satisfied (the host then tells main or the waiting worker), ``update``
with a note and no release reaches main for a blocker only main can lift (the
node is still gated, so side_work_decision files a ``resume`` handoff), and
``answer`` records "nothing changed" without waking anyone. The side
instruction retired ``advise`` (a ``request`` must create, update or
prioritize), so the event text no longer asks for it (Stage 3, 2026-11-17). The event names no external message, so it has
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
# Gates per side event. Every gate is checked each interval: a board with more
# gates gets several events (Walkie 2026-11-16, board version 5928: 270 gates;
# one event of 24 left 246 never checked).
BATCH_GATES = 24
MAX_BATCHES = 20


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
    return rows[:BATCH_GATES * MAX_BATCHES]


def digest(rows: list[dict[str, Any]]) -> str:
    material = [[row["id"], row["waiting_on"], row["blocked"]] for row in rows]
    return hashlib.sha256(json.dumps(material, separators=(",", ":")).encode()).hexdigest()[:16]


def event_text(rows: list[dict[str, Any]], interval: int, part: str = "") -> str:
    lines = [
        "Ciel gate re-check (host timer, every %d min while main is idle; Work forks may be running)%s." % (
            interval, (" — " + part) if part else ""),
        "This is not an external message: no person sent it and there is no source to reply to.",
        "For each gate below, check with read-only means whether its condition is now satisfied",
        "(the referenced CI/build/deploy/git result, review or approval, a file or service state).",
        "- A recorded wait you can prove satisfied: action update on that node, kind request, with",
        "  release_waits [{\"id\": node id, \"waiting_on\": its exact text}]. The host clears it and",
        "  tells main or the waiting worker at once.",
        "- A blocker only main can lift, now resolvable: action update on that node, kind request,",
        "  with a note saying what changed and the evidence and no release_waits. The node is still",
        "  gated, so the host hands it to main to resume (a resume handoff); nothing is cleared.",
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
    the main-idle condition before it calls this; running Work forks do not
    defer it (Stage 3, 2026-11-17).
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
    batches = [rows[index:index + BATCH_GATES] for index in range(0, len(rows), BATCH_GATES)]
    events = []
    for number, batch in enumerate(batches, 1):
        part = ("gates %d-%d of %d (event %d of %d)" % ((number - 1) * BATCH_GATES + 1,
                (number - 1) * BATCH_GATES + len(batch), len(rows), number, len(batches))
                if len(batches) > 1 else "")
        text = event_text(batch, interval, part)
        # The text digest keeps one key per exact content: a retry of the same check
        # deduplicates, and a same-slot check with newer evidence is its own event.
        body = hashlib.sha256(text.encode()).hexdigest()[:8]
        events.append({"event_key": f"{EVENT_PREFIX}{current}:{slot}:{number}:{body}", "text": text,
                       "gates": [row["id"] for row in batch]})
    return {"event_key": events[0]["event_key"], "text": events[0]["text"], "events": events,
            "digest": current, "at": stamp, "gates": [row["id"] for row in rows], "interval_minutes": interval}


_HEX = frozenset("0123456789abcdef")


def host_report(config: Mapping[str, Any], last: Mapping[str, Any] | None, report: Any, *,
                side_on: bool, forks_running: Any, now: float | None = None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Apply one host-round report to the gate-check record: (new record or None, answer).

    ``{"claim": "diagnosis", "digest": hex}`` claims the one main notice of a
    changed per-priority diagnosis (at most once per digest);
    ``{"claim": "discovery"}`` claims one discovery request per interval;
    ``{"round": {...}}`` records what the host did (re-pump, diagnosis, notice).
    An empty attention list ends the notified diagnosis, so the same state
    coming back later is a change again.
    """
    if not isinstance(report, dict):
        raise ValueError("host_report must be an object")
    stamp = time.time() if now is None else now
    record = dict(last or {})
    claim = report.get("claim")
    if claim == "diagnosis":
        value = report.get("digest")
        if not isinstance(value, str) or not 16 <= len(value) <= 64 or not set(value) <= _HEX:
            raise ValueError("diagnosis digest must be 16-64 lowercase hex characters")
        if record.get("notified_digest") == value:
            return None, {"claimed": False, "reason": "already_notified"}
        record.update(notified_digest=value, notified_at=stamp)
        return record, {"claimed": True}
    if claim == "discovery":
        interval = minutes(config) or DEFAULT_MINUTES
        if stamp - float(record.get("discovery_at") or 0) < interval * 60:
            return None, {"claimed": False, "reason": "discovery_recent"}
        record["discovery_at"] = stamp
        return record, {"claimed": True}
    if claim is not None:
        raise ValueError("unknown host_report claim")
    raw = report.get("round")
    if not isinstance(raw, dict):
        raise ValueError("host_report needs claim or round")
    attention = [str(value)[:4] for value in (raw.get("attention") or [])[:7]]
    entry = {"at": stamp, "side_intake": side_on is True,
             "forks_running": forks_running if type(forks_running) is int and forks_running >= 0 else None,
             "repumped": raw.get("repumped") is True, "attention": attention,
             "notified": raw.get("notified") is True, "discovery_requested": raw.get("discovery_requested") is True,
             "summary": str(raw.get("summary") or "")[:300]}
    record["host_round"] = entry
    if not attention:
        record.pop("notified_digest", None)
    return record, {"recorded": True, "host_round": entry}


__all__ = ["DEFAULT_MINUTES", "EVENT_PREFIX", "KEY", "RANGE", "digest", "due", "event_text", "gates", "host_report",
           "minutes", "set_minutes"]
