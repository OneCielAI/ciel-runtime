"""Hierarchical Work plan document: pure tree operations and renderings.

A document holds any number of root goals.  Each root decomposes into
phases, tasks and steps of arbitrary depth (capped by ``MAX_DEPTH``).  The
document also carries the Work phase: ``planning`` while the active goal is
being decomposed, ``executing`` once the active plan was approved. Each root
retains its own phase when priority changes; the document phase mirrors the active root.
All functions here are pure; persistence lives in ``work_plan_store``.
"""

from __future__ import annotations

import copy
import math
import time
from typing import Any

from .side_intake_limits import SIDE_INTAKE_TEXT_MAX_CHARS

SCHEMA = 1
PHASE_PLANNING = "planning"
PHASE_EXECUTING = "executing"
PHASES = (PHASE_PLANNING, PHASE_EXECUTING)
STATUSES = ("pending", "in_progress", "done", "blocked")
MAX_DEPTH = 8
# Presentation/input batching is independent of queue registration, which has
# no item-count ceiling. Execution concurrency is controlled by the per-priority fork limits.
MAX_FOCUS_BATCH = 100
MAX_TITLE_CHARS = 300
MAX_TEXT_CHARS = 2000
MAX_QUEUE_IN_SNAPSHOT = 20
# What an item waits for outside the conversation, and an id it is linked to;
# both are one line and kept apart from the title.
MAX_WAIT_CHARS = 120
MAX_REF_CHARS = 64
# Third-level rows per branch in the snapshot; the rest is counted.
MAX_BRANCH_CHILDREN_IN_SNAPSHOT = 8
MAX_WAITING_IN_CONTINUATION = 5
MAX_BLOCKED_IN_CONTINUATION = 5
# A plan is ready to execute once the active goal reaches at least
# goal -> phase -> task (a leaf at depth 2).
READY_MIN_LEAF_DEPTH = 2
STATUS_MARKS = {"pending": "[ ]", "in_progress": "[~]", "done": "[✓]", "blocked": "[!]"}
PRIORITIES = ("P1", "P2", "P3", "P4", "P5")


def priority_rank(value: Any) -> int:
    """P1..P5 rank for queue ordering; unranked goals sort after ranked ones."""
    return PRIORITIES.index(value) if value in PRIORITIES else len(PRIORITIES)


class WorkPlanError(ValueError):
    """A plan operation was rejected; the message is shown to the model."""


class WorkPlanCapacityError(WorkPlanError):
    """No unfinished-node slot is available; existing records are preserved."""


def empty_document() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "version": 0,
        "phase": PHASE_PLANNING,
        "active_root": None,
        "focus_ids": [],
        "next_id": 1,
        "nodes": [],
        "updated_at": 0.0,
    }


def normalize_document(raw: Any) -> dict[str, Any]:
    """Validate a stored document; raise ``WorkPlanError`` when it is unusable."""

    if not isinstance(raw, dict):
        raise WorkPlanError("work plan document is not an object")
    if raw.get("schema") != SCHEMA or raw.get("phase") not in PHASES:
        raise WorkPlanError("unsupported work plan schema or phase")
    doc = empty_document()
    doc["version"] = _int(raw.get("version"), 0)
    doc["phase"] = raw["phase"]
    doc["next_id"] = max(1, _int(raw.get("next_id"), 1))
    doc["updated_at"] = float(raw.get("updated_at") or 0.0)
    receipts = raw.get("side_intake_receipts")
    if receipts is not None:
        if not isinstance(receipts, dict):
            raise WorkPlanError("invalid side intake receipts")
        for key, receipt in receipts.items():
            if (not isinstance(key, str) or len(key) != 64 or not isinstance(receipt, dict)
                    or not all(isinstance(receipt.get(field), str) for field in ("item_id", "digest", "text", "recap"))
                    or len(receipt["text"]) > SIDE_INTAKE_TEXT_MAX_CHARS or len(receipt["recap"]) > 500):
                raise WorkPlanError("invalid side intake receipt")
        doc["side_intake_receipts"] = copy.deepcopy(receipts)
    decisions = raw.get('side_decisions')
    if decisions is not None:
        if not isinstance(decisions, dict) or any(
                not isinstance(key, str) or len(key) != 64 or not isinstance(row, dict)
                or not isinstance(row.get('decision_digest'), str) or not isinstance(row.get('result'), dict)
                for key, row in decisions.items()):
            raise WorkPlanError('invalid side decision receipts')
        doc['side_decisions'] = copy.deepcopy(decisions)
    handoffs = raw.get('side_handoffs')
    if handoffs is not None:
        if not isinstance(handoffs, dict) or any(not isinstance(key, str) or len(key) != 64
                or not isinstance(row, dict) or row.get('state') not in ('requested', 'acknowledged', 'answered')
                for key, row in handoffs.items()):
            raise WorkPlanError('invalid side handoff records')
        doc['side_handoffs'] = copy.deepcopy(handoffs)
    reviews = raw.get('side_reviews')
    if reviews is not None:
        if not isinstance(reviews, dict) or any(not isinstance(key, str) or len(key) != 64
                or not isinstance(row, dict) or row.get('state') not in ('pending', 'reviewed', 'corrected')
                for key, row in reviews.items()):
            raise WorkPlanError('invalid side review records')
        doc['side_reviews'] = copy.deepcopy(reviews)
    nodes = raw.get("nodes")
    if not isinstance(nodes, list):
        raise WorkPlanError("work plan nodes are not a list")
    seen: set[str] = set()
    for item in nodes:
        if not isinstance(item, dict):
            raise WorkPlanError("work plan node is not an object")
        if item.get("status") not in STATUSES:
            raise WorkPlanError("work plan node has an invalid status")
        node = {
            "id": str(item.get("id") or ""),
            "parent_id": item.get("parent_id") if item.get("parent_id") is None else str(item.get("parent_id")),
            "title": _clip(item.get("title"), MAX_TITLE_CHARS),
            "status": item.get("status") if item.get("status") in STATUSES else "pending",
            "note": _clip(item.get("note"), MAX_TEXT_CHARS),
            "evidence": _clip(item.get("evidence"), MAX_TEXT_CHARS),
            "waiting_on": _line(item.get("waiting_on"), MAX_WAIT_CHARS),
            "ref": _line(item.get("ref"), MAX_REF_CHARS),
            "urgent": item.get("urgent") is True and item.get("parent_id") is None,
            "updated_at": float(item.get("updated_at") or 0.0),
            "created_at": float(item.get("created_at", item.get("updated_at")) or 0.0),
        }
        if "completed_at" in item:
            value = item["completed_at"]
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise WorkPlanError("invalid completion timestamp")
            node["completed_at"] = value
        if "expired_done_steps" in item:
            value = item["expired_done_steps"]
            if type(value) is not int or value < 0:
                raise WorkPlanError("invalid expired step count")
            node["expired_done_steps"] = value
        if node["parent_id"] is None:
            phase = item.get("phase", raw["phase"] if item.get("id") == raw.get("active_root") else PHASE_PLANNING)
            if phase not in PHASES:
                raise WorkPlanError("work goal has an invalid phase")
            node["phase"] = phase
            # Queue priority is optional and stored only on goals; older
            # boards without it normalize to unranked ('').
            priority = item.get("priority", "")
            if priority not in ("", *PRIORITIES):
                raise WorkPlanError("work goal has an invalid priority")
            node["priority"] = priority
        if not node["id"] or node["id"] in seen or not node["title"]:
            raise WorkPlanError("work plan node has a missing or duplicate id or an empty title")
        seen.add(node["id"])
        doc["nodes"].append(node)
    by_id = _index(doc)
    for node in doc["nodes"]:
        if node["parent_id"] is not None and node["parent_id"] not in by_id:
            raise WorkPlanError(f"work plan node {node['id']} has an unknown parent")
    for node in doc["nodes"]:
        if _depth(by_id, node["id"]) > MAX_DEPTH:
            raise WorkPlanError("stored work plan exceeds the depth limit")
    allocated = [int(key[1:]) for key in by_id if key.startswith("n") and key[1:].isdigit()]
    doc["next_id"] = max(doc["next_id"], max(allocated, default=0) + 1)
    active = raw.get("active_root")
    doc["active_root"] = str(active) if active is not None and str(active) in by_id and by_id[str(active)]["parent_id"] is None else None
    if doc["active_root"]:
        doc["phase"] = by_id[doc["active_root"]]["phase"]
    focus_ids = raw.get("focus_ids", [])
    if not isinstance(focus_ids, list) or len(focus_ids) > len(by_id) or any(
        not isinstance(n, str) or n not in by_id for n in focus_ids
    ) or len(set(focus_ids)) != len(focus_ids):
        raise WorkPlanError("invalid stored Work focus; no implicit release is allowed")
    doc["focus_ids"] = list(focus_ids)
    return doc


