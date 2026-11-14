"""Versioned durable stranded-result journal ignored by builds through 57dbfb3.

A stranded record is not a completion review: its retained worktree could not
be verified, or its worker ended without reporting. Keeping these rows out of
schema-5 finalization means an old process neither presents an undecidable main
review nor loops its Stop hook. The legacy ledger remains authoritative for
execution state; this store contains no worker capability or token.
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
from .work_parallel import (WorkParallelConflict, WorkParallelStateUnavailable, digest,
                            integer, number, require, text, unique_object)
from .work_parallel_finalization import (LAUNCH_AUDIT_FIELDS, LAUNCH_AUDIT_KINDS,
                                         LAUNCH_STATES, MAX_LAUNCH_AUDIT,
                                         UNRESOLVED_LAUNCH_STATES, WAKE_STATES,
                                         canonical_digest, launch_ref_valid,
                                         new_launch_ref)

SCHEMA = 1
MAX_BYTES = 2_097_152
MAX_ROWS = 2_000
MAX_PROJECTION = 24
STATES = ("intent", "pending", "deciding", "recovered", "superseded", "done")
MAX_KNOWN_LEASE_IDS = 128
DECISIONS = (None, "rework")
STAGES = ("reserved", "workspace", "coordination", "retired")
KINDS = ("workspace_unavailable", "no_report")


def review_pin(row):
    """rp1 semantic pin of a stranded review; wake, launch and time are bookkeeping.

    The same exclusions as schema-5 finalization: an undecided stranded launch
    is state=none by the validator, wake is delivery state, and updated_at moves
    with that delivery. Every result, assignment, binding, supersession and
    tombstone fact stays in the digest.
    """
    from .work_parallel_finalization import REVIEW_PIN_PREFIX, canonical_digest
    require(type(row) is dict, "stranded review pin row")
    semantic = {key: copy.deepcopy(value) for key, value in row.items()
                if key not in ("wake", "launch", "updated_at")}
    return REVIEW_PIN_PREFIX + canonical_digest(semantic)


def empty_state():
    return {"schema": SCHEMA, "version": 0, "rows": {}}


def _binding(value):
    require(type(value) is dict and set(value) == {"repo", "path", "branch", "head"}
            and all(text(value[key], 2_000) for key in value), "stranded binding identity")


def _wake(value, decision):
    require(type(value) is dict and set(value) == {"state", "attempt_id", "attempts", "updated_at"},
            "stranded wake fields")
    require(value["state"] in WAKE_STATES and integer(value["attempts"], 0, 100)
            and number(value["updated_at"], 1e15), "stranded wake values")
    require(value["attempt_id"] is None or text(value["attempt_id"], 200), "stranded wake attempt")
    require((value["state"] in ("pending", "acknowledged")) == (value["attempt_id"] is None),
            "stranded wake attempt state")
    require((decision is None) == (value["state"] != "acknowledged"), "stranded wake decision")


def _launch_audit(rows):
    require(type(rows) is list and len(rows) <= MAX_LAUNCH_AUDIT, "stranded launch audit bound")
    for row in rows:
        require(type(row) is dict and set(row) == LAUNCH_AUDIT_FIELDS
                and row["kind"] in LAUNCH_AUDIT_KINDS and text(row["dispatch_name"], 200)
                and integer(row["attempt"], 1, 100)
                and (row["observer_nonce"] is None or text(row["observer_nonce"], 200))
                and number(row["at"], 1e15) and text(row["detail"], 500),
                "stranded launch audit row")


def _launch(value, decision, replacement_generation):
    require(type(value) is dict and set(value) == {
        "state", "dispatch_name", "attempt", "generation", "agent_id", "proof", "host_nonce",
        "manual_from", "launch_ref", "audit", "updated_at"}, "stranded launch fields")
    require(value["state"] in LAUNCH_STATES and integer(value["attempt"], 0, 100)
            and number(value["updated_at"], 1e15), "stranded launch values")
    require(value["dispatch_name"] is None or text(value["dispatch_name"], 200), "stranded dispatch name")
    require(value["generation"] is None or integer(value["generation"], 2), "stranded launch generation")
    require(value["agent_id"] is None or text(value["agent_id"], 200), "stranded launch agent")
    require(value["proof"] is None or text(value["proof"], 500), "stranded launch proof")
    require(value["host_nonce"] is None or text(value["host_nonce"], 200), "stranded launch host nonce")
    require(value["manual_from"] is None or value["manual_from"] in ("launch_intent", "spawn_observed"),
            "stranded launch manual source")
    require((value["launch_ref"] is not None) == (value["state"] in UNRESOLVED_LAUNCH_STATES)
            and (value["launch_ref"] is None or launch_ref_valid(value["launch_ref"])),
            "stranded launch reference")
    _launch_audit(value["audit"])
    if decision is None:
        require(value["state"] == "none" and value["attempt"] == 0 and value["audit"] == []
                and all(value[key] is None for key in ("dispatch_name", "generation", "agent_id", "proof",
                                                       "host_nonce", "manual_from", "launch_ref")),
                "undecided stranded launch")
        return
    require(value["generation"] in (None, replacement_generation), "stranded launch generation changed")
    if value["state"] == "none":
        require(value["dispatch_name"] is None and value["attempt"] == 0 and value["agent_id"] is None
                and value["proof"] is None and value["host_nonce"] is None
                and value["manual_from"] is None and value["launch_ref"] is None and value["audit"] == [],
                "empty stranded launch")
    elif value["state"] == "spawn_denied":
        require(value["dispatch_name"] is not None and value["attempt"] >= 1 and value["agent_id"] is None
                and value["proof"] is not None and value["manual_from"] is None and value["launch_ref"] is None,
                "stranded spawn denial")
    elif value["state"] == "launch_intent":
        require(value["dispatch_name"] is not None and value["attempt"] >= 1 and value["agent_id"] is None
                and value["proof"] is None and value["manual_from"] is None, "stranded launch intent")
    elif value["state"] == "manual_requested":
        require(value["dispatch_name"] is not None and value["attempt"] >= 1
                and value["manual_from"] in ("launch_intent", "spawn_observed"), "stranded manual request")
    else:
        require(value["dispatch_name"] is not None and value["attempt"] >= 1 and value["agent_id"] is not None
                and value["manual_from"] is None, "stranded observed launch")


def _row(key, row):
    fields = {"lease_id", "scope", "item_id", "root_id", "priority", "plan_version", "generation",
              "kind", "reason", "artifact", "completion_receipt", "fingerprint", "summary", "coverage",
              "verification", "binding", "source_digest", "known_lease_ids", "capture_ledger_version",
              "state", "decision_id", "decision_digest", "evidence",
              "replacement_lease_id", "replacement_generation", "stages", "wake", "launch", "blocked",
              "superseded", "legacy_review_removed", "updated_at"}
    require(type(row) is dict and set(row) == fields and key == row["lease_id"], "stranded row fields")
    require(all(text(row[k], 200) for k in ("lease_id", "scope", "item_id", "root_id"))
            and row["priority"] in ("P1", "P2", "P3", "P4", "P5")
            and integer(row["plan_version"]) and integer(row["generation"], 1), "stranded assignment")
    require(row["kind"] in KINDS and text(row["reason"], 300), "stranded kind/reason")
    if row["kind"] == "no_report":
        # No artifact exists; the receipt is the durable host-salvage receipt.
        require(row["artifact"] is None and digest(row["completion_receipt"]), "no-report facts")
    else:
        require(digest(row["artifact"]) and digest(row["completion_receipt"]), "stranded receipt digests")
    require(digest(row["fingerprint"]), "stranded fingerprint")
    require(text(row["summary"], 2_000) and text(row["verification"], 2_000), "stranded receipt text")
    require(type(row["coverage"]) is list and len(row["coverage"]) <= 100, "stranded coverage")
    for entry in row["coverage"]:
        require(type(entry) is dict and set(entry) <= {"item_id", "verified", "evidence_digest"}
                and {"item_id", "verified"} <= set(entry) and text(entry["item_id"], 200)
                and type(entry["verified"]) is bool
                and ("evidence_digest" not in entry or digest(entry["evidence_digest"])),
                "stranded coverage entry")
    _binding(row["binding"])
    require(digest(row["source_digest"]), "stranded source digest")
    require(type(row["known_lease_ids"]) is list and len(row["known_lease_ids"]) <= MAX_KNOWN_LEASE_IDS
            and len(set(row["known_lease_ids"])) == len(row["known_lease_ids"])
            and all(text(value, 200) for value in row["known_lease_ids"]), "known lease ids")
    require(row["lease_id"] in row["known_lease_ids"] and integer(row["capture_ledger_version"]),
            "stranded intent capture")
    require(row["state"] in STATES, "stranded state")
    require(row["decision_id"] is None or text(row["decision_id"], 200), "stranded decision id")
    require(row["decision_digest"] is None or digest(row["decision_digest"]), "stranded decision digest")
    require(row["evidence"] is None or text(row["evidence"], 2_000), "stranded evidence")
    require(row["replacement_lease_id"] is None or text(row["replacement_lease_id"], 200),
            "stranded replacement")
    require(row["replacement_generation"] is None or integer(row["replacement_generation"], 2),
            "stranded replacement generation")
    require(type(row["stages"]) is dict and set(row["stages"]) == set(STAGES)
            and all(type(value) is bool for value in row["stages"].values()), "stranded stages")
    values = [row["stages"][stage] for stage in STAGES]
    require(values == sorted(values, reverse=True), "stranded stage order")
    blocked = row["blocked"]
    require(blocked is None or (type(blocked) is dict and set(blocked) == {"code", "message", "at"}
            and text(blocked["code"], 80) and text(blocked["message"], 300)
            and number(blocked["at"], 1e15)), "stranded blocked")
    superseded = row["superseded"]
    require(superseded is None or (type(superseded) is dict
            and set(superseded) == {"lease_id", "generation", "observed_at", "digest"}
            and text(superseded["lease_id"], 200) and integer(superseded["generation"], 1)
            and number(superseded["observed_at"], 1e15) and digest(superseded["digest"])),
            "stranded superseded outcome")
    tombstone = row["legacy_review_removed"]
    require(tombstone is None or (type(tombstone) is dict
            and set(tombstone) == {"state", "row_digest", "source_digest", "old_version", "removed_at"}
            and tombstone["state"] in ("prepared", "removed")
            and digest(tombstone["row_digest"]) and digest(tombstone["source_digest"])
            and integer(tombstone["old_version"]) and number(tombstone["removed_at"], 1e15)),
            "legacy review tombstone")
    require(number(row["updated_at"], 1e15), "stranded updated at")
    decision = None if row["decision_id"] is None else "rework"
    require((decision is None) == (row["decision_digest"] is None and row["evidence"] is None),
            "stranded decision body")
    if row["state"] == "intent":
        require(row["kind"] == "no_report" and superseded is None and row["decision_id"] is None
                and row["replacement_lease_id"] is None and not any(values) and row["blocked"] is None,
                "stranded intent invariant")
    elif row["state"] == "superseded":
        # Undecided, or decided but never launched: another execution owns the
        # item, and an unbound recovery reservation (if any) was cancelled.
        require(superseded is not None and row["blocked"] is None
                and (row["replacement_lease_id"] is None) == (decision is None)
                and (decision is None or row["replacement_generation"] == row["generation"] + 1)
                and (decision is not None or not any(values)), "superseded stranded invariant")
        # Terminal: it neither wakes main nor owns a launch.
        require(row["wake"]["state"] == "acknowledged" and row["wake"]["attempt_id"] is None,
                "superseded wake terminal")
        require(row["launch"]["state"] == "none", "superseded launch terminal")
    elif decision is None:
        require(superseded is None and row["state"] in ("pending", "done")
                and row["replacement_lease_id"] is None
                and row["replacement_generation"] is None and not any(values), "pending stranded invariant")
    else:
        require(superseded is None and row["replacement_lease_id"] is not None
                and row["replacement_generation"] == row["generation"] + 1
                and row["state"] in ("deciding", "recovered", "done"), "decided stranded invariant")
        require((row["state"] == "recovered") <= all(values), "stranded recovered invariant")
    if row["state"] != "superseded":
        _wake(row["wake"], decision)
        _launch(row["launch"], decision, row["replacement_generation"])


def validate(state):
    require(type(state) is dict and set(state) == {"schema", "version", "rows"}, "stranded fields")
    require(state["schema"] == SCHEMA and integer(state["version"]), "stranded schema/version")
    require(type(state["rows"]) is dict and len(state["rows"]) <= MAX_ROWS, "stranded row bound")
    for key, row in state["rows"].items():
        _row(key, row)


class WorkParallelStrandedStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self._lock = threading.RLock()

    def _read(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return empty_state()
        if len(raw) > MAX_BYTES:
            raise WorkParallelStateUnavailable("stranded byte limit")
        try:
            state = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
            validate(state)
            return state
        except (UnicodeDecodeError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
            raise WorkParallelStateUnavailable("corrupt Work stranded state; writes blocked") from error

    def read(self):
        if not self.path.exists():
            return empty_state()
        with self._lock, exclusive_file_lock(self.path):
            return self._read()

    def _change(self, fn, expected_version):
        require(integer(expected_version), "expected_stranded_version required")
        with self._lock, exclusive_file_lock(self.path):
            before = self._read()
            if before["version"] != expected_version:
                raise WorkParallelConflict("Work stranded state changed; reread")
            state = copy.deepcopy(before)
            result = fn(state)
            if state != before:
                state["version"] += 1
                validate(state)
                raw = json.dumps(state, ensure_ascii=False, allow_nan=False,
                                 sort_keys=True, separators=(",", ":")).encode("utf-8")
                require(len(raw) <= MAX_BYTES, "stranded byte limit")
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

    @staticmethod
    def _new_row(facts):
        stamp = time.time()
        facts = copy.deepcopy(facts)
        source_digest = canonical_digest(facts)
        known = facts.pop("known_lease_ids")
        captured = facts.pop("capture_ledger_version")
        return {**facts, "source_digest": source_digest, "known_lease_ids": known,
                "capture_ledger_version": captured,
                "state": "intent" if facts["kind"] == "no_report" else "pending", "decision_id": None,
                "decision_digest": None, "evidence": None, "replacement_lease_id": None,
                "replacement_generation": None, "stages": {key: False for key in STAGES},
                "wake": {"state": "pending", "attempt_id": None, "attempts": 0, "updated_at": stamp},
                "launch": {"state": "none", "dispatch_name": None, "attempt": 0, "generation": None,
                           "agent_id": None, "proof": None, "host_nonce": None, "manual_from": None,
                           "launch_ref": None, "audit": [], "updated_at": stamp},
                "blocked": None, "superseded": None, "legacy_review_removed": None,
                "updated_at": stamp}

    def pending(self, facts, *, expected_version):
        row = self._new_row(facts)
        _row(row.get("lease_id"), row)
        def apply(state):
            prior = state["rows"].get(row["lease_id"])
            if prior is not None:
                immutable = ("scope", "item_id", "root_id", "priority", "plan_version", "generation",
                             "kind", "reason", "artifact", "completion_receipt", "fingerprint", "summary",
                             "coverage", "verification", "binding", "source_digest",
                             "known_lease_ids", "capture_ledger_version")
                require(all(prior[key] == row[key] for key in immutable), "stranded facts changed")
                return prior
            require(len(state["rows"]) < MAX_ROWS, "stranded row bound")
            state["rows"][row["lease_id"]] = row
            return row
        return self._change(apply, expected_version)

    def refresh_intent(self, lease_id, known_lease_ids, capture_ledger_version, *, expected_version):
        require(type(known_lease_ids) is list and len(known_lease_ids) <= MAX_KNOWN_LEASE_IDS
                and len(set(known_lease_ids)) == len(known_lease_ids)
                and all(text(value, 200) for value in known_lease_ids)
                and integer(capture_ledger_version), "stranded intent refresh")
        def apply(state):
            row = state["rows"].get(lease_id)
            require(row is not None and row["state"] == "intent", "unknown stranded intent")
            require(lease_id in known_lease_ids, "stranded lease missing from intent")
            row["known_lease_ids"] = copy.deepcopy(known_lease_ids)
            row["capture_ledger_version"] = capture_ledger_version
            row["updated_at"] = time.time()
            return row
        return self._change(apply, expected_version)

    def activate(self, lease_id, *, expected_version):
        def apply(state):
            row = state["rows"].get(lease_id)
            require(row is not None, "unknown stranded intent")
            if row["state"] == "pending":
                return row
            require(row["state"] == "intent", "stranded intent cannot activate")
            row["state"] = "pending"
            row["updated_at"] = time.time()
            return row
        return self._change(apply, expected_version)

    def decide(self, lease_id, decision_id, decision_body_digest, evidence, replacement_lease_id,
               *, expected_version, expected_review_pin=None):
        require(text(lease_id, 200) and text(decision_id, 200) and digest(decision_body_digest)
                and text(evidence, 2_000) and text(replacement_lease_id, 200), "stranded decision")
        def apply(state):
            row = state["rows"].get(lease_id)
            require(row is not None, "stranded result is not pending main review")
            if row["decision_id"] is None and expected_review_pin is not None:
                from .work_parallel_finalization import review_pin_valid
                require(review_pin_valid(expected_review_pin) and review_pin(row) == expected_review_pin,
                        "review changed; reread")
            if row["decision_id"] is not None:
                require(row["decision_id"] == decision_id and row["decision_digest"] == decision_body_digest,
                        "stranded review already decided differently")
                return row
            row.update(state="deciding", decision_id=decision_id, decision_digest=decision_body_digest,
                       evidence=evidence, replacement_lease_id=replacement_lease_id,
                       replacement_generation=row["generation"] + 1, blocked=None,
                       wake={"state": "acknowledged", "attempt_id": None,
                             "attempts": row["wake"]["attempts"], "updated_at": time.time()},
                       updated_at=time.time())
            return row
        return self._change(apply, expected_version)

    def mark(self, lease_id, stage, *, expected_version, state_value=None):
        require(stage in STAGES and (state_value is None or state_value in STATES), "stranded stage")
        def apply(state):
            row = state["rows"].get(lease_id)
            require(row is not None and row["decision_id"] is not None, "unknown stranded decision")
            index = STAGES.index(stage)
            require(all(row["stages"][name] for name in STAGES[:index]), "stranded stage order")
            row["stages"][stage] = True
            row["blocked"] = None
            if state_value is not None:
                row["state"] = state_value
            row["updated_at"] = time.time()
            return row
        return self._change(apply, expected_version)

    def block(self, lease_id, code, message, *, expected_version):
        require(text(code, 80) and text(message, 300), "stranded blocked reason")
        def apply(state):
            row = state["rows"].get(lease_id)
            require(row is not None and row["decision_id"] is not None, "unknown stranded decision")
            row.update(state="deciding", blocked={"code": code, "message": message, "at": time.time()},
                       updated_at=time.time())
            return row
        return self._change(apply, expected_version)

    def wake_prepare(self, lease_id, *, expected_version):
        def apply(state):
            row = state["rows"].get(lease_id)
            require(row is not None and row["decision_id"] is None and row["state"] == "pending",
                    "only an undecided stranded row may wake main")
            require(row["wake"]["state"] in ("pending", "failed"), "stranded wake already submitted")
            attempt = uuid.uuid4().hex
            row["wake"] = {"state": "submitting", "attempt_id": attempt,
                           "attempts": row["wake"]["attempts"] + 1, "updated_at": time.time()}
            row["updated_at"] = time.time()
            return row["wake"]
        return self._change(apply, expected_version)

    def wake_record(self, lease_id, attempt_id, state_value, *, expected_version):
        require(state_value in ("appended", "queued", "uncertain", "failed")
                and text(attempt_id, 200), "stranded wake outcome")
        def apply(state):
            row = state["rows"].get(lease_id)
            require(row is not None and row["decision_id"] is None, "stranded row already decided")
            require(row["wake"]["state"] == "submitting"
                    and row["wake"]["attempt_id"] == attempt_id, "stale stranded wake attempt")
            row["wake"].update(state=state_value, updated_at=time.time())
            row["updated_at"] = time.time()
            return row["wake"]
        return self._change(apply, expected_version)

    def done(self, lease_id, *, expected_version):
        def apply(state):
            row = state["rows"].get(lease_id)
            require(row is not None, "unknown stranded row")
            if row["state"] != "done":
                row["state"] = "done"
                row["blocked"] = None
                row["updated_at"] = time.time()
            return row
        return self._change(apply, expected_version)

    def supersede(self, lease_id, superseding_lease_id, superseding_generation, superseding_digest,
                  *, expected_version):
        """Terminal outcome: another execution owns this item; never recovered later."""
        require(text(lease_id, 200) and text(superseding_lease_id, 200)
                and integer(superseding_generation, 1) and digest(superseding_digest), "stranded supersede")
        def apply(state):
            row = state["rows"].get(lease_id)
            require(row is not None, "unknown stranded row")
            outcome = row["superseded"]
            if outcome is not None:
                require(outcome["lease_id"] == superseding_lease_id
                        and outcome["digest"] == superseding_digest, "stranded row superseded differently")
                return row
            require(row["state"] in ("intent", "pending", "deciding") and row["launch"]["state"] == "none",
                    "only a stranded row that never launched recovery may be superseded")
            stamp = time.time()
            row.update(state="superseded", blocked=None, updated_at=stamp,
                       launch={**row["launch"], "updated_at": stamp},
                       superseded={"lease_id": superseding_lease_id, "generation": superseding_generation,
                                   "observed_at": stamp, "digest": superseding_digest},
                       wake={"state": "acknowledged", "attempt_id": None,
                             "attempts": row["wake"]["attempts"], "updated_at": stamp})
            return row
        return self._change(apply, expected_version)

    def legacy_review(self, lease_id, step, *, row_digest, source_digest, old_version, expected_version):
        """Exact audit of removing an undecided schema-5 row this stranded row replaces.

        ``prepared`` is written before the schema-5 delete and binds the exact row
        digest; ``removed`` completes it after the delete. A different digest for
        the same row is a conflict, never an overwrite.
        """
        require(step in ("prepared", "removed") and digest(row_digest) and digest(source_digest)
                and integer(old_version), "legacy review tombstone")
        def apply(state):
            row = state["rows"].get(lease_id)
            require(row is not None and row["kind"] == "workspace_unavailable", "unknown stranded row")
            prior = row["legacy_review_removed"]
            if prior is not None:
                require(prior["row_digest"] == row_digest and prior["source_digest"] == source_digest
                        and prior["old_version"] == old_version, "legacy review tombstone changed")
                if prior["state"] == "removed" or step == "prepared":
                    return row
            else:
                require(step == "prepared", "legacy review removal was not prepared")
            row["legacy_review_removed"] = {"state": step, "row_digest": row_digest,
                                            "source_digest": source_digest, "old_version": old_version,
                                            "removed_at": time.time()}
            row["updated_at"] = time.time()
            return row
        return self._change(apply, expected_version)

    def projection(self, scope, *, limit=MAX_PROJECTION):
        require(text(scope, 200) and integer(limit, 1, MAX_PROJECTION), "stranded projection")
        state = self.read()
        rows = [copy.deepcopy(row) for row in state["rows"].values()
                if row["scope"] == scope and row["state"] != "done"]
        rows.sort(key=lambda row: (row["updated_at"], row["lease_id"]), reverse=True)
        return {"version": state["version"], "pending": rows[:limit], "truncated": len(rows) > limit}

    @staticmethod
    def _replacement_row(state, replacement_lease_id):
        return next((row for row in state["rows"].values()
                     if row["replacement_lease_id"] == replacement_lease_id), None)

    @staticmethod
    def _resolve(row, kind, dispatch_name, observer_nonce, detail):
        launch = row["launch"]
        stamp = time.time()
        launch.update(state="spawn_denied", agent_id=None, proof=detail[:500], manual_from=None,
                      launch_ref=None, updated_at=stamp)
        launch["audit"] = (launch["audit"] + [{"kind": kind, "dispatch_name": dispatch_name,
            "attempt": launch["attempt"], "observer_nonce": observer_nonce, "at": stamp,
            "detail": detail[:500]}])[-MAX_LAUNCH_AUDIT:]
        row["updated_at"] = stamp
        return launch

    def launch_intent(self, lease_id, dispatch_name, generation, *, host_nonce, expected_version):
        require(text(dispatch_name, 200) and integer(generation, 2) and text(host_nonce, 200),
                "stranded launch intent")
        def apply(state):
            row = self._replacement_row(state, lease_id)
            require(row is not None and row["state"] == "recovered", "stranded recovery not launch-ready")
            launch = row["launch"]
            if launch["state"] == "launch_intent":
                require(launch["dispatch_name"] == dispatch_name and launch["generation"] == generation
                        and launch["host_nonce"] == host_nonce, "another stranded launch is unresolved")
                return launch
            require(launch["state"] in ("none", "spawn_denied"), "stranded launch outcome unresolved")
            launch.update(state="launch_intent", dispatch_name=dispatch_name, attempt=launch["attempt"] + 1,
                          generation=generation, agent_id=None, proof=None, host_nonce=host_nonce,
                          manual_from=None, launch_ref=new_launch_ref(), updated_at=time.time())
            row["updated_at"] = time.time()
            return launch
        return self._change(apply, expected_version)

    def spawn_observed(self, lease_id, dispatch_name, agent_id, *, expected_version):
        require(text(dispatch_name, 200) and text(agent_id, 200), "stranded spawn observation")
        def apply(state):
            row = self._replacement_row(state, lease_id)
            launch = None if row is None else row["launch"]
            require(launch is not None and launch["dispatch_name"] == dispatch_name
                    and launch["state"] in ("launch_intent", "spawn_observed")
                    and launch["agent_id"] in (None, agent_id), "stale stranded spawn observation")
            if launch["state"] != "spawn_observed":
                launch.update(state="spawn_observed", agent_id=agent_id, launch_ref=new_launch_ref(),
                              updated_at=time.time())
                row["updated_at"] = time.time()
            return launch
        return self._change(apply, expected_version)

    def launch_denied(self, lease_id, dispatch_name, proof, *, expected_version):
        require(text(proof, 500), "stranded spawn denial proof")
        def apply(state):
            row = self._replacement_row(state, lease_id)
            launch = None if row is None else row["launch"]
            require(launch is not None and launch["state"] == "launch_intent"
                    and launch["dispatch_name"] == dispatch_name, "stale stranded launch denial")
            return self._resolve(row, "denied", dispatch_name, None, proof)
        return self._change(apply, expected_version)

    @staticmethod
    def _absent_agent_matches(launch, absent_agent_id):
        observed = launch["state"] == "spawn_observed" or (
            launch["state"] == "manual_requested" and launch["manual_from"] == "spawn_observed")
        require(not observed or (absent_agent_id is not None and absent_agent_id == launch["agent_id"]),
                "host must assert the exact observed agent absent from its agent list")

    def launch_absent(self, lease_id, dispatch_name, observer_nonce, *, absent_agent_id=None,
                      expected_version):
        require(text(dispatch_name, 200) and text(observer_nonce, 200), "stranded launch absence")
        def apply(state):
            row = self._replacement_row(state, lease_id)
            require(row is not None and row["state"] == "recovered", "unknown stranded launch")
            launch = row["launch"]
            last = launch["audit"][-1] if launch["audit"] else None
            if (launch["state"] == "spawn_denied" and launch["dispatch_name"] == dispatch_name
                    and last is not None and last["kind"] == "absent" and last["observer_nonce"] == observer_nonce):
                return launch
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
        require(text(scope, 200) and text(review_lease_id, 200), "stranded manual launch request")
        require(launch_ref_valid(launch_ref), "exact rework launch reference required")
        require(confirmed is True, "manual launch request requires the person's explicit confirmation")
        def apply(state):
            row = state["rows"].get(review_lease_id)
            require(row is not None and row["scope"] == scope and row["state"] == "recovered",
                    "unknown rework launch")
            launch = row["launch"]
            require(launch["state"] in UNRESOLVED_LAUNCH_STATES,
                    "rework launch is not unresolved or observed-unbound")
            require(launch["launch_ref"] == launch_ref, "stale rework launch reference")
            if launch["state"] == "manual_requested":
                return launch
            stamp = time.time()
            prior = launch["state"]
            launch.update(state="manual_requested", manual_from=prior,
                          proof="user confirmed a host absence check is required", updated_at=stamp)
            launch["audit"] = (launch["audit"] + [{
                "kind": "manual_requested", "dispatch_name": launch["dispatch_name"],
                "attempt": launch["attempt"], "observer_nonce": None, "at": stamp,
                "detail": "user requested resolution after a host agent-list check"}])[-MAX_LAUNCH_AUDIT:]
            row["updated_at"] = stamp
            return launch
        return self._change(apply, expected_version)

    def launch_manual(self, lease_id, dispatch_name, observer_nonce, *, absent_agent_id=None,
                      launch_ref, expected_version):
        require(text(dispatch_name, 200) and (observer_nonce is None or text(observer_nonce, 200)),
                "stranded manual launch resolution")
        def apply(state):
            row = self._replacement_row(state, lease_id)
            require(row is not None and row["state"] == "recovered", "unknown stranded launch")
            launch = row["launch"]
            last = launch["audit"][-1] if launch["audit"] else None
            if (launch["state"] == "spawn_denied" and launch["dispatch_name"] == dispatch_name
                    and last is not None and last["kind"] == "manual"):
                return launch
            require(launch["state"] == "manual_requested", "manual launch resolution was not requested")
            require(launch["dispatch_name"] == dispatch_name, "stale rework launch name")
            require(launch["launch_ref"] == launch_ref, "stale rework launch reference")
            self._absent_agent_matches(launch, absent_agent_id)
            return self._resolve(row, "manual", dispatch_name, observer_nonce,
                                 "host confirmed the user-requested launch absent from its agent list")
        return self._change(apply, expected_version)

    def launch_present(self, lease_id, dispatch_name, agent_id, *, launch_ref, expected_version):
        require(text(dispatch_name, 200) and text(agent_id, 200), "stranded present launch")
        def apply(state):
            row = self._replacement_row(state, lease_id)
            require(row is not None and row["state"] == "recovered", "unknown stranded launch")
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
            return launch
        return self._change(apply, expected_version)

    def launch_bound(self, replacement_lease_id, agent_id, agent_name=None, *, handoff_drift="unknown",
                     expected_version):
        require(text(agent_id, 200), "stranded bound agent")
        def apply(state):
            row = self._replacement_row(state, replacement_lease_id)
            require(row is not None, "unknown stranded launch")
            launch = row["launch"]
            if launch["state"] == "bound":
                require(launch["agent_id"] == agent_id, "stranded launch bound to another agent")
                return launch
            source = launch["manual_from"] if launch["state"] == "manual_requested" else launch["state"]
            observed = source == "spawn_observed" and launch["agent_id"] == agent_id
            named = source == "launch_intent" and agent_name is not None and launch["dispatch_name"] == agent_name
            require(observed or named, "stranded launch was not observed for this agent")
            token = "handoff_drift=" + (str(handoff_drift).lower() if type(handoff_drift) is bool else handoff_drift)
            prior = launch.get("proof") or ""
            proof = (prior[:max(0, 500 - len(token) - 2)] + "; " if prior else "") + token
            launch.update(state="bound", agent_id=agent_id, proof=proof, launch_ref=None, manual_from=None,
                          updated_at=time.time())
            row["updated_at"] = time.time()
            return launch
        return self._change(apply, expected_version)

    def launch_status(self, scope, observer_nonce=None, *, limit=64):
        require(text(scope, 200) and integer(limit, 1, 64), "stranded launch status")
        state = self.read()
        rows = []
        for row in state["rows"].values():
            launch = row["launch"]
            if row["scope"] != scope or row["state"] != "recovered" or launch["state"] == "bound":
                continue
            intent_nonce = launch["host_nonce"]
            rows.append({"review_lease_id": row["lease_id"], "lease_id": row["replacement_lease_id"],
                         "item_id": row["item_id"], "generation": row["replacement_generation"],
                         "dispatch_name": launch["dispatch_name"], "state": launch["state"],
                         "attempt": launch["attempt"], "launch_ref": launch["launch_ref"],
                         "manual_requested": launch["state"] == "manual_requested",
                         "manual_from": launch["manual_from"], "intent_nonce_present": intent_nonce is not None,
                         "same_process": (None if intent_nonce is None or observer_nonce is None
                                          else intent_nonce == observer_nonce),
                         "observed_agent_id": launch["agent_id"], "updated_at": launch["updated_at"]})
        rows.sort(key=lambda value: (value["updated_at"], value["lease_id"]))
        return {"stranded_version": state["version"], "launches": rows[:limit], "truncated": len(rows) > limit}


def stranded_store(store):
    return WorkParallelStrandedStore(Path(store.path).with_name("work-parallel-stranded-v1.json"))
