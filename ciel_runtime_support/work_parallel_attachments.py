"""Which claude.exe Work host owns which launch nonce (R2 option B2).

Every Work host (the ciel-work mod in one claude.exe) attaches once per process
start: it reports the launch nonce it records on its rework launch attempts and
the pid of the ciel-runtime launcher that started it
(``CIEL_RUNTIME_WORK_LAUNCHER_PID``). The router reads that launcher's creation
identity itself (``process_identity``) and keeps
``{conversation, launcher_pid, creation_identity, launch_nonce}`` here.

Absence of a fork from one process's agent list never proves anything about a
fork another process owns. With this record the router can tell, for a launch
attempt or a lease, whether its owner is this process, another live process,
gone (its launcher exited or its pid was reused), or unknown.

``work-parallel-attachments-v1.json`` lives beside the ledger. It is a new file;
builds through 57dbfb3 never read it. It never changes the Work board or the
ledger and is never shown to the model. A pid and a nonce are shared values,
not secrets.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from .durable_file_lock import exclusive_file_lock
from .work_parallel import WorkParallelError, WorkParallelStateUnavailable

PRODUCER = "ciel-runtime/work_parallel_attachments"
SCHEMA = 1
FILE_NAME = "work-parallel-attachments-v1.json"
MAX_ATTACHMENTS = 256
ATTACHMENTS_FULL_TEXT = (f"Work host attachment record is full: all {MAX_ATTACHMENTS} recorded Work hosts "
                         "are live; this host is not attached")
MAX_BYTES = 256 * 1024
ROW_FIELDS = frozenset(("conversation", "launcher_pid", "creation_identity", "attached_at", "seen_at"))
# The mod's launch nonce is 32 lowercase hex; the journals accept any visible
# text of at most 200 characters, so the record key does too.
_NONCE = re.compile(r"[!-~]{1,200}\Z")
_SCOPE = re.compile(r"(?:claude|codex):[A-Za-z0-9_-]{1,200}\Z")
_IDENTITY = re.compile(r"[!-~]{1,128}\Z")
# Owner classes of an attempt or a lease, relative to the calling process.
OWN, EARLIER_CHILD, OTHER_LIVE, GONE, UNKNOWN = "own", "earlier_child", "other_live", "gone", "unknown"


def attachments_path(ledger_path):
    return Path(ledger_path).with_name(FILE_NAME)


def _time(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 < value < 1e11


def _pid(value):
    return type(value) is int and 0 < value < 2 ** 32


def _valid_row(row):
    return (type(row) is dict and set(row) == ROW_FIELDS
            and isinstance(row["conversation"], str) and _SCOPE.fullmatch(row["conversation"]) is not None
            and _pid(row["launcher_pid"])
            and isinstance(row["creation_identity"], str) and _IDENTITY.fullmatch(row["creation_identity"]) is not None
            and _time(row["attached_at"]) and _time(row["seen_at"]) and row["attached_at"] <= row["seen_at"])


def _empty():
    return {"producer": PRODUCER, "schema": SCHEMA, "version": 0, "attachments": {}}


def _identity_now(pid):
    from .workspace_router_selection import _process_identity
    return _process_identity(pid)


def liveness(row, identity_now=None):
    """"live", "gone" or "unknown" for one recorded launcher, observed now."""
    observed = (identity_now or _identity_now)(row["launcher_pid"])
    if observed is None:
        return "unknown"
    if observed == "" or observed != row["creation_identity"]:
        # Exited, or the pid now names another process.
        return "gone"
    return "live"


def _classify(caller, pid, identity, identity_now):
    """Owner launcher (pid, identity) relative to the attached caller record."""
    if caller is not None and (caller["launcher_pid"], caller["creation_identity"]) == (pid, identity):
        # Same launcher: it runs one claude.exe at a time (runtime_launch
        # run_claude_session loop), so the owner is this process or an exited one.
        return EARLIER_CHILD
    state = liveness({"launcher_pid": pid, "creation_identity": identity}, identity_now)
    if state == "gone":
        return GONE
    if state == "live" and caller is not None:
        return OTHER_LIVE
    # A live owner with no attached caller to compare against, or an
    # unreadable identity: not proven either way.
    return UNKNOWN


class WorkParallelAttachments:
    def __init__(self, path):
        self.path = Path(path)

    @classmethod
    def beside(cls, ledger_path):
        return cls(attachments_path(ledger_path))

    def read(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return _empty()
        if len(raw) > MAX_BYTES:
            raise WorkParallelStateUnavailable("Work host attachment record exceeds its limit; left unchanged")
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise WorkParallelStateUnavailable("Work host attachment record is unreadable; left unchanged") from error
        if (type(doc) is not dict or set(doc) != {"producer", "schema", "version", "attachments"}
                or doc["producer"] != PRODUCER or doc["schema"] != SCHEMA
                or type(doc["version"]) is not int or doc["version"] < 0
                or type(doc["attachments"]) is not dict or len(doc["attachments"]) > MAX_ATTACHMENTS
                or not all(isinstance(key, str) and _NONCE.fullmatch(key) is not None and _valid_row(row)
                           for key, row in doc["attachments"].items())):
            raise WorkParallelStateUnavailable(
                "Work host attachment record has another producer or schema; left unchanged")
        return doc

    def attach(self, conversation, launcher_pid, launch_nonce, *, identity_now=None):
        """Record (or confirm) one Work host. The router reads the creation identity itself."""
        if not isinstance(conversation, str) or _SCOPE.fullmatch(conversation) is None:
            raise WorkParallelError("attachment conversation must be the current conversation identity")
        if not _pid(launcher_pid):
            raise WorkParallelError("launcher_pid must be the launcher's positive process id")
        if not isinstance(launch_nonce, str) or _NONCE.fullmatch(launch_nonce) is None:
            raise WorkParallelError("launch_nonce must be the host's process nonce (visible text, at most 200)")
        identity = (identity_now or _identity_now)(launcher_pid)
        if not isinstance(identity, str) or identity == "":
            raise WorkParallelError("launcher process is not running or its identity is unreadable; not attached")
        if _IDENTITY.fullmatch(identity) is None:
            raise WorkParallelError("launcher process identity is not recordable; not attached")
        stamp = time.time()
        from .durable_json_file import save
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path):
            doc = self.read()
            rows = doc["attachments"]
            prior = rows.get(launch_nonce)
            if prior is not None:
                # A nonce names one claude.exe process for its whole life.
                if (prior["conversation"], prior["launcher_pid"], prior["creation_identity"]) != (
                        conversation, launcher_pid, identity):
                    raise WorkParallelError("this launch nonce is already attached to another host process")
                if stamp - prior["seen_at"] < 1:
                    return {"attachment_version": doc["version"], "attachment": self._public(launch_nonce, prior)}
                prior["seen_at"] = max(stamp, prior["seen_at"])
                row = prior
            else:
                row = {"conversation": conversation, "launcher_pid": launcher_pid, "creation_identity": identity,
                       "attached_at": stamp, "seen_at": stamp}
                rows[launch_nonce] = row
            if len(rows) > MAX_ATTACHMENTS:
                # Only a gone or unreadable launcher's row may go, oldest first.
                # A live owner's row is never evicted: without it that owner
                # would read "unknown", and an unknown owner's attempt may be
                # restarted (lead ruling D) while it still runs (review F1).
                evictable = sorted((key for key in rows
                                    if key != launch_nonce and liveness(rows[key], identity_now) != "live"),
                                   key=lambda key: (rows[key]["seen_at"], key))
                excess = len(rows) - MAX_ATTACHMENTS
                if len(evictable) < excess:
                    # Nothing is written: every recorded launcher is live.
                    raise WorkParallelError(ATTACHMENTS_FULL_TEXT)
                for key in evictable[:excess]:
                    del rows[key]
            doc["version"] += 1
            save(self.path, doc)
            return {"attachment_version": doc["version"], "attachment": self._public(launch_nonce, row)}

    @staticmethod
    def _public(nonce, row):
        """Host view of one record: never the creation identity."""
        return {"state": "live", "launcher_pid": row["launcher_pid"], "launch_nonce": nonce,
                "attached_at": row["attached_at"]}

    def owner_class(self, owner_nonce, caller_nonce, *, identity_now=None, doc=None):
        """Classify the process that recorded ``owner_nonce`` relative to the caller.

        own: the caller's own nonce. earlier_child: another nonce under the
        caller's own launcher (same pid and creation identity), which runs one
        claude.exe at a time, so that earlier process has exited. other_live:
        another launcher that is alive with the recorded identity now. gone: its
        launcher exited or the pid was reused. unknown: no record, an unreadable
        identity, or no attached caller to compare a same-launcher owner with.
        """
        if isinstance(owner_nonce, str) and owner_nonce == caller_nonce:
            return OWN
        if doc is None:
            doc = self.read()
        rows = doc["attachments"]
        owner = rows.get(owner_nonce) if isinstance(owner_nonce, str) else None
        if owner is None:
            return UNKNOWN
        caller = rows.get(caller_nonce) if isinstance(caller_nonce, str) else None
        return _classify(caller, owner["launcher_pid"], owner["creation_identity"], identity_now)

    def lease_owner_class(self, owner, caller_nonce, *, identity_now=None, doc=None):
        """Classify a ledger lease owner ``{session_pid, identity}`` relative to the caller.

        The same launcher as the attached caller is earlier_child or own (one
        claude.exe per launcher at a time; either way not another live process).
        """
        if not isinstance(owner, dict) or not _pid(owner.get("session_pid")) \
                or not isinstance(owner.get("identity"), str) or not owner["identity"]:
            return UNKNOWN
        if doc is None:
            doc = self.read()
        caller = doc["attachments"].get(caller_nonce) if isinstance(caller_nonce, str) else None
        return _classify(caller, owner["session_pid"], owner["identity"], identity_now)

    def caller_owner(self, conversation, caller_nonce, *, identity_now=None):
        """The attached caller's launcher as a ledger owner, when it is live now; else None."""
        try:
            row = self.read()["attachments"].get(caller_nonce) if isinstance(caller_nonce, str) else None
        except (WorkParallelStateUnavailable, OSError):
            return None
        if row is None or row["conversation"] != conversation or liveness(row, identity_now) != "live":
            return None
        return {"session_pid": row["launcher_pid"], "identity": row["creation_identity"]}

    def summary(self, conversation, *, identity_now=None):
        """{live, gone, unknown} host counts for one conversation; never identities."""
        try:
            doc = self.read()
        except (WorkParallelStateUnavailable, OSError):
            return {"available": False, "live": 0, "gone": 0, "unknown": 0, "attachment_version": None}
        counts = {"live": 0, "gone": 0, "unknown": 0}
        for row in doc["attachments"].values():
            if row["conversation"] == conversation:
                counts[liveness(row, identity_now)] += 1
        return {"available": True, **counts, "attachment_version": doc["version"]}