def root_phase(doc: dict[str, Any], root_id: str) -> str:
    # The document phase is the active-root mirror; inactive approvals live on roots.
    if root_id == doc.get("active_root"):
        return doc["phase"]
    return _index(doc)[root_id].get("phase", PHASE_PLANNING)


def set_phase(doc: dict[str, Any], phase: str, root_id: str | None = None) -> None:
    if phase not in PHASES:
        raise WorkPlanError("invalid Work phase")
    root_id = root_id or doc.get("active_root")
    if root_id:
        _index(doc)[root_id]["phase"] = phase
    if root_id == doc.get("active_root"):
        doc["phase"] = phase


def _activate(doc: dict[str, Any], root_id: str | None) -> None:
    old = doc.get("active_root")
    if old in _index(doc):
        _index(doc)[old]["phase"] = doc["phase"]
    phase = root_phase(doc, root_id) if root_id else PHASE_PLANNING
    doc["active_root"] = root_id
    doc["phase"] = phase


def _insert_root_by_priority(doc: dict[str, Any], node: dict[str, Any]) -> None:
    """Place a new goal in the node list by queue priority.

    Roots are kept in queue order inside doc['nodes']: active first, then
    P1..P5, unranked last, stable within a rank. The new goal never takes
    the active slot and never lands before a done root's history position
    relative to other done roots — only unfinished queue order is sorted.
    """
    active = doc.get("active_root")
    nodes = doc["nodes"]
    position = len(nodes)
    for index, existing in enumerate(nodes):
        if existing["parent_id"] is not None or existing["status"] == "done" or existing["id"] == active:
            continue
        if priority_rank(existing.get("priority")) > priority_rank(node.get("priority")):
            position = index
            break
    nodes.insert(position, node)


def add_node(doc: dict[str, Any], parent_id: str | None, title: Any, note: Any = "", *, now: float | None = None,
             ref: Any = "", waiting_on: Any = "", within_scope: bool = False,
             priority: Any = "") -> str:
    stamp = time.time() if now is None else now
    by_id = _index(doc)
    # A new goal waits behind an unfinished active goal instead of taking its place.
    queue_behind = parent_id is None and _open_active_root(doc, by_id) is not None
    if parent_id is not None:
        parent_id = str(parent_id)
        if parent_id not in by_id:
            raise WorkPlanError(f"unknown parent node {parent_id}")
        if _depth(by_id, parent_id) + 1 > MAX_DEPTH:
            raise WorkPlanError(f"plan depth is limited to {MAX_DEPTH} levels below the goal")
        if priority:
            raise WorkPlanError("priority belongs to a goal, not a detail item")
    elif priority not in ("", *PRIORITIES):
        raise WorkPlanError("priority must be one of P1, P2, P3, P4, P5")
    clean = _clip(title, MAX_TITLE_CHARS)
    if not clean:
        raise WorkPlanError("node title must not be empty")
    node_id = f"n{doc['next_id']}"
    doc["next_id"] += 1
    node = {
        "id": node_id,
        "parent_id": parent_id,
        "title": clean,
        "status": "pending",
        "note": _clip(note, MAX_TEXT_CHARS),
        "evidence": "",
        "waiting_on": _line(waiting_on, MAX_WAIT_CHARS),
        "ref": _line(ref, MAX_REF_CHARS),
        "urgent": False,
        "created_at": stamp,
        "updated_at": stamp,
    }
    if parent_id is None:
        node["phase"] = PHASE_PLANNING
        node["priority"] = priority
        if queue_behind and priority:
            _insert_root_by_priority(doc, node)
        else:
            doc["nodes"].append(node)
        if not queue_behind:
            _activate(doc, node_id)
    else:
        doc["nodes"].append(node)
        root = _root_of(_index(doc), parent_id)
        if not within_scope:
            set_phase(doc, PHASE_PLANNING, root)
        _reopen_ancestors(doc, parent_id, stamp)
    return node_id


