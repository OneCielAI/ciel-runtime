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
import time
from typing import Any

SCHEMA = 1
PHASE_PLANNING = "planning"
PHASE_EXECUTING = "executing"
PHASES = (PHASE_PLANNING, PHASE_EXECUTING)
STATUSES = ("pending", "in_progress", "done", "blocked")
MAX_DEPTH = 8
MAX_NODES = 600
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
# A plan is ready to execute once the active goal reaches at least
# goal -> phase -> task (a leaf at depth 2).
READY_MIN_LEAF_DEPTH = 2
STATUS_MARKS = {"pending": "[ ]", "in_progress": "[~]", "done": "[✓]", "blocked": "[!]"}


class WorkPlanError(ValueError):
    """A plan operation was rejected; the message is shown to the model."""


def empty_document() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "version": 0,
        "phase": PHASE_PLANNING,
        "active_root": None,
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
    nodes = raw.get("nodes")
    if not isinstance(nodes, list) or len(nodes) > MAX_NODES:
        raise WorkPlanError("work plan nodes are not a list or exceed the node limit")
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
        }
        if node["parent_id"] is None:
            phase = item.get("phase", raw["phase"] if item.get("id") == raw.get("active_root") else PHASE_PLANNING)
            if phase not in PHASES:
                raise WorkPlanError("work goal has an invalid phase")
            node["phase"] = phase
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


def add_node(doc: dict[str, Any], parent_id: str | None, title: Any, note: Any = "", *, now: float | None = None,
             ref: Any = "", waiting_on: Any = "", within_scope: bool = False) -> str:
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
    clean = _clip(title, MAX_TITLE_CHARS)
    if not clean:
        raise WorkPlanError("node title must not be empty")
    if len(doc["nodes"]) >= MAX_NODES:
        raise WorkPlanError(f"a work plan holds at most {MAX_NODES} nodes")
    node_id = f"n{doc['next_id']}"
    doc["next_id"] += 1
    doc["nodes"].append({
        "id": node_id,
        "parent_id": parent_id,
        "title": clean,
        "status": "pending",
        "note": _clip(note, MAX_TEXT_CHARS),
        "evidence": "",
        "waiting_on": _line(waiting_on, MAX_WAIT_CHARS),
        "ref": _line(ref, MAX_REF_CHARS),
        "urgent": False,
        "updated_at": stamp,
    })
    if parent_id is None:
        _index(doc)[node_id]["phase"] = PHASE_PLANNING
        if not queue_behind:
            _activate(doc, node_id)
    else:
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
                       ref=spec.get("ref", ""), waiting_on=spec.get("waiting_on", ""), within_scope=within_scope)
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
    root["status"] = "pending"
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
                waiting_on: Any = None, ref: Any = None) -> dict[str, Any]:
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
    if candidate == node:
        return node
    scope_changed = (candidate["title"] != node["title"] or candidate["note"] != node["note"]
                     or (node["status"] == "done" and candidate["status"] != "done"))
    node.update(candidate)
    if scope_changed:
        set_phase(doc, PHASE_PLANNING, _root_of(by_id, node_id))
    node["updated_at"] = stamp
    if node["parent_id"] is not None:
        if status == "done":
            _complete_ancestors(doc, node["parent_id"], stamp)
        elif status in ("in_progress", "pending", "blocked"):
            _reopen_ancestors(doc, node["parent_id"], stamp)
    root = _root_of(by_id, node_id)
    if by_id[root]["status"] == "done":
        set_phase(doc, PHASE_PLANNING, root)
    if root == doc.get("active_root") and by_id[root]["status"] == "done":
        set_phase(doc, PHASE_PLANNING)
        _advance_queue(doc)
    return node


def remove_node(doc: dict[str, Any], node_id: str) -> int:
    by_id = _index(doc)
    node_id = str(node_id)
    if node_id not in by_id:
        raise WorkPlanError(f"unknown node {node_id}")
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
                raise WorkPlanError("changing the active goal requires an explicit user request (user_requested=true)")
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
    """Select runnable leaves, respecting ancestor waits, blocks and urgent holds."""

    by_id = _index(doc)
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
    chosen_index = next((i for i, rows in enumerate(available) if rows), None)
    chosen = available[chosen_index] if chosen_index is not None else []
    nxt = chosen[0] if chosen else None
    target_root = roots[chosen_index]["id"] if chosen_index is not None else None
    return {
        "actionable": len(chosen),
        "next": dict(_node_summary(nxt), path=path_titles(doc, nxt["id"]), root_id=target_root,
                     phase=root_phase(doc, target_root)) if nxt else None,
        "waiting": waiting[:MAX_WAITING_IN_CONTINUATION],
        "blocked": blocked,
        "held": len(queued_roots(doc)) if held else 0,
    }


def _node_summary(node: dict[str, Any]) -> dict[str, Any]:
    result = {key: node.get(key, "") for key in ("id", "title", "status", "ref", "waiting_on", "urgent")}
    if node.get("parent_id") is None:
        result["phase"] = node.get("phase", PHASE_PLANNING)
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
    return sum(1 for leaf in leaves if by_id[leaf]["status"] == "done"), len(leaves)


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
    nxt = next_leaf(doc, root)
    ready, reason = readiness(doc)
    return {
        "enabled": bool(enabled),
        "phase": doc.get("phase", PHASE_PLANNING),
        "approval": "ask" if approval_ask else "auto",
        "version": doc.get("version", 0),
        "active_root": _node_summary(by_id[root]) if root else None,
        "continuation": continuation(doc),
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
        # Complete display index, bounded by MAX_NODES; summaries only (no notes,
        # evidence or queued descendants). Keeps roots beyond the short queue
        # preview and completed history reachable without loading other contexts.
        "root_index": [dict(_node_summary(node),
                            held=bool(root and node["id"] != root and by_id[root].get("urgent")
                                      and by_id[root]["status"] != "done" and node["status"] != "done"))
                       for node in doc["nodes"] if node["parent_id"] is None],
        "completed_active": [_node_summary(by_id[node_id]) for node_id, _ in _walk(doc, root)
                             if node_id != root and by_id[node_id]["status"] == "done"] if root else [],
        "ready": ready,
        "ready_reason": reason,
        "goals": sum(1 for node in doc["nodes"] if node["parent_id"] is None),
        # Unfinished goals waiting behind the active one, in the order they become active.
        "queue": [
            dict(_node_summary(node), done=progress(doc, node["id"])[0], total=progress(doc, node["id"])[1],
                 held=bool(root and by_id[root].get("urgent") and by_id[root]["status"] != "done"))
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
        if current in seen or depth > MAX_NODES:
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
    """Unfinished goals other than the active one, oldest first: the order they become active."""

    active = doc.get("active_root")
    return [node for node in doc["nodes"] if node["parent_id"] is None and node["status"] != "done" and node["id"] != active]


def _advance_queue(doc: dict[str, Any]) -> None:
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
