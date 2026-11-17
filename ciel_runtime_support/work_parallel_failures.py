"""Durable Work parallel failure log: a host-only write, a bounded public read.

The Work host records refusals that are otherwise visible only as a transient
tool denial or a Stop notice: a refused SendMessage revival of a Work fork, a
review decision refused while Stop holds, and a fork stopped after attribution.
The web dashboard, REST and ciel-runtimectl read it after the fact.

Only fixed codes, short opaque ids and timestamps are stored. No path, text,
agent id, launch name, token or conversation content is ever recorded.
``work-parallel-failures-v1.json`` lives beside the ledger. It is a new file;
builds through 57dbfb3 never read it.

Workspace preparation failures (2026-11-17) are kept in their own file,
``work-parallel-workspace-failures-v1.json``, with the same schema. The first
file is shared by every build on the device and each build refuses a code it
does not know, so a new code there would stop an older running build from
reading or writing it. ``public_failures`` merges both for the surfaces.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

from .durable_file_lock import exclusive_file_lock
from .work_parallel import WorkParallelError, WorkParallelStateUnavailable

PRODUCER = "ciel-runtime/work_parallel_failures"
WORKSPACE_PRODUCER = "ciel-runtime/work_parallel_workspace_failures"
SCHEMA = 1
FILE_NAME = "work-parallel-failures-v1.json"
WORKSPACE_FILE_NAME = "work-parallel-workspace-failures-v1.json"
MAX_EVENTS = 200
MAX_BYTES = 256 * 1024
PUBLIC_LIMIT = 50
CODES = ("revival_refused", "decision_refused_stop", "fork_attribution_stop", "owner_unknown", "owner_live")
# The router prepares a fork's worktree before the fork starts. A failure there
# used to leave its lease or coordination run reserved and reached no surface
# (Celly, 2026-11-17).
WORKSPACE_CODES = ("workspace_git_untrusted", "workspace_prepare_failed", "workspace_prepare_retained")
# R2 B2: the router refused an automatic absence or restart observation.
# One unknown-owner text on every surface (lead ruling).
OWNER_UNKNOWN_TEXT = "Owner of this launch attempt is unknown; resolve it from the Work pane."
OWNER_LIVE_TEXT = "Another live Work host owns this launch attempt; waiting for that host."
# One server text per code, shown verbatim by the web console, REST and ctl.
MESSAGES = {
    "owner_unknown": OWNER_UNKNOWN_TEXT,
    "owner_live": OWNER_LIVE_TEXT,
    "workspace_git_untrusted": ("Fork not started: Git refused the repository as owned by another account. "
                                "Work trusts only the folder this router was launched in. The reservation "
                                "was released and the item stays queued; the router log has Git's message."),
    "workspace_prepare_failed": ("Fork not started: its workspace could not be prepared. The reservation was "
                                 "released and the item stays queued; the router log has the cause."),
    "workspace_prepare_retained": ("Fork not started: workspace preparation stopped after it began writing. "
                                   "The reservation was released and the item stays queued; the partial "
                                   "worktree is kept for inspection. The router log has the cause."),
}
EVENT_FIELDS = frozenset(("id", "code", "scope", "lease_id", "first_at", "at", "count"))
_ID = re.compile(r"[0-9a-f]{16}\Z")
_KEY = re.compile(r"[!-~]{1,64}\Z")
_SCOPE = re.compile(r"(?:claude|codex):[A-Za-z0-9_-]{1,200}\Z")
_LEASE = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")


def failures_path(ledger_path):
    return Path(ledger_path).with_name(FILE_NAME)


def _time(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 < value < 1e11


def _valid_event(row, codes=CODES):
    return (type(row) is dict and set(row) == EVENT_FIELDS and isinstance(row["id"], str)
            and _ID.fullmatch(row["id"]) is not None and row["code"] in codes
            and isinstance(row["scope"], str) and _SCOPE.fullmatch(row["scope"]) is not None
            and (row["lease_id"] is None or (isinstance(row["lease_id"], str)
                                             and _LEASE.fullmatch(row["lease_id"]) is not None))
            and _time(row["first_at"]) and _time(row["at"]) and row["first_at"] <= row["at"]
            and type(row["count"]) is int and 1 <= row["count"] <= 1_000_000)


class WorkParallelFailureLog:
    def __init__(self, path, *, producer=PRODUCER, codes=CODES):
        self.path = Path(path)
        self.producer = producer
        self.codes = tuple(codes)

    @classmethod
    def beside(cls, ledger_path):
        return cls(failures_path(ledger_path))

    @classmethod
    def workspace_beside(cls, ledger_path):
        return cls(Path(ledger_path).with_name(WORKSPACE_FILE_NAME), producer=WORKSPACE_PRODUCER,
                   codes=WORKSPACE_CODES)

    def _empty(self):
        return {"producer": self.producer, "schema": SCHEMA, "version": 0, "events": []}

    def read(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return self._empty()
        if len(raw) > MAX_BYTES:
            raise WorkParallelStateUnavailable("Work failure log exceeds its limit; left unchanged")
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise WorkParallelStateUnavailable("Work failure log is unreadable; left unchanged") from error
        if (type(doc) is not dict or set(doc) != {"producer", "schema", "version", "events"}
                or doc["producer"] != self.producer or doc["schema"] != SCHEMA
                or type(doc["version"]) is not int or doc["version"] < 0
                or type(doc["events"]) is not list or len(doc["events"]) > MAX_EVENTS
                or not all(_valid_event(row, self.codes) for row in doc["events"])
                or len({row["id"] for row in doc["events"]}) != len(doc["events"])):
            raise WorkParallelStateUnavailable("Work failure log has another producer or schema; left unchanged")
        return doc

    def record(self, scope, code, *, lease_id=None, dedup_key=None, now=None):
        """Host-only append. A repeated dedup_key counts the same event again."""
        if not isinstance(scope, str) or _SCOPE.fullmatch(scope) is None:
            raise WorkParallelError("failure scope must be the current conversation identity")
        if code not in self.codes:
            raise WorkParallelError("failure code must be one of " + ", ".join(self.codes))
        if lease_id is not None and (not isinstance(lease_id, str) or _LEASE.fullmatch(lease_id) is None):
            raise WorkParallelError("failure lease_id must be a short opaque id")
        if dedup_key is not None and (not isinstance(dedup_key, str) or _KEY.fullmatch(dedup_key) is None):
            raise WorkParallelError("failure dedup_key must be 1-64 visible ASCII characters")
        stamp = time.time() if now is None else now
        if not _time(stamp):
            raise WorkParallelError("failure clock")
        if dedup_key is None:
            ident = os.urandom(8).hex()
        else:
            ident = hashlib.sha256("\0".join((scope, code, dedup_key)).encode("utf-8")).hexdigest()[:16]
        from .durable_json_file import save
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path):
            doc = self.read()
            events = doc["events"]
            row = next((event for event in events if event["id"] == ident), None)
            if row is None:
                row = {"id": ident, "code": code, "scope": scope, "lease_id": lease_id,
                       "first_at": stamp, "at": stamp, "count": 1}
                events.append(row)
            elif row["scope"] != scope or row["code"] != code or row["lease_id"] != lease_id:
                raise WorkParallelError("failure dedup_key names a different event")
            else:
                row["count"] = min(row["count"] + 1, 1_000_000)
                row["at"] = max(row["at"], stamp)
            events.sort(key=lambda event: (event["at"], event["id"]))
            del events[:max(0, len(events) - MAX_EVENTS)]
            doc["version"] += 1
            save(self.path, doc)
            return {"id": ident, "code": code, "count": row["count"], "failures_version": doc["version"]}

    def public(self, scope, limit=PUBLIC_LIMIT):
        """This conversation's newest events. The scope itself is never returned."""
        doc = self.read()
        rows = [{key: event[key] for key in ("id", "code", "lease_id", "first_at", "at", "count")}
                for event in doc["events"] if event["scope"] == scope]
        rows.sort(key=lambda event: (event["at"], event["id"]), reverse=True)
        return {"failures_version": doc["version"], "failures": rows[:limit],
                "failures_truncated": len(rows) > limit}


def public_failures(ledger_path, scope, limit=PUBLIC_LIMIT):
    """Both logs of this conversation, newest first, each row with its server text.

    ``failures_version`` stays the first log's version (its existing meaning);
    an unreadable workspace log hides only its own rows.
    """
    view = WorkParallelFailureLog.beside(ledger_path).public(scope, limit)
    rows = list(view["failures"])
    truncated = view["failures_truncated"]
    try:
        extra = WorkParallelFailureLog.workspace_beside(ledger_path).public(scope, limit)
        rows += extra["failures"]
        truncated = truncated or extra["failures_truncated"]
    except (OSError, ValueError):
        pass
    rows.sort(key=lambda event: (event["at"], event["id"]), reverse=True)
    for row in rows:
        if row["code"] in MESSAGES:
            row["message"] = MESSAGES[row["code"]]
    return {"failures_version": view["failures_version"], "failures": rows[:limit],
            "failures_truncated": truncated or len(rows) > limit}