def add_subtree(doc: dict[str, Any], parent_id: str | None, spec: Any, *, now: float | None = None,
                within_scope: bool = False) -> str:
    """Add a nested ``{title, note, children: [...]}`` spec below ``parent_id``."""

    if not isinstance(spec, dict):
        raise WorkPlanError("a plan node must be an object with a title")
    node_id = add_node(doc, parent_id, spec.get("title"), spec.get("note", ""), now=now,
                       ref=spec.get("ref", ""), waiting_on=spec.get("waiting_on", ""), within_scope=within_scope,
                       priority=spec.get("priority", ""))
    children = spec.get("children", [])
    if not isinstance(children, list):
        raise WorkPlanError("children must be a list")
    for child in children:
        add_subtree(doc, node_id, child, now=now, within_scope=within_scope)
    return node_id


def set_plan(doc: dict[str, Any], spec: Any, root_id: str | None = None, *, now: float | None = None) -> str:
    """Create a goal from a nested spec, or replace an existing goal's subtree."""

    if root_id is None:
        return add_subtree(doc, None, spec, now=now)
    by_id = _index(doc)
    root_id = str(root_id)
    if root_id not in by_id or by_id[root_id]["parent_id"] is not None:
        raise WorkPlanError(f"{root_id} is not a goal")
    if not isinstance(spec, dict):
        raise WorkPlanError("a plan must be an object with a title")
    children = spec.get("children", [])
    if not isinstance(children, list):
        raise WorkPlanError("children must be a list")
    if any(n in doc.get("focus_ids", []) for n, depth in _walk(doc, root_id) if depth):
        raise WorkPlanError("clear focus before replacing focused descendants")
    # Re-planning a queued or finished goal must not take the slot of the unfinished active goal.
    open_active = _open_active_root(doc, by_id)
    trial = copy.deepcopy(doc)
    _remove_descendants(trial, root_id)
    root = _index(trial)[root_id]
    if spec.get("title"):
        root["title"] = _clip(spec.get("title"), MAX_TITLE_CHARS)
    if "note" in spec:
        root["note"] = _clip(spec.get("note"), MAX_TEXT_CHARS)
    for key, limit in (("ref", MAX_REF_CHARS), ("waiting_on", MAX_WAIT_CHARS)):
        if key in spec:
            root[key] = _line(spec[key], limit)
    if "priority" in spec:
        if spec["priority"] not in ("", *PRIORITIES):
            raise WorkPlanError("priority must be one of P1, P2, P3, P4, P5")
        root["priority"] = spec["priority"]
    root["status"] = "pending"
    root.pop("completed_at", None)
    root.pop("expired_done_steps", None)
    root["updated_at"] = time.time() if now is None else now
    for child in children:
        add_subtree(trial, root_id, child, now=now)
    set_phase(trial, PHASE_PLANNING, root_id)
    if open_active is None or open_active == root_id:
        _activate(trial, root_id)
    doc.clear()
    doc.update(trial)
    return root_id


def update_node(doc: dict[str, Any], node_id: str, *, status: Any = None, title: Any = None,
                note: Any = None, evidence: Any = None, now: float | None = None,
                waiting_on: Any = None, ref: Any = None, priority: Any = None) -> dict[str, Any]:
    stamp = time.time() if now is None else now
    by_id = _index(doc)
    node_id = str(node_id)
    if node_id not in by_id:
        raise WorkPlanError(f"unknown node {node_id}")
    node = by_id[node_id]
    candidate = dict(node)
    if status is not None:
        if status not in STATUSES:
            raise WorkPlanError(f"status must be one of {', '.join(STATUSES)}")
        if status == "done" and _children(doc, node_id) and not all(
            child["status"] == "done" for child in _children(doc, node_id)
        ):
            raise WorkPlanError(f"{node_id} still has unfinished children; finish them first")
        if status == "done" and not _children(doc, node_id) and not _clip(
            evidence if evidence is not None else node.get("evidence"), MAX_TEXT_CHARS
        ):
            raise WorkPlanError("a completed leaf needs verification evidence")
        candidate["status"] = status
    if title is not None:
        clean = _clip(title, MAX_TITLE_CHARS)
        if not clean:
            raise WorkPlanError("node title must not be empty")
        candidate["title"] = clean
    if note is not None:
        candidate["note"] = _clip(note, MAX_TEXT_CHARS)
    if evidence is not None:
        candidate["evidence"] = _clip(evidence, MAX_TEXT_CHARS)
    if waiting_on is not None:
        candidate["waiting_on"] = _line(waiting_on, MAX_WAIT_CHARS)
    if ref is not None:
        candidate["ref"] = _line(ref, MAX_REF_CHARS)
    if priority is not None:
        if node["parent_id"] is not None:
            raise WorkPlanError("priority belongs to a goal, not a detail item")
        if priority not in ("", *PRIORITIES):
            raise WorkPlanError("priority must be one of P1, P2, P3, P4, P5")
        candidate["priority"] = priority
    if candidate == node:
        return node
    scope_changed = (candidate["title"] != node["title"] or candidate["note"] != node["note"]
                     or (node["status"] == "done" and candidate["status"] != "done"))
    if candidate["status"] == "done" and node["status"] != "done":
        candidate["completed_at"] = stamp
    elif candidate["status"] != "done" and "completed_at" in candidate:
        candidate["completed_at"] = None
    priority_changed = candidate.get("priority") != node.get("priority")
    node.update(candidate)
    if priority_changed and node["parent_id"] is None and node["status"] != "done" and node_id != doc.get("active_root"):
        # Reinsert this queued goal at its new rank; children keep parent links.
        siblings = [n for n in doc["nodes"] if n["id"] != node_id]
        doc["nodes"] = siblings
        _insert_root_by_priority(doc, node)
    if scope_changed:
        set_phase(doc, PHASE_PLANNING, _root_of(by_id, node_id))
    node["updated_at"] = stamp
    if node["parent_id"] is not None:
        if status == "done":
            _complete_ancestors(doc, node["parent_id"], stamp)
        elif status in ("in_progress", "pending", "blocked"):
            _reopen_ancestors(doc, node["parent_id"], stamp)
    _release_finished_focus(doc)
    root = _root_of(by_id, node_id)
    if by_id[root]["status"] == "done":
        set_phase(doc, PHASE_PLANNING, root)
    if root == doc.get("active_root") and by_id[root]["status"] == "done":
        set_phase(doc, PHASE_PLANNING)
        _advance_queue(doc)
    return node


