"""Strict durable main-review, wake, launch-intent and roll-forward journal.

This sidecar never changes the legacy ``work-parallel.json`` schema and never
stores a plaintext worker capability. Every cross-store effect is idempotent;
its completion is fenced by a journal stage under this store's independent CAS.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import threading
import time
import uuid
from pathlib import Path

from .channel_message_repository import exclusive_file_lock
from .work_parallel import (WorkParallelConflict, WorkParallelError, digest,
                            integer, number, require, text, unique_object)

MAX_BYTES = 2_097_152
MAX_REVIEWS = 2_000
MAX_PROJECTION = 24
SCHEMA = 5
STAGES = ("accepted", "workspace", "board", "lease", "archive")
STATES = ("pending", "awaiting_capacity", "deciding", "rework_ready", "finalized")
DECISIONS = (None, "approve", "rework")
WAKE_STATES = ("pending", "submitting", "appended", "queued", "uncertain", "failed", "acknowledged")
LAUNCH_STATES = ("none", "launch_intent", "spawn_denied", "spawn_observed", "manual_requested", "bound")
LAUNCH_AUDIT_KINDS = ("denied", "absent", "manual_requested", "manual", "present")
MAX_LAUNCH_AUDIT = 20
UNRESOLVED_LAUNCH_STATES = ("launch_intent", "spawn_observed", "manual_requested")
_LAUNCH_REF = re.compile(r"[0-9a-f]{32}\Z")
LAUNCH_AUDIT_FIELDS = frozenset(("kind", "dispatch_name", "attempt", "observer_nonce", "at", "detail"))


def empty_state():
    return {"schema": SCHEMA, "version": 0, "reviews": {}}


def canonical_digest(value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def decision_digest(lease_id, decision, evidence, findings=None, rework_instructions=None):
    """Bind the decision id to every semantic field supplied by main."""
    body = {"lease_id": lease_id, "decision": decision, "evidence": evidence,
            "findings": [] if findings is None else findings,
            "rework_instructions": "" if rework_instructions is None else rework_instructions}
    require(type(body["findings"]) is list and len(body["findings"]) <= 100
            and all(text(value, 2_000) for value in body["findings"]), "decision findings")
    require(type(body["rework_instructions"]) is str and len(body["rework_instructions"]) <= 2_000,
            "rework instructions")
    return canonical_digest(body)


def _workspace(value):
    require(type(value) is dict and set(value) == {
        "repo", "path", "branch", "head", "dirty", "changed_files", "changed_files_total",
        "diff_stat", "snapshot_digest", "receipt_digest"
    }, "review workspace fields")
    require(integer(value["changed_files_total"])
            and value["changed_files_total"] >= len(value["changed_files"]), "review changed file total")
    require(all(text(value[k], 2_000) for k in ("repo", "path", "branch", "head")),
            "review workspace identity")
    require(type(value["dirty"]) is bool, "review workspace dirty")
    require(type(value["changed_files"]) is list and len(value["changed_files"]) <= 100
            and all(text(row, 2_000) for row in value["changed_files"]), "review changed files")
    require(type(value["diff_stat"]) is str and len(value["diff_stat"]) <= 16_384,
            "review diff stat")
    require(digest(value["snapshot_digest"]) and digest(value["receipt_digest"]),
            "review snapshot digest")


def _wake(value, *, decision):
    require(type(value) is dict and set(value) == {"state", "attempt_id", "attempts", "updated_at"},
            "review wake fields")
    require(value["state"] in WAKE_STATES and integer(value["attempts"], 0, 100), "review wake state")
    require(value["attempt_id"] is None or text(value["attempt_id"], 200), "review wake attempt")
    require(number(value["updated_at"], 1e15), "review wake time")
    if value["state"] in ("pending", "acknowledged"):
        require(value["attempt_id"] is None, "inactive wake attempt remains")
    else:
        require(value["attempt_id"] is not None and value["attempts"] >= 1, "active wake attempt missing")
    require((decision is None) == (value["state"] != "acknowledged"), "review wake acknowledgement")


def _launch_audit(rows):
    require(type(rows) is list and len(rows) <= MAX_LAUNCH_AUDIT, "launch audit bound")
    for row in rows:
        require(type(row) is dict and set(row) == LAUNCH_AUDIT_FIELDS
                and row["kind"] in LAUNCH_AUDIT_KINDS and text(row["dispatch_name"], 200)
                and integer(row["attempt"], 1, 100)
                and (row["observer_nonce"] is None or text(row["observer_nonce"], 200))
                and number(row["at"], 1e15) and text(row["detail"], 500), "launch audit row")


def new_launch_ref():
    """Opaque 128-bit reference to one exact launch attempt; not a capability."""
    return secrets.token_hex(16)


def launch_ref_valid(value):
    return isinstance(value, str) and _LAUNCH_REF.fullmatch(value) is not None


def _launch(value, *, decision, replacement_generation):
    require(type(value) is dict and set(value) == {
        "state", "dispatch_name", "attempt", "generation", "agent_id", "proof", "host_nonce",
        "manual_from", "launch_ref", "audit", "updated_at"
    }, "review launch fields")
    require((value["launch_ref"] is not None) == (value["state"] in UNRESOLVED_LAUNCH_STATES)
            and (value["launch_ref"] is None or launch_ref_valid(value["launch_ref"])),
            "launch reference exists exactly while the launch is unresolved")
    require(value["host_nonce"] is None or text(value["host_nonce"], 200), "launch host nonce")
    require(value["manual_from"] is None or value["manual_from"] in ("launch_intent", "spawn_observed"),
            "manual request source")
    _launch_audit(value["audit"])
    require(value["state"] in LAUNCH_STATES and integer(value["attempt"], 0, 100), "review launch state")
    require(value["dispatch_name"] is None or text(value["dispatch_name"], 200), "dispatch name")
    require(value["generation"] is None or integer(value["generation"], 2), "launch generation")
    require(value["agent_id"] is None or text(value["agent_id"], 200), "launch agent")
    require(value["proof"] is None or text(value["proof"], 500), "launch proof")
    require(number(value["updated_at"], 1e15), "launch time")
    if decision != "rework":
        require(value["state"] == "none" and value["attempt"] == 0 and value["audit"] == []
                and all(value[key] is None for key in ("dispatch_name", "generation", "agent_id",
                                                       "proof", "host_nonce", "manual_from")),
                "non-rework launch state")
        return
    require(value["generation"] in (None, replacement_generation), "launch generation changed")
    if value["state"] == "none":
        require(value["dispatch_name"] is None and value["attempt"] == 0 and value["agent_id"] is None
                and value["proof"] is None and value["host_nonce"] is None
                and value["manual_from"] is None and value["audit"] == [], "empty launch state")
    elif value["state"] == "spawn_denied":
        require(value["dispatch_name"] is not None and value["attempt"] >= 1
                and value["agent_id"] is None and value["proof"] is not None
                and value["manual_from"] is None, "spawn denial proof")
        # Every resolution that permits a fresh token leaves its audit row.
        require(bool(value["audit"]) and value["audit"][-1]["dispatch_name"] == value["dispatch_name"]
                and value["audit"][-1]["attempt"] == value["attempt"], "spawn denial audit")
    elif value["state"] == "launch_intent":
        require(value["dispatch_name"] is not None and value["attempt"] >= 1 and value["agent_id"] is None
                and value["proof"] is None and value["manual_from"] is None, "launch intent")
    elif value["state"] == "manual_requested":
        require(value["dispatch_name"] is not None and value["attempt"] >= 1
                and value["manual_from"] in ("launch_intent", "spawn_observed")
                and (value["manual_from"] != "spawn_observed" or value["agent_id"] is not None),
                "manual request")
    else:
        require(value["dispatch_name"] is not None and value["attempt"] >= 1 and value["agent_id"] is not None
                and value["manual_from"] is None, "observed launch")


def _prefix(stages):
    values = [stages[key] for key in STAGES]
    return values == sorted(values, reverse=True)


def _review(key, row):
    fields = {"lease_id", "scope", "item_id", "root_id", "priority", "plan_version",
              "generation", "artifact", "completion_receipt", "fingerprint", "summary",
              "coverage", "verification", "workspace", "state", "decision_id", "decision",
              "decision_digest", "evidence", "replacement_lease_id", "replacement_generation",
              "stages", "wake", "launch", "blocked", "updated_at"}
    require(type(row) is dict and set(row) == fields, "review fields")
    require(key == row["lease_id"] and all(text(row[k], 200) for k in
            ("lease_id", "scope", "item_id", "root_id")), "review identity")
    require(row["priority"] in ("P1", "P2", "P3", "P4", "P5")
            and integer(row["plan_version"]) and integer(row["generation"], 1), "review assignment")
    require(all(digest(row[k]) for k in ("artifact", "completion_receipt", "fingerprint")),
            "review digests")
    require(text(row["summary"], 2_000) and text(row["verification"], 2_000), "review receipt text")
    require(type(row["coverage"]) is list and len(row["coverage"]) <= 100, "review coverage")
    for entry in row["coverage"]:
        require(type(entry) is dict and set(entry) <= {"item_id", "verified", "evidence_digest"}
                and {"item_id", "verified"} <= set(entry) and text(entry["item_id"], 200)
                and type(entry["verified"]) is bool
                and ("evidence_digest" not in entry or digest(entry["evidence_digest"])),
                "review coverage entry")
    _workspace(row["workspace"])
    require(row["state"] in STATES and row["decision"] in DECISIONS, "review state")
    require(row["decision_id"] is None or text(row["decision_id"], 200), "review decision id")
    require(row["decision_digest"] is None or digest(row["decision_digest"]), "review decision digest")
    require(row["evidence"] is None or text(row["evidence"], 2_000), "review evidence")
    require(row["replacement_lease_id"] is None or text(row["replacement_lease_id"], 200),
            "replacement lease")
    require(row["replacement_generation"] is None or integer(row["replacement_generation"], 2),
            "replacement generation")
    require(type(row["stages"]) is dict and set(row["stages"]) == set(STAGES)
            and all(type(value) is bool for value in row["stages"].values()) and _prefix(row["stages"]),
            "review stages must be an ordered prefix")
    require(number(row["updated_at"], 1e15), "review time")
    blocked = row["blocked"]
    require(blocked is None or (type(blocked) is dict and set(blocked) == {"code", "message", "at"}
            and text(blocked["code"], 80) and text(blocked["message"], 300)
            and number(blocked["at"], 1e15)), "review blocked reason")
    require((row["state"] == "awaiting_capacity") <= (blocked is not None)
            and (blocked is None or row["state"] in ("awaiting_capacity", "deciding")),
            "review blocked invariant")
    _wake(row["wake"], decision=row["decision"])
    _launch(row["launch"], decision=row["decision"], replacement_generation=row["replacement_generation"])
    completed = all(row["stages"].values())
    empty = not any(row["stages"].values())
    if row["state"] == "pending":
        require(row["decision"] is None and row["decision_id"] is None
                and row["decision_digest"] is None and row["evidence"] is None and empty
                and row["replacement_lease_id"] is None and row["replacement_generation"] is None,
                "pending review invariant")
    elif row["decision"] == "approve":
        require(row["decision_id"] is not None and row["decision_digest"] is not None
                and row["evidence"] is not None and row["replacement_lease_id"] is None
                and row["replacement_generation"] is None, "approve decision invariant")
        require((row["state"] == "finalized") == completed, "approve finalization invariant")
        require(row["state"] in ("deciding", "finalized"), "approve state")
    elif row["decision"] == "rework":
        require(row["decision_id"] is not None and row["decision_digest"] is not None
                and row["evidence"] is not None and row["replacement_lease_id"] is not None
                and row["replacement_generation"] == row["generation"] + 1,
                "rework decision invariant")
        require(row["state"] in ("awaiting_capacity", "deciding", "rework_ready")
                and (row["state"] == "rework_ready") == completed
                and (row["state"] != "awaiting_capacity" or empty), "rework ready invariant")
    else:
        raise WorkParallelError("decided review missing verdict")


def validate(state):
    require(type(state) is dict and set(state) == {"schema", "version", "reviews"},
            "finalization fields")
    require(state["schema"] == SCHEMA and integer(state["version"]), "finalization schema/version")
    require(type(state["reviews"]) is dict and len(state["reviews"]) <= MAX_REVIEWS,
            "finalization review bound")
    for key, row in state["reviews"].items():
        _review(key, row)


def _migrated_launch_ref(row, launch):
    material = json.dumps(["work-parallel-launch-ref-v1", row.get("lease_id"), row.get("replacement_lease_id"),
                           launch.get("state"), launch.get("dispatch_name"), launch.get("attempt"),
                           launch.get("generation"), launch.get("updated_at")],
                          ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _migrate(state):
    """In-memory schema 1-4 migration; the next mutation atomically writes schema 5."""
    if type(state) is not dict or state.get("schema") not in (1, 2, 3, 4):
        return state
    require(set(state) == {"schema", "version", "reviews"} and type(state["reviews"]) is dict,
            "legacy finalization fields")
    stamp = time.time()
    migrated = copy.deepcopy(state)
    migrated["schema"] = SCHEMA
    for row in migrated["reviews"].values():
        row.setdefault("decision_digest", None if row.get("decision") is None else
                       decision_digest(row["lease_id"], row["decision"], row.get("evidence") or ""))
        row.setdefault("wake", {"state": "pending" if row.get("decision") is None else "acknowledged",
                                "attempt_id": None, "attempts": 0, "updated_at": stamp})
        row.setdefault("launch", {"state": "none", "dispatch_name": None, "attempt": 0,
                                  "generation": None, "agent_id": None, "proof": None,
                                  "host_nonce": None, "audit": [], "updated_at": stamp})
        row.setdefault("blocked", None)
        launch = row["launch"]
        if type(launch) is dict:
            launch.setdefault("host_nonce", None)
            launch.setdefault("manual_from", None)
            if "launch_ref" not in launch:
                # Reads migrate in memory until the next write, so the
                # reference must be the same on every read of this row.
                launch["launch_ref"] = (_migrated_launch_ref(row, launch)
                                        if launch.get("state") in UNRESOLVED_LAUNCH_STATES else None)
            if "audit" not in launch:
                # A schema-2 denial carried no audit row; synthesize the one it implies.
                launch["audit"] = [] if launch.get("state") != "spawn_denied" else [{
                    "kind": "denied", "dispatch_name": launch.get("dispatch_name"),
                    "attempt": launch.get("attempt"), "observer_nonce": None,
                    "at": launch.get("updated_at", stamp), "detail": launch.get("proof") or "migrated denial"}]
    return migrated


class WorkParallelFinalizationStore:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self._lock = threading.RLock()

    def _read(self):
        try:
            with self.path.open("rb") as handle:
                raw = handle.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return empty_state()
        require(len(raw) <= MAX_BYTES, "finalization byte limit")
        try:
            state = _migrate(json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object))
            validate(state)
            return state
        except (UnicodeDecodeError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
            raise WorkParallelError("corrupt Work finalization state; writes blocked") from error

    def read(self):
        if not self.path.exists():
            return empty_state()
        with self._lock, exclusive_file_lock(self.path):
            return self._read()

    def _change(self, fn, expected_version):
        require(integer(expected_version), "expected_finalization_version required")
        with self._lock, exclusive_file_lock(self.path):
            before = self._read()
            if before["version"] != expected_version:
                raise WorkParallelConflict("Work finalization changed; reread")
            state = copy.deepcopy(before)
            result = fn(state)
            if state != before:
                state["version"] += 1
                validate(state)
                raw = json.dumps(state, ensure_ascii=False, allow_nan=False,
                                 sort_keys=True, separators=(",", ":")).encode("utf-8")
                require(len(raw) <= MAX_BYTES, "finalization byte limit")
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
                try:
                    with tmp.open("xb") as handle:
                        handle.write(raw)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(tmp, self.path)
                finally:
                    tmp.unlink(missing_ok=True)
            return copy.deepcopy(state), copy.deepcopy(result)

    def pending(self, facts, *, expected_version):
        require(type(facts) is dict, "review facts")
        stamp = time.time()
        row = {**copy.deepcopy(facts), "state": "pending", "decision_id": None,
               "decision": None, "decision_digest": None, "evidence": None,
               "replacement_lease_id": None, "replacement_generation": None,
               "stages": {key: False for key in STAGES},
               "wake": {"state": "pending", "attempt_id": None, "attempts": 0, "updated_at": stamp},
               "launch": {"state": "none", "dispatch_name": None, "attempt": 0, "generation": None,
                          "agent_id": None, "proof": None, "host_nonce": None, "manual_from": None, "launch_ref": None,
                          "audit": [],
                          "updated_at": stamp},
               "blocked": None, "updated_at": stamp}
        _review(row.get("lease_id"), row)
        def apply(state):
            prior = state["reviews"].get(row["lease_id"])
            if prior is not None:
                immutable = ("scope", "item_id", "root_id", "priority", "plan_version", "generation",
                             "artifact", "completion_receipt", "fingerprint", "summary", "coverage",
                             "verification", "workspace")
                require(all(prior[key] == row[key] for key in immutable), "completion review facts changed")
                return prior
            require(len(state["reviews"]) < MAX_REVIEWS, "finalization review bound")
            state["reviews"][row["lease_id"]] = row
            return row
        return self._change(apply, expected_version)

    def decide(self, lease_id, decision, decision_id, evidence, *, decision_body_digest,
               expected_version, replacement_lease_id=None):
        require(decision in ("approve", "rework") and text(decision_id, 200)
                and text(evidence, 2_000) and digest(decision_body_digest), "review decision")
        def apply(state):
            row = state["reviews"].get(lease_id)
            require(row is not None, "result is not pending main review")
            if row["decision"] is not None:
                require(row["decision"] == decision and row["decision_id"] == decision_id
                        and row["decision_digest"] == decision_body_digest,
                        "review already decided differently")
                return row
            row.update(decision=decision, decision_id=decision_id,
                       decision_digest=decision_body_digest, evidence=evidence,
                       state="deciding", wake={"state": "acknowledged", "attempt_id": None,
                                               "attempts": row["wake"]["attempts"],
                                               "updated_at": time.time()}, updated_at=time.time())
            if decision == "rework":
                require(text(replacement_lease_id, 200), "replacement lease required")
                row.update(replacement_lease_id=replacement_lease_id,
                           replacement_generation=row["generation"] + 1)
            return row
        return self._change(apply, expected_version)

    def await_capacity(self, lease_id, code, message, *, expected_version):
        """A durable rework waits for admission; the predecessor stays the fence."""
        require(text(code, 80) and text(message, 300), "admission wait reason")
        def apply(state):
            row = state["reviews"].get(lease_id)
            require(row is not None and row["decision"] == "rework"
                    and not any(row["stages"].values()), "rework is no longer waiting for admission")
            if row["state"] == "awaiting_capacity" and row["blocked"] and row["blocked"]["code"] == code                     and row["blocked"]["message"] == message:
                return row
            row.update(state="awaiting_capacity", updated_at=time.time(),
                       blocked={"code": code, "message": message, "at": time.time()})
            return row
        return self._change(apply, expected_version)

    def block(self, lease_id, code, message, *, expected_version):
        """Record why a decided saga could not advance; the host pump retries."""
        require(text(code, 80) and text(message, 300), "blocked reason")
        def apply(state):
            row = state["reviews"].get(lease_id)
            require(row is not None and row["state"] in ("awaiting_capacity", "deciding"),
                    "review is not blocked in progress")
            if row["blocked"] and row["blocked"]["code"] == code and row["blocked"]["message"] == message:
                return row
            row.update(blocked={"code": code, "message": message, "at": time.time()},
                       updated_at=time.time())
            return row
        return self._change(apply, expected_version)

    def mark(self, lease_id, stage, *, expected_version, state_value=None):
        require(stage in STAGES and (state_value is None or state_value in STATES), "finalization stage")
        def apply(state):
            row = state["reviews"].get(lease_id)
            require(row is not None and row["decision"] is not None, "unknown review decision")
            index = STAGES.index(stage)
            require(all(row["stages"][name] for name in STAGES[:index]), "finalization stage order")
            row["stages"][stage] = True
            row["blocked"] = None
            if row["state"] == "awaiting_capacity":
                row["state"] = "deciding"
            if state_value is not None:
                row["state"] = state_value
            row["updated_at"] = time.time()
            return row
        return self._change(apply, expected_version)

    def wake_prepare(self, lease_id, *, expected_version):
        def apply(state):
            row = state["reviews"].get(lease_id)
            require(row is not None and row["decision"] is None and row["state"] == "pending",
                    "only an undecided review may wake main")
            require(row["wake"]["state"] in ("pending", "failed"), "review wake already submitted")
            attempt = uuid.uuid4().hex
            row["wake"] = {"state": "submitting", "attempt_id": attempt,
                           "attempts": row["wake"]["attempts"] + 1, "updated_at": time.time()}
            row["updated_at"] = time.time()
            return copy.deepcopy(row["wake"])
        return self._change(apply, expected_version)

    def wake_record(self, lease_id, attempt_id, state_value, *, expected_version):
        require(state_value in ("appended", "queued", "uncertain", "failed")
                and text(attempt_id, 200), "review wake outcome")
        def apply(state):
            row = state["reviews"].get(lease_id)
            require(row is not None and row["decision"] is None, "review already decided")
            require(row["wake"]["state"] == "submitting"
                    and row["wake"]["attempt_id"] == attempt_id, "stale review wake attempt")
            row["wake"].update(state=state_value, updated_at=time.time())
            row["updated_at"] = time.time()
            return copy.deepcopy(row["wake"])
        return self._change(apply, expected_version)

    @staticmethod
    def _rework_row(state, lease_id):
        return next((value for value in state["reviews"].values()
                     if value.get("replacement_lease_id") == lease_id), None)

    @staticmethod
    def _resolve(row, kind, dispatch_name, observer_nonce, detail):
        """Turn an unresolved intent into a denial that permits a fresh token."""
        launch = row["launch"]
        stamp = time.time()
        launch.update(state="spawn_denied", agent_id=None, proof=detail[:500], manual_from=None,
                      launch_ref=None, updated_at=stamp)
        launch["audit"] = (launch["audit"] + [{
            "kind": kind, "dispatch_name": dispatch_name, "attempt": launch["attempt"],
            "observer_nonce": observer_nonce, "at": stamp, "detail": detail[:500]}])[-MAX_LAUNCH_AUDIT:]
        row["updated_at"] = stamp
        return copy.deepcopy(launch)

    def launch_intent(self, lease_id, dispatch_name, generation, *, host_nonce, expected_version):
        require(text(dispatch_name, 200) and integer(generation, 2), "rework launch intent")
        # A new intent always names its host process. Only intents written before
        # nonces existed (migrated) may lack one, and those count as unknown.
        require(text(host_nonce, 200), "rework launch intent requires the host process nonce")
        def apply(state):
            row = self._rework_row(state, lease_id)
            require(row is not None and row["decision"] == "rework" and row["state"] == "rework_ready",
                    "rework is not launch-ready")
            launch = row["launch"]
            if launch["state"] == "launch_intent":
                require(launch["dispatch_name"] == dispatch_name and launch["generation"] == generation
                        and launch["host_nonce"] == host_nonce, "another rework launch is unresolved")
                return copy.deepcopy(launch)
            require(launch["state"] in ("none", "spawn_denied"), "rework launch outcome unresolved")
            launch.update(state="launch_intent", dispatch_name=dispatch_name, attempt=launch["attempt"] + 1,
                          generation=generation, agent_id=None, proof=None, host_nonce=host_nonce,
                          manual_from=None, launch_ref=new_launch_ref(), updated_at=time.time())
            row["updated_at"] = time.time()
            return copy.deepcopy(launch)
        return self._change(apply, expected_version)

    def launch_denied(self, lease_id, dispatch_name, proof, *, expected_version):
        require(text(dispatch_name, 200) and text(proof, 500), "spawn denial proof")
        def apply(state):
            row = self._rework_row(state, lease_id)
            require(row is not None and row["launch"]["state"] == "launch_intent"
                    and row["launch"]["dispatch_name"] == dispatch_name, "stale rework launch denial")
            return self._resolve(row, "denied", dispatch_name, None, proof)
        return self._change(apply, expected_version)

    @staticmethod
    def _absent_agent_matches(launch, absent_agent_id):
        """An observed launch is absent only when its exact agent id is absent too."""
        observed = launch["state"] == "spawn_observed" or (
            launch["state"] == "manual_requested" and launch["manual_from"] == "spawn_observed")
        require(not observed or (absent_agent_id is not None and absent_agent_id == launch["agent_id"]),
                "host must assert the exact observed agent absent from its agent list")

    def launch_absent(self, lease_id, dispatch_name, observer_nonce, *, absent_agent_id=None,
                      expected_version):
        """A later host process proves the exact launch name absent from its list.

        Only an intent recorded by another (or an unknown, pre-nonce) process can
        be resolved this way: its own process can no longer complete the spawn.
        """
        require(text(dispatch_name, 200) and text(observer_nonce, 200), "launch absence observation")
        def apply(state):
            row = self._rework_row(state, lease_id)
            require(row is not None and row["decision"] == "rework", "unknown rework launch")
            launch = row["launch"]
            last = launch["audit"][-1] if launch["audit"] else None
            if (launch["state"] == "spawn_denied" and launch["dispatch_name"] == dispatch_name
                    and last is not None and last["kind"] == "absent"
                    and last["observer_nonce"] == observer_nonce):
                return copy.deepcopy(launch)
            require(launch["state"] in ("launch_intent", "spawn_observed"),
                    "rework launch is not unresolved or observed-unbound")
            require(launch["dispatch_name"] == dispatch_name, "stale rework launch name")
            require(launch["host_nonce"] is None or launch["host_nonce"] != observer_nonce,
                    "launch intent belongs to this host process; use the user-confirmed resolution")
            self._absent_agent_matches(launch, absent_agent_id)
            return self._resolve(row, "absent", dispatch_name, observer_nonce,
                                 "exact launch name absent from a later host process's agent list")
        return self._change(apply, expected_version)

    def launch_manual_request(self, scope, review_lease_id, launch_ref, *, confirmed, expected_version):
        """Record the person's request for one exact launch; it never asserts absence."""
        require(text(scope, 200) and text(review_lease_id, 200), "manual launch request")
        require(launch_ref_valid(launch_ref), "exact rework launch reference required")
        require(confirmed is True, "manual launch request requires the person's explicit confirmation")
        def apply(state):
            row = state["reviews"].get(review_lease_id)
            require(row is not None and row["scope"] == scope and row["decision"] == "rework",
                    "unknown rework launch")
            launch = row["launch"]
            require(launch["state"] in UNRESOLVED_LAUNCH_STATES,
                    "rework launch is not unresolved or observed-unbound")
            require(launch["launch_ref"] == launch_ref, "stale rework launch reference")
            if launch["state"] == "manual_requested":
                return copy.deepcopy(launch)
            stamp = time.time()
            prior = launch["state"]
            launch.update(state="manual_requested", manual_from=prior,
                          proof="user confirmed a host absence check is required", updated_at=stamp)
            launch["audit"] = (launch["audit"] + [{
                "kind": "manual_requested", "dispatch_name": launch["dispatch_name"],
                "attempt": launch["attempt"], "observer_nonce": None, "at": stamp,
                "detail": "user requested resolution after a host agent-list check"}])[-MAX_LAUNCH_AUDIT:]
            row["updated_at"] = stamp
            return copy.deepcopy(launch)
        return self._change(apply, expected_version)

    def launch_manual(self, lease_id, dispatch_name, observer_nonce, *, absent_agent_id=None,
                      launch_ref, expected_version):
        """Host checked its agent list and found a user-requested launch absent."""
        require(text(dispatch_name, 200) and (observer_nonce is None or text(observer_nonce, 200)),
                "manual launch resolution")
        def apply(state):
            row = self._rework_row(state, lease_id)
            require(row is not None and row["decision"] == "rework", "unknown rework launch")
            launch = row["launch"]
            last = launch["audit"][-1] if launch["audit"] else None
            if (launch["state"] == "spawn_denied" and launch["dispatch_name"] == dispatch_name
                    and last is not None and last["kind"] == "manual"):
                return copy.deepcopy(launch)
            require(launch["state"] == "manual_requested", "manual launch resolution was not requested")
            require(launch["dispatch_name"] == dispatch_name, "stale rework launch name")
            require(launch["launch_ref"] == launch_ref, "stale rework launch reference")
            self._absent_agent_matches(launch, absent_agent_id)
            return self._resolve(row, "manual", dispatch_name, observer_nonce,
                                 "host confirmed the user-requested launch absent from its agent list")
        return self._change(apply, expected_version)

    def launch_present(self, lease_id, dispatch_name, agent_id, *, launch_ref, expected_version):
        """Host checked a manual request and found the exact launch still present."""
        require(text(dispatch_name, 200) and text(agent_id, 200), "present launch observation")
        def apply(state):
            row = self._rework_row(state, lease_id)
            require(row is not None and row["decision"] == "rework", "unknown rework launch")
            launch = row["launch"]
            require(launch["state"] == "manual_requested" and launch["dispatch_name"] == dispatch_name,
                    "manual launch resolution was not requested for this name")
            require(launch["launch_ref"] == launch_ref, "stale rework launch reference")
            if launch["manual_from"] == "spawn_observed":
                require(launch["agent_id"] == agent_id, "present launch agent changed")
            stamp = time.time()
            launch.update(state="spawn_observed", agent_id=agent_id, proof="agent present",
                          manual_from=None, launch_ref=new_launch_ref(), updated_at=stamp)
            launch["audit"] = (launch["audit"] + [{
                "kind": "present", "dispatch_name": dispatch_name, "attempt": launch["attempt"],
                "observer_nonce": None, "at": stamp,
                "detail": "host found the exact launch present in its agent list"}])[-MAX_LAUNCH_AUDIT:]
            row["updated_at"] = stamp
            return copy.deepcopy(launch)
        return self._change(apply, expected_version)

    def spawn_observed(self, lease_id, dispatch_name, agent_id, *, expected_version):
        require(text(dispatch_name, 200) and text(agent_id, 200), "observed rework spawn")
        def apply(state):
            row = next((value for value in state["reviews"].values()
                        if value.get("replacement_lease_id") == lease_id), None)
            launch = row["launch"] if row is not None else None
            require(launch is not None and launch["dispatch_name"] == dispatch_name
                    and launch["state"] in ("launch_intent", "spawn_observed")
                    and launch["agent_id"] in (None, agent_id), "stale rework spawn observation")
            if launch["state"] == "spawn_observed":
                return copy.deepcopy(launch)
            launch.update(state="spawn_observed", agent_id=agent_id, launch_ref=new_launch_ref(),
                          updated_at=time.time())
            row["updated_at"] = time.time()
            return copy.deepcopy(launch)
        return self._change(apply, expected_version)

    def launch_bound(self, replacement_lease_id, agent_id, agent_name=None, *, expected_version):
        """Bind is itself a positive host observation of the exact named agent."""
        require(text(agent_id, 200), "bound agent")
        def apply(state):
            row = next((value for value in state["reviews"].values()
                        if value.get("replacement_lease_id") == replacement_lease_id), None)
            require(row is not None and row["decision"] == "rework", "unknown rework launch")
            launch = row["launch"]
            if launch["state"] == "bound":
                require(launch["agent_id"] == agent_id, "rework launch bound to another agent")
                return copy.deepcopy(launch)
            # A person's resolution request for a launch whose ledger bind already
            # committed is settled by that bind: it must match the exact launch
            # the request was made for. The request's audit row stays as history.
            source = launch["manual_from"] if launch["state"] == "manual_requested" else launch["state"]
            observed = source == "spawn_observed" and launch["agent_id"] == agent_id
            named = (source == "launch_intent" and agent_name is not None
                     and launch["dispatch_name"] == agent_name)
            require(observed or named, "rework launch was not observed for this agent")
            launch.update(state="bound", agent_id=agent_id, launch_ref=None, manual_from=None,
                          updated_at=time.time())
            row["updated_at"] = time.time()
            return copy.deepcopy(launch)
        return self._change(apply, expected_version)

    def launch_status(self, scope, observer_nonce=None, *, limit=64):
        """Host-only recovery view of every unbound rework launch in one scope.

        The stored nonce is compared, never returned.
        """
        require(text(scope, 200) and integer(limit, 1, 64), "launch status")
        require(observer_nonce is None or text(observer_nonce, 200), "launch host nonce")
        state = self.read()
        rows = []
        for row in state["reviews"].values():
            launch = row["launch"]
            if (row["scope"] != scope or row["decision"] != "rework" or row["state"] != "rework_ready"
                    or launch["state"] == "bound"):
                continue
            intent_nonce = launch["host_nonce"]
            rows.append({
                "review_lease_id": row["lease_id"], "lease_id": row["replacement_lease_id"],
                "item_id": row["item_id"], "generation": row["replacement_generation"],
                "dispatch_name": launch["dispatch_name"], "state": launch["state"],
                "attempt": launch["attempt"], "launch_ref": launch["launch_ref"],
                "manual_requested": launch["state"] == "manual_requested",
                "manual_from": launch["manual_from"], "intent_nonce_present": intent_nonce is not None,
                "same_process": (None if intent_nonce is None or observer_nonce is None
                                 else intent_nonce == observer_nonce),
                "observed_agent_id": launch["agent_id"], "updated_at": launch["updated_at"]})
        rows.sort(key=lambda value: (value["updated_at"], value["lease_id"]))
        return {"finalization_version": state["version"], "launches": rows[:limit],
                "truncated": len(rows) > limit}

    def projection(self, scope, *, limit=MAX_PROJECTION):
        require(text(scope, 200) and integer(limit, 1, MAX_PROJECTION), "finalization projection")
        state = self.read()
        reviews = state["reviews"]

        def superseded(row):
            # A completed rework (rework_ready, every stage done, not blocked) is
            # live only until its exact replacement generation publishes its own
            # review row, in any state: that row then carries the item. Until then
            # the predecessor is the host's rework launch row. awaiting_capacity
            # and deciding are never hidden: the host pump rolls those forward.
            # Any mismatch keeps the row visible.
            if (row["decision"] != "rework" or row["state"] != "rework_ready"
                    or row["blocked"] is not None or not all(row["stages"].values())):
                return False
            replacement = reviews.get(row["replacement_lease_id"])
            return (replacement is not None
                    and replacement["lease_id"] == row["replacement_lease_id"]
                    and replacement["scope"] == row["scope"]
                    and replacement["item_id"] == row["item_id"]
                    and replacement["generation"] == row["replacement_generation"])

        rows = [copy.deepcopy(row) for row in reviews.values()
                if row["scope"] == scope and row["state"] in (
                    "pending", "awaiting_capacity", "deciding", "rework_ready")
                and not superseded(row)]
        rows.sort(key=lambda row: (row["updated_at"], row["lease_id"]), reverse=True)
        projected = []
        for row in rows[:limit]:
            projected.append({key: copy.deepcopy(row[key]) for key in (
                "lease_id", "item_id", "root_id", "priority", "artifact", "completion_receipt",
                "fingerprint", "summary", "coverage", "verification", "workspace", "state",
                "decision_id", "decision", "evidence", "replacement_lease_id",
                "replacement_generation", "stages", "wake", "launch", "blocked")})
        return {"version": state["version"], "pending": projected,
                "truncated": len(rows) > limit}


def finalization_store(store):
    return WorkParallelFinalizationStore(Path(store.path).with_name("work-parallel-finalization.json"))
