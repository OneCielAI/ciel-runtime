"""Content-free native fork evidence, kept apart from the lease ledger.

A lease says a slot was reserved for a Work item. Only an observation of the
native agent list says an independent Claude fork actually exists. Observations
are written to their own file with their own version, so host heartbeats never
invalidate the ``expected_version`` used by admission or operator controls.

Nothing a fork said or did is stored: no prompt, description, transcript, tool
output, result body or worker token. Each record holds ids, a fixed status word
and server timestamps only.
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from pathlib import Path

from .channel_message_repository import exclusive_file_lock
from .work_parallel import WorkParallelConflict, WorkParallelError, integer, number, require, text, unique_object

MAX_BYTES = 1048576
MAX_RECORDS = 2000
MAX_SCOPES = 200
# Claude Code AgentStatus (2.1.321 plugin types). Anything else is refused.
NATIVE_STATUSES = ("pending", "running", "waiting", "idle", "completed", "failed", "killed")
ACTIVE_NATIVE = frozenset(("pending", "running"))
TERMINAL_NATIVE = frozenset(("completed", "failed", "killed"))
# An unchanged observation is rewritten at most this often (freshness only).
HEARTBEAT_SECONDS = 15
# A confirmed fork must have been seen within this window.
FRESH_SECONDS = 30
MAX_ROWS_PER_OBSERVATION = 64
RECORD_FIELDS = ("scope", "lease_id", "agent_id", "agent_name", "status", "first_seen_at",
                 "last_seen_at", "status_changed_at", "missing_since", "terminal_at")


def empty_state():
    return {"schema": 1, "version": 0, "records": {}, "scans": {}}


def _optional_time(value):
    return value is None or number(value, 1e15)


def validate(d):
    require(type(d) is dict and set(d) == {"schema", "version", "records", "scans"}, "native evidence fields")
    require(d["schema"] == 1 and integer(d["version"]), "native evidence schema")
    require(type(d["records"]) is dict and len(d["records"]) <= MAX_RECORDS, "native record bound")
    require(type(d["scans"]) is dict and len(d["scans"]) <= MAX_SCOPES, "native scan bound")
    for scope, at in d["scans"].items():
        require(text(scope, 200) and number(at, 1e15), "native scan")
    for lease_id, r in d["records"].items():
        require(type(r) is dict and set(r) == set(RECORD_FIELDS), "native record fields")
        require(r["lease_id"] == lease_id and text(lease_id, 200) and text(r["scope"], 200), "native record identity")
        require(text(r["agent_id"], 200) and (r["agent_name"] is None or text(r["agent_name"], 200)), "native agent identity")
        require(r["status"] in NATIVE_STATUSES, "native status")
        require(all(number(r[k], 1e15) for k in ("first_seen_at", "last_seen_at", "status_changed_at")), "native times")
        require(r["first_seen_at"] <= r["status_changed_at"] <= r["last_seen_at"], "native time order")
        require(_optional_time(r["missing_since"]) and _optional_time(r["terminal_at"]), "native optional times")


class NativeForkEvidence:
    """Corruption stays in place and blocks writes; a missing file reads empty
    and is never created by a read."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self._lock = threading.RLock()

    def _read(self):
        try:
            with self.path.open("rb") as f:
                raw = f.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return empty_state()
        require(len(raw) <= MAX_BYTES, "native evidence byte limit")
        try:
            d = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
            validate(d)
            return d
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
            raise WorkParallelError("corrupt native fork evidence; writes blocked") from error

    def read(self):
        if not self.path.exists():
            return empty_state()
        with self._lock, exclusive_file_lock(self.path):
            return self._read()

    def _change(self, fn, version):
        require(integer(version), "expected_evidence_version required")
        with self._lock, exclusive_file_lock(self.path):
            before = self._read()
            if before["version"] != version:
                raise WorkParallelConflict("native evidence changed; reread")
            d = copy.deepcopy(before)
            result = fn(d)
            if d != before:
                d["version"] += 1
                validate(d)
                raw = json.dumps(d, ensure_ascii=False, allow_nan=False).encode("utf-8")
                require(len(raw) <= MAX_BYTES, "native evidence byte limit")
                tmp = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
                try:
                    with tmp.open("xb") as f:
                        f.write(raw)
                        f.flush()
                        os.fsync(f.fileno())
                    for attempt in range(20):
                        try:
                            os.replace(tmp, self.path)
                            break
                        except PermissionError:
                            if attempt == 19:
                                raise
                            time.sleep(.002)
                finally:
                    tmp.unlink(missing_ok=True)
            return copy.deepcopy(d), copy.deepcopy(result)

    def observe(self, scope, leases, rows, *, complete, expected_version, now=None):
        """Record one native agent-list observation for one conversation.

        ``leases`` is that conversation's lease map (scope-filtered by the
        caller). Each row is ``{lease_id, agent_id, agent_name, status}`` and
        must name a lease of this scope whose bound host is that agent; a row
        for any other agent is refused, so the main loop and side actors can
        never be recorded as Work forks. A complete list that no longer holds a
        previously seen fork marks it missing; absence is not death.
        """
        require(text(scope, 200), "scope")
        require(type(complete) is bool, "complete flag")
        require(type(rows) is list and len(rows) <= MAX_ROWS_PER_OBSERVATION, "native rows")
        stamp = time.time() if now is None else now
        require(number(stamp, 1e15), "clock")
        clean = {}
        for row in rows:
            require(type(row) is dict and set(row) <= {"lease_id", "agent_id", "agent_name", "status"}
                    and {"lease_id", "agent_id", "status"} <= set(row), "native row fields")
            lease = leases.get(row["lease_id"])
            require(lease is not None and lease.get("scope") == scope, "native row names no lease of this conversation")
            require(text(row["agent_id"], 200) and lease.get("host_id") == row["agent_id"], "native row is not this lease's bound fork")
            name = row.get("agent_name")
            require(name is None or text(name, 200), "native agent name")
            require(row["status"] in NATIVE_STATUSES, "native status")
            require(row["lease_id"] not in clean, "duplicate native row")
            clean[row["lease_id"]] = {"agent_id": row["agent_id"], "agent_name": name, "status": row["status"]}

        def apply(d):
            changed = []
            for lease_id, seen in clean.items():
                old = d["records"].get(lease_id)
                if old is None or old["agent_id"] != seen["agent_id"]:
                    # A resumed lease is a new native generation for that lease.
                    d["records"][lease_id] = {"scope": scope, "lease_id": lease_id, **seen,
                        "first_seen_at": stamp, "last_seen_at": stamp, "status_changed_at": stamp,
                        "missing_since": None, "terminal_at": stamp if seen["status"] in TERMINAL_NATIVE else None}
                    changed.append(lease_id)
                    continue
                record = d["records"][lease_id]
                status_changed = record["status"] != seen["status"]
                returned = record["missing_since"] is not None
                if status_changed or returned or record["agent_name"] != seen["agent_name"]:
                    if status_changed:
                        record["status_changed_at"] = stamp
                        record["terminal_at"] = stamp if seen["status"] in TERMINAL_NATIVE else None
                    record.update(status=seen["status"], agent_name=seen["agent_name"], missing_since=None,
                                  last_seen_at=stamp)
                    changed.append(lease_id)
                elif stamp - record["last_seen_at"] >= HEARTBEAT_SECONDS:
                    record["last_seen_at"] = stamp
            if complete:
                for lease_id, record in d["records"].items():
                    if (record["scope"] == scope and lease_id not in clean
                            and record["missing_since"] is None and record["status"] not in TERMINAL_NATIVE):
                        record["missing_since"] = stamp
                        changed.append(lease_id)
                previous = d["scans"].get(scope)
                if changed or previous is None or stamp - previous >= HEARTBEAT_SECONDS:
                    d["scans"][scope] = stamp
                if len(d["scans"]) > MAX_SCOPES:
                    for oldest in sorted(d["scans"], key=d["scans"].get)[:len(d["scans"]) - MAX_SCOPES]:
                        d["scans"].pop(oldest)
            return changed
        return self._change(apply, expected_version)

    def prune(self, live_lease_ids, *, expected_version):
        """Drop records whose lease no longer exists in the ledger."""
        keep = set(live_lease_ids)

        def apply(d):
            removed = []
            for lease_id in [k for k in d["records"] if k not in keep]:
                d["records"].pop(lease_id)
                removed.append(lease_id)
            return removed
        return self._change(apply, expected_version)


def classify(lease, record, scan_at, *, now=None):
    """Evidence class of one lease of the current conversation.

    Only a fresh native ``pending``/``running`` row confirms a fork. A lease
    state, a held slot or a bound host id alone never does.
    """
    stamp = time.time() if now is None else now
    state = lease.get("state")
    if state in ("done", "unreported", "orphaned"):
        return "ended"
    if state == "waiting_input":
        return "waiting"
    if state == "reserved" and not lease.get("host_id"):
        return "reservation"
    if record is not None and record.get("agent_id") == lease.get("host_id"):
        if record["status"] in TERMINAL_NATIVE:
            return "ended" if not lease.get("slot") else "uncertain"
        if record["status"] == "waiting":
            return "waiting"
        if record["status"] == "idle":
            return "validating" if state in ("submitted", "verifying", "reviewing", "accepted", "cleanup_pending") else "uncertain"
        fresh = stamp - record["last_seen_at"] <= FRESH_SECONDS and record["missing_since"] is None
        if record["status"] in ACTIVE_NATIVE and fresh:
            return "confirmed"
        return "uncertain"
    if state in ("submitted", "verifying", "reviewing", "accepted", "cleanup_pending"):
        return "validating"
    return "uncertain"
