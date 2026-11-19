"""Gate re-check: the side actor looks at blocked/waiting Work items on a timer.

User requirement 2026-11-15: when Work items carry a blocker or a recorded wait,
a side actor checks every N minutes (default 5) whether each gate has cleared and
tells main. N is managed in the settings (menu, web dashboard, REST, ctl).

KOBE hotfix 2026-11-18: the check is the backup for a person's approval message
lost to a network problem, so it also runs while main's turn is busy (KOBE: main
stayed busy and no check ran for over an hour). Gates are ordered by their goal's
effective priority: P0 first, then P1, then the rest.

The host (ciel-work) owns the timer. Stage 3
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
import re
import time
from typing import Any, Callable, Mapping

DEFAULT_MINUTES = 5
RANGE = (1, 1440)
KEY = "work_gate_check_minutes"
EVENT_PREFIX = "gate-check:"
# Every gate is checked each interval (Walkie 2026-11-16, board version 5928:
# 270 gates; one event of 24 left 246 never checked). Andy 2026-11-18 (user
# design): one tick is one batch, so all gates go into one side event, decided
# by one fork; the event text is bounded by the side inbox text limit.
BATCH_GATES = 24
MAX_BATCHES = 20
EVENT_TEXT_CHARS = 30_000


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


def _rank(priority: Any) -> int:
    return {"P0": 0, "P1": 1}.get(str(priority or ""), 2)


def gates(doc: Mapping[str, Any], priority: Callable[[Mapping[str, Any]], str] | None = None) -> list[dict[str, Any]]:
    """Unfinished nodes with a recorded wait or a blocked status.

    Ordered by the goal's effective priority (``priority(goal)``, default its
    canonical ``priority``): P0 first, then P1, then the rest; within a rank the
    active goal first, then board order.
    """
    priority_of = priority or (lambda goal: str(goal.get("priority") or ""))
    ranks: dict[str, str] = {}
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
        if goal["id"] not in ranks:
            try:
                ranks[goal["id"]] = str(priority_of(goal) or "")
            except Exception:  # noqa: BLE001 - an unreadable P0 marker keeps the canonical priority
                ranks[goal["id"]] = str(goal.get("priority") or "")
        rows.append({"id": node["id"], "title": str(node.get("title") or "")[:200],
                     "priority": ranks[goal["id"]],
                     "waiting_on": str(node.get("waiting_on") or ""), "blocked": node.get("status") == "blocked",
                     "root_id": goal["id"], "root_title": str(goal.get("title") or "")[:200],
                     "evidence": str(node.get("evidence") or "")[-400:], "note": str(node.get("note") or "")[-400:]})
    active = doc.get("active_root")
    rows.sort(key=lambda row: (_rank(row["priority"]), row["root_id"] != active, order.get(row["root_id"], 0),
                               order.get(row["id"], 0)))
    return rows[:BATCH_GATES * MAX_BATCHES]


def digest(rows: list[dict[str, Any]]) -> str:
    material = [[row["id"], row["waiting_on"], row["blocked"]] for row in rows]
    return hashlib.sha256(json.dumps(material, separators=(",", ":")).encode()).hexdigest()[:16]


def event_text(rows: list[dict[str, Any]], interval: int, part: str = "",
               threads: Mapping[str, Mapping[str, Any]] | None = None,
               included: list[str] | None = None, rules: str | None = None) -> str:
    """The tick's one event. ``rules`` is the managed instruction (prompt
    ``gate_recheck``: REST, web, ctl and menu edit it; it applies from the next
    tick with no restart). ``threads`` maps a gate id to its thread's new
    messages (gate-check thread reads): they are listed under the first gate of
    that thread, and the later gates of the thread point to it."""
    lines = [
        "Ciel 게이트 재확인 (호스트 타이머, %d분마다, 메인이 바쁠 때도 동작; Work 포크가 돌고 있을 수 있음)%s." % (
            interval, (" — " + part) if part else ""),
        *(rules if isinstance(rules, str) and rules.strip() else prompt_default("gate_recheck")).split("\n"),
        "게이트:",
    ]
    shown: dict[str, str] = {}
    size = sum(len(line) + 1 for line in lines)
    for row in rows:
        if included is not None:
            if size > EVENT_TEXT_CHARS - 1500 and included:
                lines.append("[게이트 %d개 중 %d개만 실었습니다. 나머지는 다음 점검에서 이어서 확인합니다]" % (len(rows), len(included)))
                break
            included.append(row["id"])
        mark = len(lines)
        what = ("대기 조건: " + row["waiting_on"]) if row["waiting_on"] else "상태 blocked"
        rank = ("[" + row["priority"] + "] ") if row.get("priority") else ""
        lines.append(f"- {rank}{row['id']} ({row['title']}) / 목표 {row['root_id']} ({row['root_title']}): {what}")
        if row.get("lease") is not None:
            lines.append("  lease: " + (row["lease"] or "없음 (이 아이템을 맡은 포크가 없었음)"))
        if row["evidence"]:
            lines.append("  마지막 증거: " + row["evidence"].replace("\n", " ")[:300])
        thread = (threads or {}).get(row["id"])
        if thread:
            key = thread["key"]
            if key in shown:
                lines.append("  새 스레드 메시지: 위의 " + shown[key] + " 참고 (같은 스레드)")
                size += sum(len(line) + 1 for line in lines[mark:])
                continue
            shown[key] = row["id"]
            messages = thread["messages"]
            from .work_gate_threads import MESSAGES_PER_THREAD
            kept = messages[-MESSAGES_PER_THREAD:]
            lines.append("  원본 스레드(루트 %s)의 %s 이후 새 메시지: %d건%s" % (
                thread["root_id"], thread["after"], len(messages),
                (", 최신 %d건 표시" % len(kept)) if len(kept) < len(messages) else ""))
            for message in kept:
                lines.append("    #%s %s: %s" % (message["id"], message["sender"] or "?", message["text"]))
            if thread.get("more"):
                lines.append("    (새 메시지가 더 있습니다. 필요한 것만 읽으십시오)")
        size += sum(len(line) + 1 for line in lines[mark:])
    text = "\n".join(lines)
    if len(text) > EVENT_TEXT_CHARS:
        text = text[:EVENT_TEXT_CHARS - 80] + "\n[이벤트 크기 한도에서 게이트 목록이 잘렸습니다. 나머지는 다음 점검에서 확인합니다]"
    return text


TURN_END_MIN_SECONDS = 60


def tick_rows(config: Mapping[str, Any], doc: Mapping[str, Any], state: Mapping[str, Any] | None,
              *, now: float | None = None,
              priority: Callable[[Mapping[str, Any]], str] | None = None,
              turn_end: bool = False) -> tuple[list[dict[str, Any]], str, float] | None:
    """(gates, digest, stamp) when a tick is due now, else None."""
    interval = minutes(config)
    if interval == 0:
        return None
    rows = gates(doc, priority)
    # At main's turn end the side also looks for other work, gates or not.
    if not rows and not turn_end:
        return None
    stamp = time.time() if now is None else now
    current = digest(rows)
    previous = state or {}
    # Main's turn end (user 2026-11-18): the side checks for released blocks,
    # approvals and missed work then, at most once a minute.
    window = TURN_END_MIN_SECONDS if turn_end else interval * 60
    if previous.get("digest") == current and stamp - float(previous.get("at") or 0) < window:
        return None
    return rows, current, stamp


TURN_END_TEXT = None  # The default lives in work_workflow_prompts ("turn_end").


def tick_event(config: Mapping[str, Any], rows: list[dict[str, Any]], current: str, stamp: float,
               threads: Mapping[str, Mapping[str, Any]] | None = None, turn_end: bool = False,
               review_pending: list[str] | None = None, rules: str | None = None,
               turn_text: str | None = None) -> dict[str, Any]:
    """The one side event of a tick for ``rows``."""
    interval = minutes(config)
    slot = int(stamp // (interval * 60))
    fit: list[str] = []
    text = event_text(rows, interval, "%d gate(s)" % len(rows) if len(rows) > 1 else "", threads, included=fit, rules=rules)
    if turn_end:
        text = (turn_text if isinstance(turn_text, str) and turn_text.strip() else prompt_default("turn_end")) + "\n" + text
    # The text digest keeps one key per exact content: a retry of the same check
    # deduplicates, and a same-slot check with newer evidence is its own event.
    body = hashlib.sha256(text.encode()).hexdigest()[:8]
    event = {"event_key": f"{EVENT_PREFIX}{current}:{slot}:1:{body}", "text": text, "gates": fit}
    return {"event_key": event["event_key"], "text": text, "events": [event], "digest": current, "at": stamp,
            "gates": event["gates"], "interval_minutes": interval}


def due(config: Mapping[str, Any], doc: Mapping[str, Any], state: Mapping[str, Any] | None,
        *, now: float | None = None,
        priority: Callable[[Mapping[str, Any]], str] | None = None) -> dict[str, Any] | None:
    """The gate-check event to queue now, or None.

    ``state`` is the last record ({"at", "digest"}). A changed gate set is due at
    once; an unchanged one is due when the interval has passed. Neither main's
    busy turn (KOBE hotfix 2026-11-18) nor running Work forks (Stage 3,
    2026-11-17) defer it; the router keeps one round at a time.
    """
    tick = tick_rows(config, doc, state, now=now, priority=priority)
    if tick is None:
        return None
    rows, current, stamp = tick
    return tick_event(config, rows, current, stamp)


def effective_priority_for(identity: str) -> Callable[[Mapping[str, Any]], str]:
    """A goal's effective priority in this conversation: P0 while its P0 marker is
    active (work_parallel_p0), otherwise its canonical priority."""
    def priority(goal: Mapping[str, Any]) -> str:
        canonical = str(goal.get("priority") or "")
        try:
            from .work_parallel_mcp import p0_state
            return p0_state().effective_priority(identity, dict(goal), canonical)
        except Exception:  # noqa: BLE001 - an unreadable ledger keeps the canonical order
            return canonical
    return priority


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


__all__ = ["DEFAULT_MINUTES", "EVENT_PREFIX", "KEY", "RANGE", "digest", "due", "effective_priority_for", "event_text",
           "gates", "host_report", "minutes", "set_minutes", "tick_event", "tick_rows"]


def prompt_default(name: str) -> str:
    from .work_workflow_prompts import DEFAULTS
    return DEFAULTS[name]


INTERNAL_WAIT = re.compile(r"work\s*(host|parallel|전역)|lease|수락\s*gate|validation|cleanup|검수|검증|정리", re.I)


def lease_facts(leases: Mapping[str, Any], scope: str) -> tuple[dict[str, str], list[str]]:
    """item id -> the fact line of its fork leases, and the items awaiting main's review.

    An item with an open lease shows those states. One without shows how its
    last fork ended ("없음 (마지막 포크: orphaned)") so the blocker-check actor
    can tell a wait on a fork that is gone; an item no fork ever took is absent.
    """
    from .work_parallel_diagnosis import FENCE_STATES
    open_states: dict[str, list[str]] = {}
    last: dict[str, tuple[float, str]] = {}
    for lease in leases.values():
        if lease.get("scope") != scope:
            continue
        item = str(lease.get("item_id"))
        if lease.get("state") in FENCE_STATES:
            open_states.setdefault(item, []).append(str(lease["state"]))
        stamp = float(lease.get("created_at") or 0)
        if item not in last or stamp >= last[item][0]:
            last[item] = (stamp, str(lease.get("state")))
    facts = {item: ", ".join(sorted(set(states))) for item, states in open_states.items()}
    for item, (_, state) in last.items():
        facts.setdefault(item, "없음 (마지막 포크: %s)" % state)
    waiting = sorted(item for item, states in open_states.items() if "submitted" in states)
    return facts, waiting


def rotate(rows: list[dict[str, Any]], offset: Any) -> list[dict[str, Any]]:
    """Start after the gates the last round carried, so every gate gets its turn."""
    if not rows or type(offset) is not int:
        return rows
    start = offset % len(rows)
    return rows[start:] + rows[:start]