COMPLETED_RETENTION_SECONDS = 86400


def expire_completed(doc: dict[str, Any], *, now: float | None = None) -> int:
    """Remove fully expired done subtrees, retaining progress and explicit focus holds."""
    stamp = time.time() if now is None else now
    if not math.isfinite(stamp) or stamp <= 0:
        raise WorkPlanError("invalid retention clock")
    by_id = _index(doc)
    for node in doc["nodes"]:
        if node["status"] == "done" and not node.get("completed_at"):
            old = node.get("updated_at")
            node["completed_at"] = old if type(old) in (int, float) and math.isfinite(old) and old > 0 else stamp
    # Legacy completed parents with unfinished descendants are not candidates.
    focused = set(doc.get("focus_ids", []))
    protected = set(focused)
    for node_id in focused:
        current = by_id[node_id]
        while current["parent_id"] is not None:
            protected.add(current["parent_id"])
            current = by_id[current["parent_id"]]
        protected.update(n for n, _ in _walk(doc, node_id))
    expired = set()
    for node in doc["nodes"]:
        if node["id"] in protected or node["status"] != "done":
            continue
        subtree = [by_id[n] for n, _ in _walk(doc, node["id"])]
        if all(child["status"] == "done" and stamp - child["completed_at"] >= COMPLETED_RETENTION_SECONDS
               for child in subtree):
            expired.add(node["id"])
    roots = [by_id[n] for n in expired if by_id[n]["parent_id"] not in expired]
    doomed = set()
    for node in roots:
        subtree = {n for n, _ in _walk(doc, node["id"])}
        if node["parent_id"] is not None:
            count = sum(by_id[n].get("expired_done_steps", 0) for n in subtree)
            count += sum(not _children(doc, n) and not by_id[n].get("expired_done_steps") for n in subtree)
            parent = by_id[node["parent_id"]]
            parent["expired_done_steps"] = parent.get("expired_done_steps", 0) + count
        doomed.update(subtree)
    if doomed:
        doc["nodes"] = [node for node in doc["nodes"] if node["id"] not in doomed]
        if doc.get("active_root") in doomed:
            remaining = queued_roots(doc)
            _activate(doc, remaining[0]["id"] if remaining else None)
    return len(doomed)


def remove_node(doc: dict[str, Any], node_id: str) -> int:
    by_id = _index(doc)
    node_id = str(node_id)
    if node_id not in by_id:
        raise WorkPlanError(f"unknown node {node_id}")
    if set(doc.get("focus_ids", [])) & {n for n, _ in _walk(doc, node_id)}:
        raise WorkPlanError("clear focus before removing a focused item")
    removed = _remove_descendants(doc, node_id) + 1
    parent_id = by_id[node_id]["parent_id"]
    doc["nodes"] = [node for node in doc["nodes"] if node["id"] != node_id]
    if parent_id is not None:
        _complete_ancestors(doc, parent_id, time.time())
        active = _index(doc).get(doc.get("active_root"))
        if active and active["status"] == "done":
            set_phase(doc, PHASE_PLANNING)
            _advance_queue(doc)
    if parent_id is None and doc.get("active_root") == node_id:
        remaining = [node["id"] for node in doc["nodes"] if node["parent_id"] is None and node["status"] != "done"]
        _activate(doc, remaining[0] if remaining else None)
    return removed


def select_root(doc: dict[str, Any], root_id: str, *, user_requested: bool = False) -> None:
    by_id = _index(doc)
    root_id = str(root_id)
    if root_id not in by_id or by_id[root_id]["parent_id"] is not None:
        raise WorkPlanError(f"{root_id} is not a goal")
    if doc.get("active_root") != root_id:
        # An unfinished active goal yields without a user request only when none of its steps can run and
        # this goal holds the first runnable step in queue order (never past an urgent hold).
        if _open_active_root(doc, by_id) is not None and not user_requested:
            nxt = continuation(doc)["next"]
            if nxt is None or nxt["root_id"] != root_id:
                raise WorkPlanError(_select_refusal(doc, root_id))
        _activate(doc, root_id)


def reorder_root(doc: dict[str, Any], root_id: str, position: int, *, user_requested: bool = False) -> None:
    """Order unfinished roots; the active slot changes only on an explicit request."""

    roots = [node for node in doc["nodes"] if node["parent_id"] is None and node["status"] != "done"]
    active = doc.get("active_root")
    roots.sort(key=lambda node: node["id"] != active)
    previous = list(roots)
    selected = next((node for node in roots if node["id"] == root_id), None)
    if selected is None:
        raise WorkPlanError(f"{root_id} is not an unfinished goal")
    if isinstance(position, bool) or not isinstance(position, int) or not 1 <= position <= len(roots):
        raise WorkPlanError(f"position must be between 1 and {len(roots)}")
    roots.remove(selected)
    roots.insert(position - 1, selected)
    if roots[0]["id"] != active and not user_requested:
        raise WorkPlanError("moving the active goal requires an explicit user request (user_requested=true)")
    if roots == previous and roots[0]["id"] == active:
        return
    ordered = iter(roots)
    doc["nodes"] = [next(ordered) if node["parent_id"] is None and node["status"] != "done" else node
                    for node in doc["nodes"]]
    if roots[0]["id"] != active:
        _activate(doc, roots[0]["id"])


