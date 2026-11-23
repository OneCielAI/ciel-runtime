"""Exact lookups of this conversation's Work forks by native id or name.

Answers ``rework_fork_attribution`` and ``fork_identity`` for the Work host:
which lease, rework launch or replacement a listed native fork belongs to.
Moved out of work_parallel_mcp (file budget); behaviour unchanged. The caller
always passes the ledger store, so the journals are built from it directly.
"""
from __future__ import annotations

import re

from .work_parallel import require, text
from .work_parallel_finalization import finalization_store as finalization
from .work_parallel_stranded import stranded_store as stranded


MAX_ATTRIBUTION_NAMES = 64
_REWORK_FORK_NAME = re.compile(r"work-rework-(.{1,200})-a([1-9][0-9]{0,2})\Z", re.DOTALL)


def _launch_rows(store, identity, reviews):
    """Every rework launch record of this conversation: schema-5 and stranded-v1."""
    rows = [row for row in reviews["reviews"].values()
            if row["scope"] == identity and row["decision"] == "rework"
            and row["replacement_lease_id"] is not None]
    rows += [row for row in stranded(store).read()["rows"].values()
             if row["scope"] == identity and row["replacement_lease_id"] is not None]
    return rows


def _fork_attribution(store, identity, names):
    """Host-only read: which rework launch of this conversation a fork name belongs to.

    The mod asks this for a native fork it cannot attribute from its own state
    (a hot reload, or a lease that has left its status read). It reads the
    finalization launch rows and the ledger and writes nothing.

    - ``current``: the name is the dispatch name of a launch that is unresolved
      or bound, and its replacement lease is still open.
    - ``stale``: the name belongs to a known launch of this conversation, but to
      an earlier attempt (``earlier_attempt``), to the latest attempt after it
      was resolved (``attempt_resolved``), to a terminal replacement lease
      (``lease_terminal``), or to a launch whose replacement lease is done or
      gone (``lease_ended``).
    - ``unknown``: no evidence. The caller must not stop a fork on this answer.

    An earlier attempt is recognized by its audit row, or by the replacement
    lease id and a lower attempt number in ``work-rework-<lease>-a<N>``: a new
    attempt is recorded only after the one before it was resolved. A name that
    matches more than one launch with different answers is ``unknown``.
    """
    from .work_parallel_finalization import UNRESOLVED_LAUNCH_STATES
    require(type(names) is list and len(names) <= MAX_ATTRIBUTION_NAMES
            and all(text(name, 200) for name in names), "bounded rework fork names required")
    reviews = finalization(store).read()
    leases = store.read()["leases"]
    launches = _launch_rows(store, identity, reviews)

    def judge(name, row):
        launch = row["launch"]
        lease = leases.get(row["replacement_lease_id"])
        if name == launch["dispatch_name"]:
            if launch["state"] == "spawn_denied":
                return "stale", "attempt_resolved"
            if launch["state"] not in UNRESOLVED_LAUNCH_STATES + ("bound",):
                return None
            if lease is None or lease["scope"] != identity or lease["state"] == "done":
                return "stale", "lease_ended"
            if lease["state"] in ("unreported", "orphaned"):
                return "stale", "lease_terminal"
            return "current", None
        if any(entry["dispatch_name"] == name for entry in launch["audit"]):
            return "stale", "earlier_attempt"
        parsed = _REWORK_FORK_NAME.fullmatch(name)
        if (parsed is not None and parsed.group(1) == row["replacement_lease_id"]
                and int(parsed.group(2)) < launch["attempt"]):
            return "stale", "earlier_attempt"
        return None

    forks = []
    for name in names:
        found = [(row, verdict) for row in launches for verdict in (judge(name, row),)
                 if verdict is not None]
        if not found or len({verdict for _, verdict in found}) != 1:
            forks.append({"name": name, "status": "unknown"})
            continue
        row, (status, reason) = found[0]
        answer = {"name": name, "status": status, "lease_id": row["replacement_lease_id"],
                  "attempt": row["launch"]["attempt"]}
        if reason is not None:
            answer["reason"] = reason
        forks.append(answer)
    return {"forks": forks, "finalization_version": reviews["version"]}


def _fork_identity(store, identity, ids, names):
    """Host-only read for the SendMessage revival gate: is this agent a Work fork?

    Matches every ledger lease of this conversation in any state (a lease is
    deleted only while it is an unbound reservation) and every rework launch
    row, including the dispatch names of earlier attempts in its bounded audit.
    Writes nothing. An unreadable store raises, so the caller fails closed.
    Only matched queries are returned; an unmatched one has no durable record,
    which does not prove the agent is not a Work fork (the launch audit is
    bounded and an earlier attempt's agent id is kept only once it bound).
    """
    def bounded(values):
        return values is None or (type(values) is list and len(values) <= MAX_ATTRIBUTION_NAMES
                                  and all(text(value, 200) for value in values))
    require(bounded(ids) and bounded(names) and len(ids or []) + len(names or []) >= 1,
            "bounded fork ids/names required")
    from .work_parallel import ACTIVE_STATES
    ledger_state = store.read()
    reviews = finalization(store).read()
    leases = {key: row for key, row in ledger_state["leases"].items() if row["scope"] == identity}
    launches = _launch_rows(store, identity, reviews)
    matches = []

    def lease_match(query, by, lease):
        if by == "id":
            current = lease.get("host_id") == query
            hit = current
        else:
            agent_name = lease.get("agent_name")
            hit = agent_name == query or lease.get("dispatch_name") == query
            current = agent_name == query or (agent_name is None and lease.get("dispatch_name") == query)
        if hit:
            matches.append({"query": query, "by": by, "owner": "lease", "lease_id": lease["id"],
                            "lease_state": lease["state"],
                            "active": lease["state"] in ACTIVE_STATES and lease.get("host_id") is not None,
                            "current": current})

    def launch_match(query, by, row):
        launch = row["launch"]
        if by == "id":
            current = launch["agent_id"] == query
            hit = current
        else:
            current = launch["dispatch_name"] == query
            hit = current or any(entry["dispatch_name"] == query for entry in launch["audit"])
        if hit:
            replacement = leases.get(row["replacement_lease_id"])
            matches.append({"query": query, "by": by, "owner": "launch",
                            "lease_id": row["replacement_lease_id"],
                            "lease_state": None if replacement is None else replacement["state"],
                            "active": False, "current": current})

    for by, values in (("id", ids or []), ("name", names or [])):
        for query in values:
            for lease in leases.values():
                lease_match(query, by, lease)
            for row in launches:
                launch_match(query, by, row)
    return {"matches": matches, "ledger_version": ledger_state["version"],
            "finalization_version": reviews["version"]}
