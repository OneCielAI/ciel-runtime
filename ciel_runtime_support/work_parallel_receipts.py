"""Host-owned coordination state for native Work forks.

The sidecar is intentionally independent of ``work-parallel.json`` and has its
own CAS version. It stores assignment manifests and bounded outcome records,
never prompts, transcripts, reasoning, tool input/output, tokens or worker
capabilities.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

from .channel_message_repository import exclusive_file_lock
from .work_parallel import WorkParallelConflict, WorkParallelError, digest, integer, number, require, text, unique_object

MAX_BYTES = 2_097_152
MAX_ROOTS = 2_000
MAX_RUNS = 4_000
MAX_RECEIPTS = 8_000
MAX_DETAILS = 4_000
MAX_ARCHIVE = 1_000
MAX_DIAGNOSTICS = 200
MAX_ARRAY = 100
MAX_TEXT = 2_000
MAX_EXIT_ATTEMPTS = 100
ARCHIVE_SECONDS = 30 * 24 * 60 * 60
RECEIPT_KINDS = ("completion", "handoff", "question", "review", "host_salvage")
NATIVE_STATUSES = ("pending", "running", "waiting", "idle", "completed", "failed", "killed", "missing")
RUN_STATES = ("reserved", "bound", "active", "waiting", "terminal", "handed_off", "superseded")
RECOVERY_STATES = ("none", "pending", "resuming", "rebound", "replacement", "failed", "salvaged")
DIAGNOSTIC_CODES = ("capacity", "launch_rate", "disabled", "paused", "no_candidate", "stale_approval",
                    "duplicate_assignment", "native_uncertain", "recovery_failed", "archive_failed",
                    "workspace_mismatch", "review_pending")
ARCHIVE_OUTCOME_FIELDS = frozenset(("state", "outcome", "receipt_digest", "artifact_digest",
                                    "proof_digest", "item_status", "published"))
RUN_FIELDS = frozenset(("scope", "lease_id", "item_id", "root_id", "priority", "role", "generation",
                        "approval_generation", "fingerprint", "agent_id", "agent_name", "state", "workspace",
                        "reserved_at", "bound_at", "state_changed_at", "native_status", "native_seen_at",
                        "exit", "exit_attempts", "recovery", "replaced_by", "assigned_todos"))


def empty_state():
    return {"schema": 1, "version": 0, "root_generations": {}, "runs": {}, "receipts": {},
            "details": {}, "scheduler": {"cursors": {}, "diagnostics": []}, "archive": [],
            "archive_dropped": 0}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _sha(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _optional_text(value, limit=200):
    return value is None or text(value, limit)


def _optional_time(value):
    return value is None or number(value, 1e15)


def _root_key(scope, root_id):
    require(text(scope, 200) and text(root_id, 200), "root identity")
    return scope + "\0" + root_id


def _bounded_strings(value, name, *, limit=MAX_ARRAY, chars=MAX_TEXT):
    require(type(value) is list and len(value) <= limit and all(text(item, chars) for item in value), name)


def _workspace(value, *, optional=False):
    if optional and value is None:
        return
    require(type(value) is dict and set(value) == {"path", "branch", "head", "dirty", "changed_files", "diff_stat"},
            "workspace fields")
    require(text(value["path"], MAX_TEXT) and text(value["branch"], 500) and text(value["head"], 200),
            "workspace identity")
    require(type(value["dirty"]) is bool and text(value["diff_stat"], MAX_TEXT), "workspace state")
    _bounded_strings(value["changed_files"], "workspace changed files", chars=MAX_TEXT)


def _manifest_workspace(value):
    if value is None:
        return
    require(type(value) is dict and set(value) == {"path", "branch", "head"}, "manifest workspace fields")
    require(text(value["path"], MAX_TEXT) and text(value["branch"], 500) and text(value["head"], 200),
            "manifest workspace")


def _validate_detail(kind, body):
    require(type(body) is dict, "receipt body")
    if kind == "completion":
        require(set(body) in ({"summary", "coverage", "verification"},
                              {"summary", "coverage", "verification", "workspace"}), "completion fields")
        require(text(body["summary"], MAX_TEXT) and text(body["verification"], MAX_TEXT), "completion text")
        coverage = body["coverage"]
        require(type(coverage) is list and len(coverage) <= MAX_ARRAY, "completion coverage")
        for row in coverage:
            require(type(row) is dict and set(row) in ({"item_id", "verified"},
                    {"item_id", "verified", "evidence_digest"}), "completion coverage fields")
            require(text(row["item_id"], 200) and type(row["verified"]) is bool, "completion coverage values")
            require("evidence_digest" not in row or digest(row["evidence_digest"]), "coverage evidence digest")
        if "workspace" in body:
            _workspace(body["workspace"], optional=True)
    elif kind in ("handoff", "host_salvage"):
        require(set(body) == {"done", "remaining", "blockers", "workspace", "next_step"}, "handoff fields")
        for field in ("done", "remaining", "blockers"):
            _bounded_strings(body[field], "handoff " + field)
        _workspace(body["workspace"])
        require(text(body["next_step"], MAX_TEXT), "handoff next step")
    elif kind == "question":
        require(set(body) == {"question", "blocking_todos", "decision_needed"}, "question fields")
        require(text(body["question"], MAX_TEXT) and text(body["decision_needed"], MAX_TEXT), "question text")
        _bounded_strings(body["blocking_todos"], "question blocking todos", chars=200)
    elif kind == "review":
        require(set(body) == {"receipt_id", "pass", "coverage", "findings", "verification"}, "review fields")
        require(text(body["receipt_id"], 200) and type(body["pass"]) is bool
                and text(body["verification"], MAX_TEXT), "review values")
        _bounded_strings(body["coverage"], "review coverage")
        _bounded_strings(body["findings"], "review findings")
    else:
        raise WorkParallelError("receipt kind")
    require(len(_canonical(body)) <= 131_072, "receipt body byte limit")


def _validate_archive_summary(summary):
    require(type(summary) is dict and set(summary) <= ARCHIVE_OUTCOME_FIELDS, "archive summary fields")
    for key, value in summary.items():
        if key in ("receipt_digest", "artifact_digest", "proof_digest"):
            require(value is None or digest(value), "archive digest")
        elif key == "published":
            require(type(value) is bool, "archive published")
        else:
            require(text(value, 500), "archive outcome")


def _validate_root(key, row):
    require(type(row) is dict and set(row) == {"scope", "root_id", "phase", "scope_digest",
            "approval_generation", "updated_at"}, "root generation fields")
    require(key == _root_key(row["scope"], row["root_id"]), "root generation identity")
    require(row["phase"] in ("planning", "executing") and digest(row["scope_digest"]), "root approval")
    require(integer(row["approval_generation"]) and number(row["updated_at"], 1e15), "root generation")


def _validate_run(lease_id, row):
    require(type(row) is dict and set(row) == RUN_FIELDS, "run fields")
    require(row["lease_id"] == lease_id and all(text(row[key], 200) for key in ("scope", "lease_id", "item_id", "root_id")),
            "run identity")
    require(row["priority"] in ("P1", "P2", "P3", "P4", "P5") and row["role"] in ("worker", "reviewer", "resume"),
            "run priority/role")
    require(integer(row["generation"], 1) and integer(row["approval_generation"], 1)
            and digest(row["fingerprint"]), "run generation/fingerprint")
    require(_optional_text(row["agent_id"]) and _optional_text(row["agent_name"]), "run agent")
    require(row["state"] in RUN_STATES and (row["native_status"] is None or row["native_status"] in NATIVE_STATUSES),
            "run state")
    _manifest_workspace(row["workspace"])
    _bounded_strings(row["assigned_todos"], "assigned todos", chars=200)
    require(number(row["reserved_at"], 1e15) and _optional_time(row["bound_at"])
            and number(row["state_changed_at"], 1e15) and _optional_time(row["native_seen_at"]), "run times")
    exit_row = row["exit"]
    require(exit_row is None or (type(exit_row) is dict and set(exit_row) == {"kind", "digest"}
            and exit_row["kind"] in RECEIPT_KINDS and digest(exit_row["digest"])), "run exit")
    require(integer(row["exit_attempts"], 0, MAX_EXIT_ATTEMPTS), "exit attempts")
    recovery = row["recovery"]
    require(type(recovery) is dict and set(recovery) == {"state", "attempts", "error_code"}
            and recovery["state"] in RECOVERY_STATES and integer(recovery["attempts"], 0, MAX_EXIT_ATTEMPTS)
            and _optional_text(recovery["error_code"], 80), "run recovery")
    require(_optional_text(row["replaced_by"]), "run replacement")


def validate(state):
    require(type(state) is dict and set(state) == {"schema", "version", "root_generations", "runs", "receipts",
            "details", "scheduler", "archive", "archive_dropped"}, "coordination fields")
    require(state["schema"] == 1 and integer(state["version"]), "coordination schema/version")
    require(type(state["root_generations"]) is dict and len(state["root_generations"]) <= MAX_ROOTS, "root bound")
    for key, row in state["root_generations"].items():
        _validate_root(key, row)
    require(type(state["runs"]) is dict and len(state["runs"]) <= MAX_RUNS, "run bound")
    for key, row in state["runs"].items():
        _validate_run(key, row)
    require(type(state["details"]) is dict and len(state["details"]) <= MAX_DETAILS, "detail bound")
    for key, row in state["details"].items():
        require(digest(key) and type(row) is dict and set(row) == {"kind", "body"} and row["kind"] in RECEIPT_KINDS,
                "detail record")
        _validate_detail(row["kind"], row["body"])
        require(_sha(row["body"]) == key, "detail digest mismatch")
    require(type(state["receipts"]) is dict and len(state["receipts"]) <= MAX_RECEIPTS, "receipt bound")
    for key, row in state["receipts"].items():
        require(digest(key) and type(row) is dict and set(row) == {"digest", "lease_id", "kind", "created_at"}
                and row["digest"] == key and text(row["lease_id"], 200) and row["kind"] in RECEIPT_KINDS
                and number(row["created_at"], 1e15) and key in state["details"], "receipt record")
    scheduler = state["scheduler"]
    require(type(scheduler) is dict and set(scheduler) == {"cursors", "diagnostics"}, "scheduler fields")
    require(type(scheduler["cursors"]) is dict and len(scheduler["cursors"]) <= MAX_ROOTS, "cursor bound")
    for scope, cursor in scheduler["cursors"].items():
        require(text(scope, 200) and (cursor is None or text(cursor, 200)), "scheduler cursor")
    require(type(scheduler["diagnostics"]) is list and len(scheduler["diagnostics"]) <= MAX_DIAGNOSTICS, "diagnostic bound")
    for row in scheduler["diagnostics"]:
        require(type(row) is dict and set(row) == {"scope", "priority", "code", "count", "at"}
                and text(row["scope"], 200) and row["priority"] in ("P1", "P2", "P3", "P4", "P5")
                and row["code"] in DIAGNOSTIC_CODES and integer(row["count"], 1)
                and number(row["at"], 1e15), "diagnostic record")
    require(type(state["archive"]) is list and len(state["archive"]) <= MAX_ARCHIVE, "archive bound")
    for row in state["archive"]:
        require(type(row) is dict and set(row) == {"run", "summary", "archived_at"}, "archive fields")
        _validate_run(row["run"]["lease_id"], row["run"])
        _validate_archive_summary(row["summary"])
        require(number(row["archived_at"], 1e15), "archive time")
    require(integer(state["archive_dropped"]), "archive dropped count")


class WorkCoordinationStore:
    """Separate, versioned CAS store; a read of a missing file creates nothing."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self._lock = threading.RLock()

    def _read(self):
        try:
            with self.path.open("rb") as handle:
                raw = handle.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return empty_state()
        require(len(raw) <= MAX_BYTES, "coordination byte limit")
        try:
            state = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
            validate(state)
            return state
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
            raise WorkParallelError("corrupt Work coordination state; writes blocked") from error

    def read(self):
        if not self.path.exists():
            return empty_state()
        with self._lock, exclusive_file_lock(self.path):
            return self._read()

    def _change(self, fn, expected_version):
        require(integer(expected_version), "expected_version required")
        with self._lock, exclusive_file_lock(self.path):
            before = self._read()
            if before["version"] != expected_version:
                raise WorkParallelConflict("Work coordination changed; reread")
            state = copy.deepcopy(before)
            result = fn(state)
            if state != before:
                state["version"] += 1
                validate(state)
                raw = _canonical(state)
                require(len(raw) <= MAX_BYTES, "coordination byte limit")
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
                try:
                    with tmp.open("xb") as handle:
                        handle.write(raw)
                        handle.flush()
                        os.fsync(handle.fileno())
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
            return copy.deepcopy(state), copy.deepcopy(result)

    def reconcile_roots(self, scope, roots, *, expected_version):
        require(text(scope, 200) and type(roots) is list and len(roots) <= MAX_ROOTS, "root reconciliation")
        clean = {}
        for root in roots:
            require(type(root) is dict and set(root) == {"root_id", "phase", "scope_digest"}, "root fields")
            require(text(root["root_id"], 200) and root["phase"] in ("planning", "executing")
                    and digest(root["scope_digest"]), "root values")
            require(root["root_id"] not in clean, "duplicate root")
            clean[root["root_id"]] = copy.deepcopy(root)
        stamp = time.time()
        def apply(state):
            result = {}
            for root_id, incoming in clean.items():
                key = _root_key(scope, root_id)
                old = state["root_generations"].get(key)
                generation = 0 if old is None else old["approval_generation"]
                entered = incoming["phase"] == "executing" and (old is None or old["phase"] != "executing")
                scope_changed = (incoming["phase"] == "executing" and old is not None
                                 and old["phase"] == "executing" and old["scope_digest"] != incoming["scope_digest"])
                if entered or scope_changed:
                    generation += 1
                row = {"scope": scope, **incoming, "approval_generation": generation, "updated_at": stamp}
                if old is not None and all(old[k] == row[k] for k in row if k != "updated_at"):
                    row["updated_at"] = old["updated_at"]
                state["root_generations"][key] = row
                result[root_id] = generation
            return result
        return self._change(apply, expected_version)

    def record_assignment(self, scope, lease_id, manifest, *, expected_version):
        require(text(scope, 200) and text(lease_id, 200) and type(manifest) is dict, "assignment identity")
        fields = {"item_id", "root_id", "priority", "role", "generation", "approval_generation",
                  "assigned_todos", "fingerprint", "workspace", "created_at"}
        require(set(manifest) == fields, "assignment manifest fields")
        require(text(manifest["item_id"], 200) and text(manifest["root_id"], 200)
                and manifest["priority"] in ("P1", "P2", "P3", "P4", "P5")
                and manifest["role"] in ("worker", "reviewer", "resume"), "assignment manifest identity")
        require(integer(manifest["generation"], 1) and integer(manifest["approval_generation"], 1)
                and digest(manifest["fingerprint"]) and number(manifest["created_at"], 1e15), "assignment manifest values")
        _bounded_strings(manifest["assigned_todos"], "assigned todos", chars=200)
        _manifest_workspace(manifest["workspace"])
        def apply(state):
            require(lease_id not in state["runs"], "assignment already exists")
            root = state["root_generations"].get(_root_key(scope, manifest["root_id"]))
            require(root is not None and root["phase"] == "executing"
                    and root["approval_generation"] == manifest["approval_generation"], "stale root approval")
            require(len(state["runs"]) < MAX_RUNS, "run capacity reached")
            run = {"scope": scope, "lease_id": lease_id,
                   **{key: copy.deepcopy(value) for key, value in manifest.items() if key != "created_at"},
                   "agent_id": None, "agent_name": None, "state": "reserved",
                   "reserved_at": manifest["created_at"], "bound_at": None,
                   "state_changed_at": manifest["created_at"], "native_status": None,
                   "native_seen_at": None, "exit": None, "exit_attempts": 0,
                   "recovery": {"state": "none", "attempts": 0, "error_code": None},
                   "replaced_by": None}
            _validate_run(lease_id, run)
            state["runs"][lease_id] = run
            return run
        return self._change(apply, expected_version)

    def attach_workspace(self, lease_id, workspace, *, expected_version):
        _manifest_workspace(workspace)
        def apply(state):
            run = state["runs"].get(lease_id)
            require(run is not None and run["state"] == "reserved" and run["agent_id"] is None,
                    "run workspace is not attachable")
            if run["workspace"] is not None:
                require(run["workspace"] == workspace, "run workspace changed")
                return run
            run["workspace"] = copy.deepcopy(workspace)
            return run
        return self._change(apply, expected_version)

    def cancel_reserved(self, lease_id, *, outcome, expected_version):
        """Retire a generation that never launched; it owns no native worker."""
        require(text(outcome, 500), "cancel outcome")
        stamp = time.time()
        def apply(state):
            run = state["runs"].get(lease_id)
            require(run is not None and run["state"] == "reserved" and run["agent_id"] is None,
                    "run is not an unbound reservation")
            run["state"] = "terminal"
            run["state_changed_at"] = stamp
            self._archive(state, run, {"state": "terminal", "outcome": outcome}, stamp)
            state["runs"].pop(lease_id)
            return run
        return self._change(apply, expected_version)

    def bind_run(self, lease_id, agent_id, agent_name, *, expected_version):
        require(text(lease_id, 200) and text(agent_id, 200) and text(agent_name, 200), "run binding")
        stamp = time.time()
        def apply(state):
            run = state["runs"].get(lease_id)
            require(run is not None and run["state"] == "reserved" and run["agent_id"] is None, "run is not bindable")
            require(not any(row["agent_id"] == agent_id and row["state"] not in ("terminal", "superseded", "handed_off")
                            for row in state["runs"].values()), "agent already bound")
            run.update(agent_id=agent_id, agent_name=agent_name, state="bound", bound_at=stamp,
                       state_changed_at=stamp, native_status="pending", native_seen_at=stamp)
            return run
        return self._change(apply, expected_version)

    def update_native(self, lease_id, status, *, expected_version):
        require(text(lease_id, 200) and status in NATIVE_STATUSES, "native status")
        stamp = time.time()
        def apply(state):
            run = state["runs"].get(lease_id)
            require(run is not None and run["agent_id"] is not None, "unknown bound run")
            require(run["native_status"] not in ("completed", "failed", "killed")
                    or run["native_status"] == status, "terminal native status is immutable")
            if run["native_status"] == status:
                return run
            run["native_status"] = status
            run["native_seen_at"] = stamp
            run["state_changed_at"] = stamp
            if status in ("pending", "running"):
                run["state"] = "active"
            elif status == "waiting":
                run["state"] = "waiting"
            elif status in ("completed", "failed", "killed"):
                run["state"] = "terminal"
            return run
        return self._change(apply, expected_version)

    def persist_receipt(self, lease_id, kind, body=None, *, detail=None, detail_digest=None,
                        expected_version):
        require(text(lease_id, 200) and kind in RECEIPT_KINDS, "receipt identity")
        require((body is None) != (detail is None), "exactly one receipt body/detail required")
        body = detail if detail is not None else body
        _validate_detail(kind, body)
        body = copy.deepcopy(body)
        receipt_digest = _sha(body)
        require(detail_digest is None or (digest(detail_digest) and detail_digest == receipt_digest),
                "receipt detail digest mismatch")
        stamp = time.time()
        def apply(state):
            run = state["runs"].get(lease_id)
            require(run is not None, "unknown run")
            prior_detail = state["details"].get(receipt_digest)
            detail_row = {"kind": kind, "body": body}
            if prior_detail is not None and prior_detail != detail_row:
                raise WorkParallelConflict("receipt digest collision")
            prior = state["receipts"].get(receipt_digest)
            if prior is not None:
                require(prior["lease_id"] == lease_id and prior["kind"] == kind, "receipt already belongs to another run")
                return receipt_digest
            require(len(state["details"]) < MAX_DETAILS and len(state["receipts"]) < MAX_RECEIPTS, "receipt capacity reached")
            state["details"][receipt_digest] = detail_row
            state["receipts"][receipt_digest] = {"digest": receipt_digest, "lease_id": lease_id,
                                                   "kind": kind, "created_at": stamp}
            run["exit"] = {"kind": kind, "digest": receipt_digest}
            run["state_changed_at"] = stamp
            if kind in ("handoff", "host_salvage"):
                run["state"] = "handed_off"
            elif kind == "question":
                run["state"] = "waiting"
            else:
                run["state"] = "terminal"
            return receipt_digest
        return self._change(apply, expected_version)

    def detail(self, detail_digest):
        require(digest(detail_digest), "detail digest")
        state = self.read()
        row = state["details"].get(detail_digest)
        require(row is not None, "unknown receipt detail")
        return copy.deepcopy(row["body"])

    def exit_attempt(self, lease_id, *, expected_version):
        def apply(state):
            run = state["runs"].get(lease_id)
            require(run is not None and run["exit_attempts"] < MAX_EXIT_ATTEMPTS, "unknown run or exit attempt bound")
            run["exit_attempts"] += 1
            return run["exit_attempts"]
        return self._change(apply, expected_version)

    def mark_recovery(self, lease_id, state_value, *, error_code=None, expected_version):
        require(state_value in RECOVERY_STATES and state_value != "none", "recovery state")
        require(error_code is None or text(error_code, 80), "recovery error code")
        def apply(state):
            run = state["runs"].get(lease_id)
            require(run is not None, "unknown run")
            attempts = run["recovery"]["attempts"] + 1
            require(attempts <= MAX_EXIT_ATTEMPTS, "recovery attempt bound")
            run["recovery"] = {"state": state_value, "attempts": attempts, "error_code": error_code}
            return run["recovery"]
        return self._change(apply, expected_version)

    @staticmethod
    def _archive(state, run, summary, stamp):
        _validate_archive_summary(summary)
        state["archive"].append({"run": copy.deepcopy(run), "summary": copy.deepcopy(summary), "archived_at": stamp})
        cutoff = stamp - ARCHIVE_SECONDS
        kept = [row for row in state["archive"] if row["archived_at"] >= cutoff]
        dropped = len(state["archive"]) - len(kept)
        if len(kept) > MAX_ARCHIVE:
            dropped += len(kept) - MAX_ARCHIVE
            kept = kept[-MAX_ARCHIVE:]
        state["archive"] = kept
        state["archive_dropped"] += dropped

    def supersede(self, old_lease_id, new_lease_id, *, expected_version):
        stamp = time.time()
        def apply(state):
            old = state["runs"].get(old_lease_id)
            new = state["runs"].get(new_lease_id)
            require(old is not None and new is not None and old["item_id"] == new["item_id"], "invalid replacement")
            require(old["replaced_by"] in (None, new_lease_id), "run already replaced")
            old["replaced_by"] = new_lease_id
            old["state"] = "superseded"
            old["state_changed_at"] = stamp
            self._archive(state, old, {"state": "superseded", "outcome": "replacement"}, stamp)
            state["runs"].pop(old_lease_id)
            return new
        return self._change(apply, expected_version)

    def diagnostic(self, scope, priority, code, *, expected_version):
        require(text(scope, 200) and priority in ("P1", "P2", "P3", "P4", "P5")
                and code in DIAGNOSTIC_CODES, "diagnostic")
        stamp = time.time()
        def apply(state):
            rows = state["scheduler"]["diagnostics"]
            if rows and all(rows[-1][key] == value for key, value in (("scope", scope), ("priority", priority), ("code", code))):
                rows[-1]["count"] += 1
                rows[-1]["at"] = stamp
            else:
                rows.append({"scope": scope, "priority": priority, "code": code, "count": 1, "at": stamp})
                state["scheduler"]["diagnostics"] = rows[-MAX_DIAGNOSTICS:]
            return state["scheduler"]["diagnostics"][-1]
        return self._change(apply, expected_version)

    def archive_terminal(self, lease_id, summary, *, expected_version):
        _validate_archive_summary(summary)
        stamp = time.time()
        def apply(state):
            run = state["runs"].get(lease_id)
            require(run is not None and run["state"] in ("terminal", "handed_off", "superseded"), "run is not terminal")
            self._archive(state, run, summary, stamp)
            state["runs"].pop(lease_id)
            return state["archive"][-1]
        return self._change(apply, expected_version)

    def projection(self, scope, *, limit=50):
        require(text(scope, 200) and integer(limit, 1, 200), "projection")
        state = self.read()
        runs = [copy.deepcopy(row) for row in state["runs"].values() if row["scope"] == scope]
        runs.sort(key=lambda row: (row["reserved_at"], row["lease_id"]), reverse=True)
        lease_ids = {run["lease_id"] for run in runs[:limit]}
        receipts = [{key: row[key] for key in ("digest", "lease_id", "kind", "created_at")}
                    for row in state["receipts"].values() if row["lease_id"] in lease_ids]
        receipts.sort(key=lambda row: (row["created_at"], row["digest"]), reverse=True)
        archive = []
        for row in reversed(state["archive"]):
            if row["run"]["scope"] != scope:
                continue
            archive.append({"lease_id": row["run"]["lease_id"], "item_id": row["run"]["item_id"],
                            "root_id": row["run"]["root_id"], "state": row["run"]["state"],
                            "exit": copy.deepcopy(row["run"]["exit"]), "summary": copy.deepcopy(row["summary"]),
                            "archived_at": row["archived_at"]})
            if len(archive) == limit:
                break
        return {"version": state["version"], "runs": runs[:limit], "receipts": receipts[:limit],
                "diagnostics": copy.deepcopy(state["scheduler"]["diagnostics"][-limit:]),
                "archive": archive, "archive_dropped": state["archive_dropped"],
                "truncated": len(runs) > limit or len(receipts) > limit}


def coordination_store(store):
    return WorkCoordinationStore(Path(store.path).with_name("work-parallel-coordination.json"))