def reorder_node(doc: dict[str, Any], node_id: str, position: int, *, user_requested: bool = False) -> None:
    """Move a node within its siblings (1-based), never changing parentage.

    Root ordering retains ``reorder_root``'s unfinished-goal and active-slot
    guards. Non-root ordering includes finished siblings and leaves all node
    data, descendants, timestamps and execution state unchanged.
    """

    by_id = _index(doc)
    node_id = str(node_id)
    if node_id not in by_id:
        raise WorkPlanError(f"unknown node {node_id}")
    selected = by_id[node_id]
    parent_id = selected["parent_id"]
    if parent_id is None:
        return reorder_root(doc, node_id, position, user_requested=user_requested)
    siblings = _children(doc, parent_id)
    if isinstance(position, bool) or not isinstance(position, int) or not 1 <= position <= len(siblings):
        raise WorkPlanError(f"position must be between 1 and {len(siblings)}")
    if siblings[position - 1]["id"] == node_id:
        return
    siblings.remove(selected)
    siblings.insert(position - 1, selected)
    ordered = iter(siblings)
    doc["nodes"] = [next(ordered) if node["parent_id"] == parent_id else node for node in doc["nodes"]]


def set_focus(doc: dict[str, Any], node_ids: Any, *, user_requested: bool = False) -> None:
    """Hold unrelated Work until explicit release; prioritize existing subtrees."""
    if not user_requested:
        raise WorkPlanError("changing focus requires an explicit user request (user_requested=true)")
    by_id = _index(doc)
    if not isinstance(node_ids, list) or len(node_ids) > MAX_FOCUS_BATCH or any(
        not isinstance(n, str) or n not in by_id for n in node_ids
    ) or len(set(node_ids)) != len(node_ids):
        raise WorkPlanError("node_ids must be a unique list of existing node ids")
    active = by_id.get(doc.get("active_root"))
    if node_ids and active and active.get("urgent") and active["status"] != "done" and any(
        _root_of(by_id, n) != active["id"] for n in node_ids
    ):
        raise WorkPlanError("focus cannot release an unfinished urgent goal's hold")
    doc["focus_ids"] = list(node_ids)
    if node_ids:
        roots = list(dict.fromkeys(_root_of(by_id, n) for n in node_ids if by_id[n]["status"] != "done"))
        for root in reversed(roots):
            reorder_root(doc, root, 1, user_requested=True)
        nxt = continuation(doc)["next"]
        if nxt:
            _activate(doc, nxt["root_id"])
    elif active and active["status"] == "done":
        _advance_queue(doc)


def _focus_leaves(doc: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(leaf for n in doc.get("focus_ids", []) for leaf in _leaves(doc, n)))


def _release_finished_focus(doc: dict[str, Any]) -> None:
    """Focus is a priority order, not a hold: release it once every focused leaf is done."""

    if doc.get("focus_ids"):
        by_id = _index(doc)
        if all(by_id[leaf]["status"] == "done" for leaf in _focus_leaves(doc)):
            doc["focus_ids"] = []


def add_urgent(doc: dict[str, Any], spec: Any, *, user_requested: bool = False) -> str:
    if not user_requested:
        raise WorkPlanError("urgent mode requires an explicit user request (user_requested=true)")
    trial = copy.deepcopy(doc)
    root = add_subtree(trial, None, spec)
    _index(trial)[root]["urgent"] = True
    reorder_root(trial, root, 1, user_requested=True)
    doc.clear()
    doc.update(trial)
    return root


def continuation(doc: dict[str, Any]) -> dict[str, Any]:
    """Select runnable leaves, respecting ancestor waits, blocks and urgent holds.

    Focus only orders work: focused runnable leaves come first, then every other
    runnable leaf. A focused item that waits or is blocked never stops unrelated
    approved work from running.
    """

    by_id = _index(doc)
    focus_leaves = _focus_leaves(doc) if doc.get("focus_ids") else []
    roots, available, waiting, blocked = _runnable_by_root(doc, by_id)
    flat = [n for group in available for n in group]
    focused = sorted((n for n in flat if n["id"] in focus_leaves), key=lambda n: focus_leaves.index(n["id"]))
    chosen = focused or flat
    nxt = chosen[0] if chosen else None
    target_root = _root_of(by_id, nxt["id"]) if nxt else None
    active = by_id.get(doc.get("active_root"))
    held = bool(active and active.get("urgent") and active["status"] != "done"
                and any(root['id'] == active['id'] and rows for root, rows in zip(roots, available)))
    return {
        "actionable": len(flat),
        "next": dict(_node_summary(nxt), path=path_titles(doc, nxt["id"]), root_id=target_root,
                     phase=root_phase(doc, target_root)) if nxt else None,
        "waiting": waiting[:MAX_WAITING_IN_CONTINUATION],
        "blocked": blocked,
        # Named blocked items (the blocking node itself), so a stop that finds
        # no runnable work can re-verify whether each block still holds
        # instead of treating "blocked" as permanent.
        "blocked_items": _blocked_items(doc, by_id)[:MAX_BLOCKED_IN_CONTINUATION],
        "held": len(queued_roots(doc)) if held else 0,
    }


