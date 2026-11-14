"""Authenticated host-only selection and dispatch boundary for wait boosts."""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .work_parallel_wait_boost import WaitBoostError


PUBLIC_FIELDS = frozenset((
    "eligible", "ordinary_allowed", "behavior", "reason", "item_id",
    "root_id", "priority", "mixed_version_best_effort",
))
EVENT_FIELDS = {
    "reserve": frozenset(("scope", "item_id", "attempt_id", "candidate", "ledger_expected_version", "wait_expected_version", "now")),
    "workspace_bind": frozenset(("attempt_id", "workspace_manifest", "agent_id", "agent_name", "now")),
    "reacquire_slot": frozenset(("scope", "item_id", "attempt_id", "wait_expected_version", "now")),
    "reacquire_result": frozenset(("scope", "item_id", "attempt_id", "delivered", "wait_expected_version", "now")),
    "cleanup": frozenset(("cleanup_key", "now")),
}


class WaitHostApi:
    def __init__(self, orchestrator: Any, *, cancel_stale=None):
        self.orchestrator = orchestrator
        self.cancel_stale = cancel_stale or (lambda row, reason: None)

    def select(self, *, p0, rows, board_items, ordinary_allowed, planning_roots=()):
        if type(ordinary_allowed) is not bool:
            raise WaitBoostError("ordinary_allowed required")
        if p0:
            return {"kind": "p0", "candidate": copy.deepcopy(p0),
                    "ordinary_allowed": False}
        planning = set(planning_roots)
        candidates = []
        for row in rows:
            item = board_items.get(row["item_id"])
            if item is None:
                self.cancel_stale(row, "identity_mismatch")
                continue
            if item.get("done"):
                self.cancel_stale(row, "terminal")
                continue
            if item.get("waiting_on"):
                self.cancel_stale(row, "reblocked")
                continue
            if row["root_id"] in planning:
                continue
            if row["state"] == "consuming":
                candidates.append((0, -row["release_at"], row["release_receipt"], row))
            elif row["state"] == "boosted":
                parked = 0 if row.get("parked_lease_id") else 1
                candidates.append((1 + parked, -row["release_at"], row["release_receipt"], row))
        if candidates:
            selected = min(candidates)[-1]
            return {"kind": "boost", "candidate": copy.deepcopy(selected),
                    "ordinary_allowed": False}
        return {"kind": "ordinary" if ordinary_allowed else "none",
                "candidate": None, "ordinary_allowed": ordinary_allowed}

    def dispatch(self, request: Mapping[str, Any]):
        if type(request) is not dict or set(request) != {"event", "args", "_host"}:
            raise WaitBoostError("invalid host request fields")
        host = request["_host"]
        if type(host) is not dict or set(host) != {"internal", "scope"}:
            raise WaitBoostError("invalid host authority")
        if host["internal"] is not True or not isinstance(host["scope"], str):
            raise WaitBoostError("host authority required")
        args = request["args"]
        if type(args) is not dict:
            raise WaitBoostError("invalid host args")
        event = request["event"]
        methods = {
            "reserve": "reserve", "workspace_bind": "workspace_and_bind",
            "reacquire_slot": "reacquire_slot", "reacquire_result": "reacquire_result",
            "cleanup": "cleanup_to_done",
        }
        method = methods.get(event)
        fields = EVENT_FIELDS.get(event)
        if method is None or fields is None:
            raise WaitBoostError("unknown host event")
        if set(args) != set(fields):
            raise WaitBoostError("invalid event args")
        bound = copy.deepcopy(args)
        if "scope" in bound and bound["scope"] != host["scope"]:
            raise WaitBoostError("host scope mismatch")
        result = getattr(self.orchestrator, method)(**bound)
        return {"ok": True, "event": event, "result": self._host_result(event, result)}

    @staticmethod
    def _host_result(event, result):
        if event == "reserve":
            # Reserve responses may contain full pins/capabilities. Return only bounded state.
            state = result[0] if isinstance(result, tuple) else result
            return {"state": "reserved", "version": state.get("version") if isinstance(state, dict) else None}
        if isinstance(result, tuple):
            return {"state": str(result[0])[:40]}
        if isinstance(result, dict):
            return {name: result.get(name) for name in ("state", "outcome", "ok") if name in result}
        return {"state": "ok"}

    @staticmethod
    def public_status(value: Mapping[str, Any]) -> dict[str, Any]:
        source = dict(value)
        return {name: source.get(name) for name in PUBLIC_FIELDS}
