"""What actually happened to each effective-P0 goal (user requirement 2026-11-18).

Celly 2026-11-17: "Side P0 queued <item> · fork starting" was printed whenever the
activation returned no error, and 9 of 10 goals had it before any Work fork existed.
Activation only registers the goal. This module holds the two facts that come later:

* why a P0 goal has no fork now (``goal_rows``, read by the per-priority diagnosis);
* the launch refusal the router saw, and which outcome notices main already got
  (``P0Outcomes``, one file beside the ledger; each outcome is claimed once per
  activation, so a restarted host does not repeat it).

Nothing here gates a launch. No timeout is used: a goal with a runnable leaf and no
known refusal stays ``pending``, which claims nothing.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import work_plan_tree as tree
from .durable_file_lock import exclusive_file_lock
from .durable_json_file import save
from .work_parallel_diagnosis import FENCE_STATES, _leaves, _path

FILE_NAME = "work-parallel-p0-outcomes.json"
MAX_ROOTS = 256
MAX_OUTCOME = 80
MAX_DETAIL = 500
REASON_WORDS = {
    "no_runnable_leaf": "no unfinished leaf to run",
    "waiting_on": "every remaining leaf waits or is blocked",
    "open_lease": "its leaves already hold open leases",
    "owner_failure": "its open lease belongs to another or an ended process",
    "stranded_result": "a stranded result awaits main's decision",
    "lease_bound": "the lease bound is reached",
    "spawn_denied": "the native fork launch was refused",
    "launch_refused": "the launch was refused",
    "workspace_git_untrusted": "git refuses the repository (dubious ownership)",
    "workspace_prepare_failed": "workspace preparation failed",
    "workspace_prepare_retained": "workspace preparation failed and its folder was kept",
    "workspace_outcome_unknown": "the workspace preparation outcome is unknown",
}


def outcome_key(scope: str, root_id: str, set_at: float) -> str:
    return "\0".join((scope, root_id, repr(float(set_at))))


class P0Outcomes:
    def __init__(self, ledger_path: Path | str):
        self.path = Path(ledger_path).with_name(FILE_NAME)

    def read(self) -> dict[str, Any]:
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"roots": {}}
        return doc if isinstance(doc, dict) and isinstance(doc.get("roots"), dict) else {"roots": {}}

    def _change(self, key: str, change) -> Any:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path):
            doc = self.read()
            row = doc["roots"].setdefault(key, {"refusal": None, "notified": {}, "at": time.time()})
            answer, changed = change(row)
            if changed:
                row["at"] = time.time()
                for old in sorted(doc["roots"], key=lambda name: doc["roots"][name].get("at") or 0)[
                        :max(0, len(doc["roots"]) - MAX_ROOTS)]:
                    del doc["roots"][old]
                save(self.path, doc)
            return answer

    def note_refusal(self, key: str, item_id: str, code: str, detail: str) -> None:
        refusal = {"item_id": str(item_id)[:200], "code": str(code)[:60], "detail": str(detail)[:MAX_DETAIL],
                   "at": time.time()}
        self._change(key, lambda row: (row.update(refusal=refusal), True))

    def clear_refusal(self, key: str) -> None:
        def change(row):
            had = row.get("refusal") is not None
            row["refusal"] = None
            return None, had
        self._change(key, change)

    def claim(self, key: str, outcome: str, *, release: bool = False) -> bool:
        """True for the one caller that may tell main this outcome."""
        outcome = str(outcome)[:MAX_OUTCOME]
        def change(row):
            notified = row.setdefault("notified", {})
            if release:
                return notified.pop(outcome, None) is not None, outcome in notified or True
            if outcome in notified:
                return False, False
            notified[outcome] = time.time()
            return True, True
        return self._change(key, change)


def refusal_code(message: str) -> str:
    if message.startswith("workspace_"):
        return message.split(":", 1)[0]
    return "workspace_outcome_unknown" if "workspace preparation outcome unknown" in message else "launch_refused"


def goal_rows(board: Mapping[str, Any] | None, scope: str, markers: Mapping[str, Mapping[str, Any]],
              leases: Mapping[str, Mapping[str, Any]], *, runnable: Iterable[str] = (),
              stranded_items: Iterable[str] = (), outcomes: Mapping[str, Any] | None = None,
              owner_classes: Mapping[str, str] | None = None, bound: bool = False) -> list[dict[str, Any]]:
    """One row per effective-P0 goal: its forks, or why it has none."""
    if board is None or not markers:
        return []
    runnable, stranded_items = set(runnable), set(stranded_items)
    owner_classes = owner_classes or {}
    saved = (outcomes or {}).get("roots", {})
    nodes, leaves = _leaves(board)
    mine = {ident: row for ident, row in leases.items() if row.get("scope") == scope
            and row.get("role") in ("worker", "resume") and row.get("state") in FENCE_STATES}
    by_item: dict[str, list[str]] = {}
    for ident, row in mine.items():
        by_item.setdefault(row["item_id"], []).append(ident)
    rows = []
    for root_id, marker in markers.items():
        root = nodes.get(root_id)
        if root is None:
            continue
        row = {"root_id": root_id, "activated_at": marker.get("set_at"), "set_by": marker.get("set_by"),
               "phase": tree.root_phase(board, root_id), "unfinished": 0, "runnable": 0, "waiting": 0,
               "fenced": 0, "stranded": 0, "fenced_states": {}, "forks": 0}
        agents, waits, foreign, pending_only = [], [], 0, True
        for node_id, node in nodes.items():
            if _path(nodes, node)[-1]["id"] != root_id:
                continue
            for ident in by_item.get(node_id, ()):
                lease = mine[ident]
                if lease["state"] == "running" and lease.get("host_id"):
                    row["forks"] += 1
                    agents.append({"agent_id": lease["host_id"], "item_id": node_id, "lease_id": ident})
        for leaf in leaves:
            path = _path(nodes, leaf)
            if path[-1]["id"] != root_id:
                continue
            row["unfinished"] += 1
            gate = next((node for node in path if node.get("waiting_on") or node.get("status") == "blocked"), None)
            if gate is not None:
                row["waiting"] += 1
                waits.append({"id": gate["id"], "waiting_on": str(gate.get("waiting_on") or "blocked")[:200]})
            elif leaf["id"] in by_item:
                row["fenced"] += 1
                for ident in by_item[leaf["id"]]:
                    lease = mine[ident]
                    row["fenced_states"][lease["state"]] = row["fenced_states"].get(lease["state"], 0) + 1
                    pending_only = pending_only and lease["state"] == "reserved" and not lease.get("host_id")
                    foreign += owner_classes.get(ident) in ("other_live", "gone")
            elif leaf["id"] in stranded_items:
                row["stranded"] += 1
            elif leaf["id"] in runnable:
                row["runnable"] += 1
        record = saved.get(outcome_key(scope, root_id, marker.get("set_at") or 0), {})
        refusal = record.get("refusal")
        if row["forks"]:
            state, code = "running", ""
        elif row["runnable"]:
            if bound:
                state, code = "blocked", "lease_bound"
            elif refusal:
                state, code = "blocked", refusal["code"]
            else:
                state, code = "pending", ""
        elif row["fenced"] and pending_only:
            state, code = "pending", ""
        elif row["fenced"]:
            state, code = "blocked", "owner_failure" if foreign else "open_lease"
        elif row["waiting"]:
            state, code = "blocked", "waiting_on"
        elif row["stranded"]:
            state, code = "blocked", "stranded_result"
        else:
            state, code = "blocked", "no_runnable_leaf"
        row.update(state=state, code=code, reason=_reason(row, state, code),
                   title=str(root.get("title") or "")[:200], fork_agents=agents[:8], waits=waits[:3],
                   refusal=refusal, notified=dict(record.get("notified") or {}))
        rows.append(row)
    return rows


def _reason(row: Mapping[str, Any], state: str, code: str) -> str:
    if state == "running":
        return f"{row['forks']} fork(s) running"
    if state == "pending":
        return "launch pending; no refusal seen"
    parts = [REASON_WORDS.get(code, code)]
    if row["fenced_states"]:
        parts.append("open leases: " + ", ".join(f"{key} {value}" for key, value in sorted(row["fenced_states"].items())))
    if row["waiting"] and code != "waiting_on":
        parts.append(f"{row['waiting']} leaf/leaves waiting")
    if row["phase"] != tree.PHASE_EXECUTING:
        parts.append("goal still in planning (P0 needs no approval)")
    return "; ".join(parts)


PUBLIC_FIELDS = ("root_id", "activated_at", "set_by", "phase", "unfinished", "runnable", "waiting", "fenced",
                 "stranded", "fenced_states", "forks", "state", "code", "reason")


def public_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Counts and fixed words only: no titles, wait text or refusal detail."""
    return {key: row[key] for key in PUBLIC_FIELDS}


__all__ = ["FILE_NAME", "P0Outcomes", "PUBLIC_FIELDS", "goal_rows", "outcome_key", "public_row", "refusal_code"]