def runnable_leaves(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Ordered host scheduler projection of every runnable approved leaf."""

    by_id = _index(doc)
    focus_leaves = _focus_leaves(doc) if doc.get("focus_ids") else []
    roots, available, _, _ = _runnable_by_root(doc, by_id)
    rows = []
    for root, candidates in zip(roots, available):
        if root_phase(doc, root["id"]) != PHASE_EXECUTING:
            continue
        for leaf in candidates:
            rows.append({
                "item_id": leaf["id"],
                "root_id": root["id"],
                "priority": root.get("priority") if root.get("priority") in PRIORITIES else "P5",
                "created_at": leaf.get("created_at", leaf.get("updated_at", 0)),
                "focused": leaf["id"] in focus_leaves,
                "title": leaf.get("title", ""),
                "note": leaf.get("note", ""),
                "path": path_titles(doc, leaf["id"]),
            })
    rows.sort(key=lambda row: (not row["focused"], priority_rank(row["priority"]),
                               -float(row["created_at"]), row["item_id"]))
    return rows


def wait_diagnostics(doc: dict[str, Any]) -> dict[str, Any]:
    """Expose exact gates without guessing whether free-form waits are resolved."""
    by_id = _index(doc)
    rows = []
    for leaf in doc["nodes"]:
        if leaf["status"] == "done" or _children(doc, leaf["id"]):
            continue
        path = [leaf]
        while path[-1]["parent_id"] is not None:
            path.append(by_id[path[-1]["parent_id"]])
        gates = [n for n in reversed(path) if n.get("waiting_on") or n["status"] == "blocked"]
        if gates:
            rows.append({"item_id": leaf["id"], "root_id": path[-1]["id"],
                         "gate_ids": [n["id"] for n in gates],
                         "inherited": any(n["id"] != leaf["id"] for n in gates),
                         "status_wait_conflict": leaf["status"] == "in_progress"})
    return {"excluded_leaves": len(rows), "items": rows[:20], "truncated": len(rows) > 20}


def _blocked_items(doc: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Every unfinished blocked node, root first, with its recorded reason."""
    items = []
    for node in doc["nodes"]:
        if node["status"] != "blocked":
            continue
        items.append({
            "id": node["id"],
            "title": node["title"],
            "waiting_on": node.get("waiting_on", ""),
            "root_id": _root_of(by_id, node["id"]),
            "path": path_titles(doc, node["id"]),
        })
    items.sort(key=lambda row: (row["root_id"] != doc.get("active_root"), row["id"]))
    return items


def _runnable_by_root(doc: dict[str, Any], by_id: dict[str, dict[str, Any]]
                      ) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]], list[dict[str, Any]], int]:
    """Unfinished roots in activation order with each one's runnable leaves (in-progress first)."""

    active = by_id.get(doc.get("active_root"))
    held = bool(active and active.get("urgent") and active["status"] != "done")
    roots = ([active] if active and active["status"] != "done" else []) + queued_roots(doc)
    waiting: list[dict[str, Any]] = []
    blocked = 0
    available: list[list[dict[str, Any]]] = []
    for root in roots:
        candidates = []
        for leaf_id in _leaves(doc, root["id"]):
            leaf = by_id[leaf_id]
            if leaf["status"] == "done":
                continue
            path = []
            current = leaf
            while True:
                path.append(current)
                if current["parent_id"] is None:
                    break
                current = by_id[current["parent_id"]]
            wait = next((node for node in reversed(path) if node.get("waiting_on")), None)
            if wait:
                if not any(row["id"] == wait["id"] for row in waiting):
                    waiting.append({"id": wait["id"], "title": wait["title"], "waiting_on": wait["waiting_on"]})
            elif any(node["status"] == "blocked" for node in path):
                blocked += 1
            elif not held or root is active:
                candidates.append(leaf)
        candidates.sort(key=lambda node: node["status"] != "in_progress")
        available.append(candidates)
        if root is active and held and not candidates:
            # An urgent priority is not a workspace-wide dependency lock.
            # Preserve its waits while independent queued goals use idle slots.
            held = False
    return roots, available, waiting, blocked


def _select_refusal(doc: dict[str, Any], root_id: str) -> str:
    """Why an unrequested switch to root_id was refused, with the facts the guard used."""

    by_id = _index(doc)
    roots, available, waiting, blocked = _runnable_by_root(doc, by_id)
    active = doc.get("active_root")
    runnable = {root["id"]: rows for root, rows in zip(roots, available)}
    nxt = continuation(doc)["next"]
    if by_id[active].get("urgent"):
        reason = f"the active goal {active} is urgent and holds every other goal"
    elif nxt is None:
        reason = "no goal has a runnable step"
    elif nxt["root_id"] == active:
        reason = f"the active goal {active} still has a runnable step ({nxt['id']})"
    else:
        reason = f"the first runnable step ({nxt['id']}) is in goal {nxt['root_id']}"
    requested = ("is held" if by_id[active].get("urgent")
                 else "has a runnable step" if runnable.get(root_id) else "has no runnable step")
    return (f"changing the active goal requires an explicit user request (user_requested=true): "
            f"without it only the goal holding the first runnable step can be selected, and {reason}; "
            f"requested goal {root_id} {requested} [active {active} {root_phase(doc, active)}, "
            f"waiting {len(waiting)}, blocked steps {blocked}, version {doc.get('version', 0)}]")


def _node_summary(node: dict[str, Any]) -> dict[str, Any]:
    result = {key: node.get(key, "") for key in ("id", "title", "status", "ref", "waiting_on", "urgent")}
    if node.get("parent_id") is None:
        result["phase"] = node.get("phase", PHASE_PLANNING)
        result["priority"] = node.get("priority", "")
    return result


def attention(doc: dict[str, Any], node_ids: list[str], *, include_self: bool = True) -> dict[str, int]:
    """Count unfinished nodes in these subtrees that are blocked or wait on something.

    A blocked node counts as blocked even when it also names what it waits on.
    """
    by_id = _index(doc)
    counts = {"waiting": 0, "blocked": 0}
    for top in node_ids:
        for node_id, depth in _walk(doc, top):
            node = by_id[node_id]
            if (depth == 0 and not include_self) or node["status"] == "done":
                continue
            if node["status"] == "blocked":
                counts["blocked"] += 1
            elif node.get("waiting_on"):
                counts["waiting"] += 1
    return counts


def readiness(doc: dict[str, Any]) -> tuple[bool, str]:
    root = doc.get("active_root")
    if root is None:
        return False, "no active goal; create one with work_plan_set"
    by_id = _index(doc)
    leaves = _leaves(doc, root)
    if not leaves or leaves == [root]:
        return False, "the goal has no steps yet; decompose it into phases, tasks and steps"
    deepest = max(_depth(by_id, leaf) for leaf in leaves)
    if deepest < READY_MIN_LEAF_DEPTH:
        return False, (
            "decompose deeper before executing: at least goal -> phase -> task, "
            "and keep splitting until each leaf is one verifiable action"
        )
    if all(by_id[leaf]["status"] == "done" for leaf in leaves):
        return False, "every step of the active goal is already done; start a new goal or select a queued one"
    return True, "ready"


