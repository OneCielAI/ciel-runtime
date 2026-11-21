"""Per-priority dispatch diagnosis: why each priority is or is not filling its slots.

User requirement 2026-11-17 (Stage 3): every priority fills up to its own
concurrent fork limit (for example P1 3, P2 2, P3 1, P4 1, P5 1, so 8 at
once), and every surface shows, per priority, the limit, the slots in use,
the runnable candidates and what keeps the others from running. Andy's board
(2026-11-17) had free slots and no fork because every runnable leaf already
held an open lease and the rest waited; nothing showed that.

The function is pure: the caller reads the ledger, the board and the
sidecars, and this module only classifies. Reasons use the dispatcher's own
rules: ``tree.runnable_leaves`` for P1-P5 (approved goal, no gate), the P0
lane's planning-safe runnable leaves, the open-lease duplicate fence, the
stranded-result hold, the parked-worker slot hold, policy, launch rate and
the lease bound. Counts only: no titles, notes or tokens.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

from . import work_plan_tree as tree

PRIORITY_ROWS = ("P0", "P1", "P2", "P3", "P4", "P5")
FENCE_STATES = ("reserved", "running", "waiting_input", "submitted", "verifying", "reviewing",
                "accepted", "cleanup_pending")
REASONS = ("waiting", "planning", "not_ready", "fenced", "stranded")
# States a person or main can act on; "can_fill" and "full" are normal.
# A full row is an exception: a goal back in planning holds unfinished work no
# slot can reach, and only main can restore its approval. Reporting it as plain
# "full" hid exactly that (Andy 2026-11-21, n1445/n2453).
ATTENTION_STATES = frozenset(("no_candidates", "disabled", "lease_bound", "rate_limited", "goal_blocked"))
PLANNING_ATTENTION_STATES = frozenset(("full",))
REASON_WORDS = {"waiting": "waiting/blocked", "planning": "goal in planning", "not_ready": "not ready",
                "fenced": "already has an open lease", "stranded": "stranded result awaiting main",
                # Approval workflow (spec 9): counted only when present, so a
                # board without workflows keeps the original five keys.
                "awaiting_proposal": "awaiting main's proposal", "awaiting_approval": "awaiting approval",
                "escalated": "escalated rework"}


def tree_workflow_gate(board: Mapping[str, Any], root_id: str) -> str:
    if not board.get("workflows"):
        return ""
    from .work_workflow import gate
    return gate(board, root_id)


def _leaves(board: Mapping[str, Any]) -> tuple[dict[str, Mapping[str, Any]], list[Mapping[str, Any]]]:
    nodes = {node["id"]: node for node in board.get("nodes", [])}
    parents = {node.get("parent_id") for node in nodes.values() if node.get("parent_id")}
    return nodes, [node for node in nodes.values() if node["id"] not in parents and node.get("status") != "done"]


def _path(nodes: Mapping[str, Mapping[str, Any]], node: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    path, seen = [node], {node["id"]}
    while path[-1].get("parent_id") is not None and path[-1]["parent_id"] in nodes \
            and path[-1]["parent_id"] not in seen:
        seen.add(path[-1]["parent_id"])
        path.append(nodes[path[-1]["parent_id"]])
    return path


def diagnose(ledger: Mapping[str, Any], board: Mapping[str, Any] | None, scope: str, *,
             p0_roots: Iterable[str] = (), p0_runnable: Iterable[str] = (),
             p0_active_ids: Iterable[str] = (), stranded_items: Iterable[str] = (),
             holds: Mapping[str, int] | None = None, now: float, max_leases: int,
             bounded_open: int, p0_goals: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """The per-priority rows, a one-line summary and the attention digest.

    ``p0_goals`` are the public per-goal rows of work_parallel_p0_outcomes: the P0
    row lists them, and a goal with no fork and a known reason draws attention.
    """
    policy = ledger["policy"]
    limits = policy["priority_limits"]
    p0_roots, p0_runnable = set(p0_roots), set(p0_runnable)
    p0_active_ids, stranded_items = set(p0_active_ids), set(stranded_items)
    p0_goals = [dict(goal) for goal in p0_goals]
    holds = dict(holds or {})
    leases = ledger["leases"]
    active = dict.fromkeys(PRIORITY_ROWS, 0)
    for ident, lease in leases.items():
        if not lease.get("slot") or lease.get("state") not in FENCE_STATES:
            continue
        active["P0" if ident in p0_active_ids else lease["priority"]] += 1
    fences: dict[str, list[str]] = {}
    for lease in leases.values():
        if (lease.get("scope") == scope and lease.get("role") in ("worker", "resume")
                and lease.get("state") in FENCE_STATES):
            fences.setdefault(lease["item_id"], []).append(lease["state"])
    rows = {name: {"priority": name, "limit": None if name == "P0" else limits.get(name, 0),
                   "active": active[name], "held_for_parked": 0 if name == "P0" else holds.get(name, 0),
                   "unfinished": 0, "candidates": 0, "blocked": dict.fromkeys(REASONS, 0),
                   "fenced_states": {}} for name in PRIORITY_ROWS}
    runnable = set()
    if board is not None:
        runnable = {row["item_id"] for row in tree.runnable_leaves(board)}
        nodes, leaves = _leaves(board)
        for leaf in leaves:
            path = _path(nodes, leaf)
            root = path[-1]
            name = "P0" if root["id"] in p0_roots else (
                root.get("priority") if root.get("priority") in tree.PRIORITIES else "P5")
            row = rows[name]
            row["unfinished"] += 1
            workflow = tree_workflow_gate(board, root["id"])
            if workflow in ("awaiting_proposal", "awaiting_approval", "escalated"):
                row["blocked"][workflow] = row["blocked"].get(workflow, 0) + 1
                continue
            if any(node.get("waiting_on") or node.get("status") == "blocked" for node in path):
                reason = "waiting"
            elif leaf["id"] in fences:
                reason = "fenced"
                for state in fences[leaf["id"]]:
                    row["fenced_states"][state] = row["fenced_states"].get(state, 0) + 1
            elif leaf["id"] in stranded_items:
                reason = "stranded"
            elif leaf["id"] in (p0_runnable if name == "P0" else runnable):
                row["candidates"] += 1
                continue
            elif name != "P0" and tree.root_phase(board, root["id"]) != tree.PHASE_EXECUTING:
                reason = "planning"
            else:
                reason = "not_ready"
            row["blocked"][reason] += 1
    stopped = not policy["enabled"] or policy["paused"]
    rate = len([stamp for stamp in ledger.get("launches", []) if now - stamp < 60]) >= policy["launches_per_minute"]
    bound = bounded_open >= max_leases
    for name, row in rows.items():
        free = None if name == "P0" else max(0, row["limit"] - row["active"] - row["held_for_parked"])
        row["free"] = free
        if row["unfinished"] == 0:
            state = "no_work"
        elif bound:
            state = "lease_bound"
        elif name != "P0" and stopped:
            state = "disabled"
        elif free is not None and free == 0:
            state = "full"
        elif row["candidates"] == 0:
            state = "no_candidates"
        elif name != "P0" and rate:
            state = "rate_limited"
        else:
            state = "can_fill"
        if name == "P0":
            row["goals"] = p0_goals
            if state != "lease_bound" and any(goal["state"] == "blocked" for goal in p0_goals):
                state = "goal_blocked"
            elif state == "no_candidates" and p0_goals:
                # Every P0 leaf is taken by its own fork or launch: nothing to report.
                state = "goals_running"
        row["state"] = state
        row["reason"] = _reason(row)
    attention = [[name, row["state"], sorted(key for key, value in row["blocked"].items() if value)
                  + sorted({f"{goal['root_id']}:{goal['code']}" for goal in row.get("goals", ())
                            if goal["state"] == "blocked"})]
                 for name, row in rows.items() if row["state"] in ATTENTION_STATES
                 or (row["state"] in PLANNING_ATTENTION_STATES and row["blocked"].get("planning", 0) > 0)]
    digest = hashlib.sha256(json.dumps(attention, separators=(",", ":")).encode()).hexdigest()[:16]
    return {"rows": [rows[name] for name in PRIORITY_ROWS], "summary": summary_line(rows.values()),
            "attention": [item[0] for item in attention], "attention_digest": digest,
            "policy": {"enabled": policy["enabled"], "paused": policy["paused"]},
            "launch_rate_reached": rate, "lease_bound": {"open": bounded_open, "max": max_leases}}


def _reason(row: Mapping[str, Any]) -> str:
    state = row["state"]
    if state == "no_work":
        return "no unfinished item"
    if state == "goals_running":
        return f"{len(row['goals'])} P0 goal(s) running or launching"
    if state == "goal_blocked":
        return "; ".join(f"{goal['root_id']}: {goal['reason']}" for goal in row["goals"]
                         if goal["state"] == "blocked")[:600]
    if state == "lease_bound":
        return "lease bound reached"
    if state == "disabled":
        return "parallel dispatch disabled or paused"
    if state == "full":
        held = row["held_for_parked"]
        reason = "all slots in use" + (f" ({held} kept for a released parked worker)" if held else "")
        planning = row["blocked"].get("planning", 0)
        if planning:
            reason += f"; goal in planning {planning} (restore execution approval to resume)"
        return reason
    if state == "rate_limited":
        return "launch rate reached; the next pump fills it"
    if state == "can_fill":
        return f"{row['candidates']} runnable; the next pump fills free slots"
    parts = [f"{REASON_WORDS[key]} {value}" for key, value in row["blocked"].items() if value]
    if row["fenced_states"]:
        parts.append("open leases: " + ", ".join(f"{key} {value}" for key, value in sorted(row["fenced_states"].items())))
    return "free slot but no runnable item (" + "; ".join(parts) + ")" if parts else "free slot but no runnable item"


def summary_line(rows: Iterable[Mapping[str, Any]]) -> str:
    bits = []
    for row in rows:
        if row["state"] == "no_work" and not row["active"]:
            continue
        used = f"{row['active']}" if row["limit"] is None else f"{row['active']}/{row['limit']}"
        planning_attention = (row["state"] in PLANNING_ATTENTION_STATES
                              and row["blocked"].get("planning", 0) > 0)
        bits.append(f"{row['priority']} {used} {row['state'].replace('_', ' ')}"
                    + (f" ({row['reason']})" if row["state"] in ATTENTION_STATES or planning_attention else ""))
    return " · ".join(bits) or "no Work to dispatch"


__all__ = ["ATTENTION_STATES", "PRIORITY_ROWS", "REASONS", "diagnose", "summary_line"]
