"""Work fork gate presence: the mod's last live probe result, for display only.

ciel-work probes the ciel-work-gate plugin live (a ``$.tool.check`` query
with a fresh nonce) before every Work fork spawn and refuses the spawn itself
when the gate does not answer. It reports the result here once per process
start and whenever (state, gate version) changes. The host, REST, web and
ciel-runtimectl views show it as ``fork_gate``.

Display only. Until B6 the host token is readable by tool children, so a
report can be forged. Nothing here authorizes or refuses a reservation or a
spawn; the mod's live probe is the only enforcement. With two processes on one
conversation the last report wins.

``work-parallel-gate-presence-v1.json`` lives beside the ledger. It is a new
file; builds through 57dbfb3 never read it. It holds no secret: fixed states,
a short version word, the single-use probe nonce and timestamps.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from .durable_file_lock import exclusive_file_lock
from .work_parallel import WorkParallelError, WorkParallelStateUnavailable

PRODUCER = "ciel-runtime/work_parallel_gate_presence"
SCHEMA = 1
FILE_NAME = "work-parallel-gate-presence-v1.json"
MAX_SESSIONS = 64
MAX_BYTES = 64 * 1024
STATES = ("loaded", "missing", "mismatch")
ROW_FIELDS = frozenset(("state", "gate_version", "probe_ref", "since", "reported_at", "reports"))
NOT_LOADED_TEXT = "Work fork gate not loaded: parallel execution disabled"
NOT_REPORTED_TEXT = "Work fork gate presence not reported by this conversation's Work host"
UNREADABLE_TEXT = "Work fork gate presence record unreadable; left unchanged"
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,31}\Z")
_NONCE = re.compile(r"[0-9a-f]{32}\Z")
_SCOPE = re.compile(r"(?:claude|codex):[A-Za-z0-9_-]{1,200}\Z")


def presence_path(ledger_path):
    return Path(ledger_path).with_name(FILE_NAME)


def _time(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 < value < 1e11


def _valid_row(row):
    return (type(row) is dict and set(row) == ROW_FIELDS and row["state"] in STATES
            and (row["gate_version"] is None or (isinstance(row["gate_version"], str)
                                                 and _VERSION.fullmatch(row["gate_version"]) is not None))
            and (row["state"] == "missing" or row["gate_version"] is not None)
            and isinstance(row["probe_ref"], str) and _NONCE.fullmatch(row["probe_ref"]) is not None
            and _time(row["since"]) and _time(row["reported_at"]) and row["since"] <= row["reported_at"]
            and type(row["reports"]) is int and 1 <= row["reports"] <= 1_000_000)


def _empty():
    return {"producer": PRODUCER, "schema": SCHEMA, "version": 0, "sessions": {}}


def _unknown(reason, message):
    return {"state": "unknown", "reason": reason, "gate_version": None,
            "observed_at": None, "reported_at": None, "message": message}


def _view(row):
    if row is None:
        return _unknown("not_reported", NOT_REPORTED_TEXT)
    loaded = row["state"] == "loaded"
    return {"state": "loaded" if loaded else "not_loaded", "reason": None if loaded else row["state"],
            "gate_version": row["gate_version"], "observed_at": row["since"],
            "reported_at": row["reported_at"], "message": None if loaded else NOT_LOADED_TEXT}


class WorkParallelGatePresence:
    def __init__(self, path):
        self.path = Path(path)

    @classmethod
    def beside(cls, ledger_path):
        return cls(presence_path(ledger_path))

    def read(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return _empty()
        if len(raw) > MAX_BYTES:
            raise WorkParallelStateUnavailable("Work fork gate presence record exceeds its limit; left unchanged")
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise WorkParallelStateUnavailable("Work fork gate presence record is unreadable; left unchanged") from error
        if (type(doc) is not dict or set(doc) != {"producer", "schema", "version", "sessions"}
                or doc["producer"] != PRODUCER or doc["schema"] != SCHEMA
                or type(doc["version"]) is not int or doc["version"] < 0
                or type(doc["sessions"]) is not dict or len(doc["sessions"]) > MAX_SESSIONS
                or not all(isinstance(key, str) and _SCOPE.fullmatch(key) is not None and _valid_row(row)
                           for key, row in doc["sessions"].items())):
            raise WorkParallelStateUnavailable(
                "Work fork gate presence record has another producer or schema; left unchanged")
        return doc

    def record(self, scope, state, *, gate_version=None, probe_ref=None):
        """Host-only report of one live probe result for this conversation."""
        if not isinstance(scope, str) or _SCOPE.fullmatch(scope) is None:
            raise WorkParallelError("gate presence scope must be the current conversation identity")
        if state not in STATES:
            raise WorkParallelError("gate presence state must be one of " + ", ".join(STATES))
        if gate_version is not None and (not isinstance(gate_version, str)
                                         or _VERSION.fullmatch(gate_version) is None):
            raise WorkParallelError("gate_version must be a short version word")
        if state != "missing" and gate_version is None:
            raise WorkParallelError("gate_version is required when the gate answered")
        if not isinstance(probe_ref, str) or _NONCE.fullmatch(probe_ref) is None:
            raise WorkParallelError("probe_ref must be the probe's 32 lowercase hex nonce")
        stamp = time.time()
        if not _time(stamp):
            raise WorkParallelError("gate presence clock")
        from .durable_json_file import save
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path):
            doc = self.read()
            sessions = doc["sessions"]
            prior = sessions.get(scope)
            same = prior is not None and (prior["state"], prior["gate_version"]) == (state, gate_version)
            row = {"state": state, "gate_version": gate_version, "probe_ref": probe_ref,
                   # "since": when this (state, version) was first reported.
                   "since": prior["since"] if same else stamp,
                   "reported_at": max(stamp, prior["reported_at"]) if same else stamp,
                   "reports": min(prior["reports"] + 1, 1_000_000) if same else 1}
            row["since"] = min(row["since"], row["reported_at"])
            sessions[scope] = row
            while len(sessions) > MAX_SESSIONS:
                oldest = min((key for key in sessions if key != scope),
                             key=lambda key: (sessions[key]["reported_at"], key))
                del sessions[oldest]
            doc["version"] += 1
            save(self.path, doc)
            return {"fork_gate": _view(row), "gate_presence_version": doc["version"]}

    def public(self, scope):
        """This conversation's fork_gate view; never the probe nonce."""
        try:
            doc = self.read()
        except (WorkParallelStateUnavailable, OSError):
            return {"fork_gate": _unknown("unreadable", UNREADABLE_TEXT), "gate_presence_version": None}
        return {"fork_gate": _view(doc["sessions"].get(scope)), "gate_presence_version": doc["version"]}


def fork_gate_view(ledger_path, scope):
    """The public fork_gate object of ``scope`` beside the given ledger."""
    return WorkParallelGatePresence.beside(ledger_path).public(scope)