def progress(doc: dict[str, Any], root: str | None = None) -> tuple[int, int]:
    root = root or doc.get("active_root")
    if root is None:
        return 0, 0
    by_id = _index(doc)
    leaves = [leaf for leaf in _leaves(doc, root) if leaf != root]
    expired = sum(by_id[n].get("expired_done_steps", 0) for n, _ in _walk(doc, root))
    leaves = [leaf for leaf in leaves if not (by_id[leaf]["status"] == "done" and by_id[leaf].get("expired_done_steps"))]
    return expired + sum(1 for leaf in leaves if by_id[leaf]["status"] == "done"), expired + len(leaves)


def next_leaf(doc: dict[str, Any], root: str | None = None) -> dict[str, Any] | None:
    root = root or doc.get("active_root")
    if root is None:
        return None
    by_id = _index(doc)
    leaves = [leaf for leaf in _leaves(doc, root) if leaf != root]
    for wanted in ("in_progress", "pending", "blocked"):
        for leaf in leaves:
            if by_id[leaf]["status"] == wanted:
                return by_id[leaf]
    return None


def path_titles(doc: dict[str, Any], node_id: str) -> list[str]:
    by_id = _index(doc)
    titles: list[str] = []
    current: str | None = node_id
    while current is not None:
        node = by_id[current]
        titles.append(node["title"])
        current = node["parent_id"]
    return list(reversed(titles))


def snapshot(doc: dict[str, Any], *, enabled: bool = True, approval_ask: bool = False) -> dict[str, Any]:
    root = doc.get("active_root")
    by_id = _index(doc)
    done, total = progress(doc, root)
    ready, reason = readiness(doc)
    resume = continuation(doc)
    # The next runnable step may be in another goal when the active one waits or is blocked.
    nxt = resume["next"] or next_leaf(doc, root)
    current = resume["next"]
    if current is not None:
        current = dict(current, runnable=True)
    elif nxt is not None:
        path = []
        node = nxt
        while True:
            path.append(node)
            if node["parent_id"] is None:
                break
            node = by_id[node["parent_id"]]
        wait = next((node for node in reversed(path) if node.get("waiting_on")), None)
        blocked = next((node for node in reversed(path) if node["status"] == "blocked"), None)
        current = dict(_node_summary(nxt), runnable=False,
                       status="blocked" if blocked else nxt["status"],
                       waiting_on=wait["waiting_on"] if wait else "",
                       held_by=(wait or blocked or {}).get("id", ""))
    return {
        "enabled": bool(enabled),
        "phase": doc.get("phase", PHASE_PLANNING),
        "approval": "ask" if approval_ask else "auto",
        "version": doc.get("version", 0),
        "active_root": _node_summary(by_id[root]) if root else None,
        "continuation": resume,
        "display_current": current,
        "wait_diagnostics": wait_diagnostics(doc),
        "focus": {"node_ids": list(doc.get("focus_ids", [])),
                  "exhausted": bool(doc.get("focus_ids")) and all(by_id[n]["status"] == "done" for n in _focus_leaves(doc))},
        "done": done,
        "total": total,
        # Waiting/blocked counts over the active goal, so items below the displayed depth stay visible.
        "attention": attention(doc, [root], include_self=False) if root else {"waiting": 0, "blocked": 0},
        "next": {"id": nxt["id"], "title": nxt["title"], "status": nxt["status"], "waiting_on": nxt.get("waiting_on", ""),
                 "path": path_titles(doc, nxt["id"])} if nxt else None,
        "breakdown": [
            dict(_node_summary(child),
                 done=progress(doc, child["id"])[0] if _children(doc, child["id"]) else int(child["status"] == "done"),
                 total=progress(doc, child["id"])[1] if _children(doc, child["id"]) else 1,
                 children=[dict(_node_summary(node), hidden=attention(doc, [node["id"]], include_self=False))
                           for node in _children(doc, child["id"])[:MAX_BRANCH_CHILDREN_IN_SNAPSHOT]],
                 more=max(0, len(_children(doc, child["id"])) - MAX_BRANCH_CHILDREN_IN_SNAPSHOT),
                 more_attention=attention(doc, [node["id"] for node in _children(doc, child["id"])[MAX_BRANCH_CHILDREN_IN_SNAPSHOT:]]))
            for child in _children(doc, root)
        ] if root else [],
        # Complete display index; summaries only (no notes,
        # evidence or queued descendants). Keeps roots beyond the short queue
        # preview and completed history reachable without loading other contexts.
        "root_index": [dict(_node_summary(node),
                            held=bool(resume['held'] and node['id'] != root and node['status'] != 'done'))
                       for node in doc["nodes"] if node["parent_id"] is None],
        "completed_active": [_node_summary(by_id[node_id]) for node_id, _ in _walk(doc, root)
                             if node_id != root and by_id[node_id]["status"] == "done"] if root else [],
        "ready": ready,
        "ready_reason": reason,
        "goals": sum(1 for node in doc["nodes"] if node["parent_id"] is None),
        # Unfinished goals waiting behind the active one, in the order they become active.
        "queue": [
            dict(_node_summary(node), done=progress(doc, node["id"])[0], total=progress(doc, node["id"])[1],
                 held=bool(resume['held']))
            for node in queued_roots(doc)[:MAX_QUEUE_IN_SNAPSHOT]
        ],
    }


def render(doc: dict[str, Any], *, max_lines: int = 80) -> str:
    """Render the active goal as an indented tree, bounded to ``max_lines``."""

    root = doc.get("active_root")
    done, total = progress(doc, root)
    lines = [f"Work phase: {doc.get('phase', PHASE_PLANNING)}"]
    if root is None:
        lines.append("No active goal.")
    else:
        lines.append(f"Active goal progress: {done}/{total} steps done")
        for node_id, depth in _walk(doc, root):
            node = _index(doc)[node_id]
            mark = STATUS_MARKS[node["status"]]
            text = f"{'  ' * depth}{mark} {node['id']} {_display_title(node)}"
            if node.get("evidence"):
                text += f"  (evidence: {_clip(node['evidence'], 120)})"
            lines.append(text)
            if len(lines) >= max_lines:
                lines.append(f"... {len(doc['nodes'])} nodes in total; call work_status for the rest")
                break
    queued = queued_roots(doc)
    if queued:
        lines.append("Queued goals (next first): " + "; ".join(
            f"{node['id']} {_display_title(node)} ({node['status']})"
            + (" [held]" if _index(doc).get(root, {}).get("urgent") and _index(doc)[root]["status"] != "done" else "")
            for node in queued[:10]))
    finished = [node for node in doc["nodes"] if node["parent_id"] is None and node["id"] != root and node["status"] == "done"]
    if finished:
        lines.append("Finished goals: " + "; ".join(f"{node['id']} {node['title']}" for node in finished[-10:]))
    return "\n".join(lines)


def _walk(doc: dict[str, Any], root: str) -> list[tuple[str, int]]:
    order: list[tuple[str, int]] = []
    stack: list[tuple[str, int]] = [(root, 0)]
    while stack:
        node_id, depth = stack.pop()
        order.append((node_id, depth))
        for child in reversed(_children(doc, node_id)):
            stack.append((child["id"], depth + 1))
    return order


def _leaves(doc: dict[str, Any], root: str) -> list[str]:
    return [node_id for node_id, _ in _walk(doc, root) if not _children(doc, node_id)]


def _children(doc: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
    return [node for node in doc["nodes"] if node["parent_id"] == node_id]


def _index(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["id"]: node for node in doc["nodes"]}


def _depth(by_id: dict[str, dict[str, Any]], node_id: str) -> int:
    depth = 0
    seen: set[str] = set()
    current = by_id[node_id]["parent_id"]
    while current is not None:
        if current in seen or depth > MAX_DEPTH:
            raise WorkPlanError("work plan contains a cycle")
        seen.add(current)
        depth += 1
        current = by_id[current]["parent_id"]
    return depth


def _root_of(by_id: dict[str, dict[str, Any]], node_id: str) -> str:
    current = node_id
    while by_id[current]["parent_id"] is not None:
        current = by_id[current]["parent_id"]
    return current


def _open_active_root(doc: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> str | None:
    active = doc.get("active_root")
    return active if active in by_id and by_id[active]["status"] != "done" else None


def queued_roots(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Unfinished goals other than the active one, in the order they become active."""

    active = doc.get("active_root")
    return [node for node in doc["nodes"] if node["parent_id"] is None and node["status"] != "done" and node["id"] != active]


def reorder_queued_root(doc: dict[str, Any], root_id: str, position: int) -> None:
    """Move a waiting goal to a 1-based place among queued_roots; the active goal never moves."""

    queued = queued_roots(doc)
    selected = next((node for node in queued if node["id"] == root_id), None)
    if selected is None:
        raise WorkPlanError(f"{root_id} is not a waiting goal")
    if isinstance(position, bool) or not isinstance(position, int) or not 1 <= position <= len(queued):
        raise WorkPlanError(f"queue position must be between 1 and {len(queued)}")
    ordered = [node for node in queued if node["id"] != root_id]
    ordered.insert(position - 1, selected)
    slots = iter(ordered)
    active = doc.get("active_root")
    doc["nodes"] = [next(slots) if node["parent_id"] is None and node["status"] != "done" and node["id"] != active
                    else node for node in doc["nodes"]]


def queue_position(doc: dict[str, Any], node_id: str) -> dict[str, Any]:
    """Where a node's goal stands now: active, queued at N of M, or done."""

    by_id = _index(doc)
    if node_id not in by_id:
        raise WorkPlanError(f"unknown node {node_id}")
    root = _root_of(by_id, node_id)
    queued = [node["id"] for node in queued_roots(doc)]
    state = ("done" if by_id[root]["status"] == "done" else "active" if root == doc.get("active_root")
             else "queued")
    position = queued.index(root) + 1 if state == "queued" else 0
    return {"root_id": root, "state": state, "position": position, "ahead": max(0, position - 1),
            "total": len(queued), "active_root": doc.get("active_root"),
            "priority": by_id[root].get("priority", ""), "phase": root_phase(doc, root)}


def _advance_queue(doc: dict[str, Any]) -> None:
    if doc.get("focus_ids"):
        nxt = continuation(doc)["next"]
        if nxt:
            _activate(doc, nxt["root_id"])
        return
    queued = queued_roots(doc)
    if queued:
        _activate(doc, queued[0]["id"])


def _remove_descendants(doc: dict[str, Any], node_id: str) -> int:
    doomed: set[str] = set()
    frontier = [node_id]
    while frontier:
        current = frontier.pop()
        for child in _children(doc, current):
            doomed.add(child["id"])
            frontier.append(child["id"])
    doc["nodes"] = [node for node in doc["nodes"] if node["id"] not in doomed]
    return len(doomed)


def _complete_ancestors(doc: dict[str, Any], node_id: str | None, stamp: float) -> None:
    by_id = _index(doc)
    while node_id is not None:
        node = by_id[node_id]
        children = _children(doc, node_id)
        if not children or not all(child["status"] == "done" for child in children):
            if node["status"] == "pending":
                node["status"] = "in_progress"
                node["updated_at"] = stamp
            node_id = node["parent_id"]
            continue
        if node["status"] != "done":
            node["completed_at"] = stamp
        node["status"] = "done"
        node["updated_at"] = stamp
        node_id = node["parent_id"]


def _reopen_ancestors(doc: dict[str, Any], node_id: str | None, stamp: float) -> None:
    by_id = _index(doc)
    while node_id is not None:
        node = by_id[node_id]
        if node["status"] in ("done", "pending"):
            node["status"] = "in_progress" if any(
                child["status"] in ("done", "in_progress") for child in _children(doc, node_id)
            ) else "pending"
            node["updated_at"] = stamp
            if "completed_at" in node:
                node["completed_at"] = None
        node_id = node["parent_id"]


def _display_title(node: dict[str, Any]) -> str:
    title = f"[ {node['ref']} ] {node['title']}" if node.get("ref") else node["title"]
    if node.get("urgent"):
        title += " [urgent]"
    if node.get("waiting_on"):
        title += f"  ◷ {node['waiting_on']}"
    return title


def _line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].rstrip()


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
