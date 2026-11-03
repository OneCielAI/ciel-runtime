"""Standalone Work parallel policy. Use the SAME caller-selected state path across
workspaces for a device-wide cap, otherwise place beside work-plan.json. Host
supplies fresh tree approval/focus/dependency projections and measured usage.
No process actions, tree mutations or artifact deletions. Unknown leases never
expire. Every mutation requires snapshot-version CAS and returns (state, result).
"""
from __future__ import annotations
import copy
import json
import math
import os
import threading
import time
import uuid
from pathlib import Path
from .channel_message_repository import exclusive_file_lock
from .work_parallel_defaults import DEFAULT_INSTRUCTION

MAX_BYTES = 2097152
MAX_LEASES = 1000
DEFAULT_DISCOVERY_TEXT = DEFAULT_INSTRUCTION
STATES = ("reserved", "running", "waiting_input", "submitted", "verifying", "reviewing", "accepted", "cleanup_pending", "done")

class WorkParallelError(ValueError):
    pass

class WorkParallelConflict(WorkParallelError):
    pass

def require(ok, message):
    if not ok:
        raise WorkParallelError(message)

def integer(v, low=0, high=2**53-1):
    return type(v) is int and low <= v <= high

def number(v, high=1):
    return type(v) in (int, float) and math.isfinite(v) and 0 <= v <= high

def text(v, limit=2000):
    return type(v) is str and 0 < len(v) <= limit and chr(0) not in v

def digest(v):
    return type(v) is str and len(v) == 64 and all(c in "0123456789abcdef" for c in v)

def fields(v, names):
    return type(v) is dict and set(v) == set(names.split())

def default_policy():
    return dict(enabled=False, paused=False, max_active=3, p1_reserved=1,
                thresholds=dict(P2=.8, P3=.6, P4=.4, P5=.2), launches_per_minute=4,
                max_rework=2, discovery_text=DEFAULT_DISCOVERY_TEXT, test_override=True)

def validate_policy(p):
    require(type(p) is dict and set(p) == set(default_policy()), "policy fields")
    require(all(type(p[k]) is bool for k in ("enabled", "paused", "test_override")), "policy flags")
    require(integer(p["max_active"], 1, 100) and integer(p["p1_reserved"], 0, p["max_active"]), "policy cap")
    require(integer(p["launches_per_minute"], 1, 60) and integer(p["max_rework"], 0, 10), "policy rate/rework")
    require(text(p["discovery_text"]), "discovery text")
    t = p["thresholds"]
    require(fields(t, "P2 P3 P4 P5") and all(number(v) for v in t.values()), "threshold values")
    require([t[k] for k in ("P2", "P3", "P4", "P5")] == sorted(t.values(), reverse=True), "threshold order")

def validate(d):
    # Optional scheduler memory is persisted without expiring uncertain leases.
    require(type(d) is dict and not set(d) - {"schema", "version", "policy", "leases", "launches", "admission"}
            and {"schema", "version", "policy", "leases", "launches"} <= set(d), "state fields")
    memory = d.get("admission", {})
    require(type(memory) is dict and len(memory) <= 4, "admission memory")
    for key, value in memory.items():
        require(key in ("P2", "P3", "P4", "P5") and fields(value, "blocked below_since")
                and type(value["blocked"]) is bool and (value["below_since"] is None or number(value["below_since"], 1e15)), "admission state")
    require(type(d["schema"]) is int and d["schema"] == 1 and integer(d["version"]), "schema/version")
    validate_policy(d["policy"])
    require(type(d["leases"]) is dict and len(d["leases"]) <= MAX_LEASES, "lease bound")
    for ident, r in d["leases"].items():
        require(fields(r, "id scope item_id priority created_at role mode plan_version state host_id slot usage artifact rework evidence idle_proof cleanup_proof"), "lease fields")
        require(text(ident, 200) and r["id"] == ident and text(r["scope"], 200) and text(r["item_id"], 200), "lease identity")
        require(r["priority"] in ("P1", "P2", "P3", "P4", "P5") and number(r["created_at"], 1e15), "priority/time")
        require(r["role"] in ("worker", "reviewer", "resume") and r["mode"] in ("fork", "fresh"), "role/mode")
        require(r["priority"] != "P1" or r["mode"] == "fork", "P1 must fork")
        require(integer(r["plan_version"]) and r["state"] in STATES and integer(r["rework"], 0, 10), "lifecycle")
        require(r["host_id"] is None or text(r["host_id"], 200), "host")
        require(type(r["slot"]) is bool and number(r["usage"]), "slot/usage")
        require(r["artifact"] is None or digest(r["artifact"]), "artifact")
        require(all(r[k] is None or text(r[k]) for k in ("evidence", "idle_proof", "cleanup_proof")), "proof")
        require(r["slot"] or (r["idle_proof"] is not None and r["usage"] == 0), "idle proof missing")
        if r["state"] != "reserved":
            require(r["host_id"] is not None, "unbound worker")
        if r["state"] in ("submitted", "verifying", "reviewing", "accepted", "cleanup_pending", "done"):
            require(r["artifact"] is not None, "missing artifact")
        if r["state"] in ("reserved", "running", "waiting_input"):
            require(r["artifact"] is None, "unexpected artifact")
        if r["state"] == "reserved":
            require(r["host_id"] is None, "reserved host already bound")
        if r["state"] == "done":
            require(not r["slot"] and r["cleanup_proof"] is not None, "cleanup missing")
    # Cap reductions drain existing leases; reserve enforces the new cap.
    require(type(d["launches"]) is list and len(d["launches"]) <= 60 and all(number(t, 1e15) for t in d["launches"]), "launch history")

def unique_object(pairs):
    d = {}
    for k, v in pairs:
        require(k not in d, "duplicate JSON key")
        d[k] = v
    return d

class WorkParallelStore:
    """Corruption stays in place and blocks writes. No-op observations preserve
    version. Terminal history is retained; admission fails closed at the bound.
    """
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self._lock = threading.RLock()

    def _read(self):
        try:
            with self.path.open("rb") as f:
                raw = f.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return dict(schema=1, version=0, policy=default_policy(), leases={}, launches=[])
        require(len(raw) <= MAX_BYTES, "byte limit")
        try:
            d = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
            validate(d)
            return d
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as e:
            raise WorkParallelError("corrupt parallel state; writes blocked") from e

    def read(self):
        with self._lock, exclusive_file_lock(self.path):
            return self._read()

    def _change(self, fn, version):
        require(integer(version), "expected_version required")
        with self._lock, exclusive_file_lock(self.path):
            before = self._read()
            if before["version"] != version:
                raise WorkParallelConflict("state changed; reread")
            d = copy.deepcopy(before)
            result = fn(d)
            if d != before:
                d["version"] += 1
                validate(d)
                raw = json.dumps(d, ensure_ascii=False, allow_nan=False).encode("utf-8")
                require(len(raw) <= MAX_BYTES, "byte limit")
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

    def configure(self, changes, *, capability_ready, expected_version):
        """Enable only after explicit host capability gating. Pause retains leases."""
        require(type(changes) is dict and not set(changes) - set(default_policy()), "policy changes")
        require(type(capability_ready) is bool, "capability flag")
        def apply(d):
            patch = copy.deepcopy(changes)
            if isinstance(patch.get("thresholds"), dict):
                patch["thresholds"] = {**d["policy"]["thresholds"], **patch["thresholds"]}
            d["policy"].update(patch)
            validate_policy(d["policy"])
            require(not d["policy"]["enabled"] or capability_ready, "capability not ready")
        return self._change(apply, expected_version)

    def observe_usage(self, measured_usage, *, expected_version, now=None):
        """Persist 10-point/30-second recovery hysteresis; unknown never clears."""
        require(measured_usage is None or number(measured_usage), "measured usage")
        stamp = time.time() if now is None else now
        require(number(stamp, 1e15), "clock")
        def apply(d):
            memory = d.setdefault("admission", {})
            for priority, threshold in d["policy"]["thresholds"].items():
                value = memory.setdefault(priority, {"blocked": False, "below_since": None})
                if measured_usage is None or measured_usage >= threshold:
                    value.update(blocked=True, below_since=None)
                elif value["blocked"]:
                    if measured_usage < max(0, threshold - .1):
                        if value["below_since"] is None:
                            value["below_since"] = stamp
                        elif stamp - value["below_since"] >= 30:
                            value.update(blocked=False, below_since=None)
                    else:
                        value["below_since"] = None
        return self._change(apply, expected_version)

    def reserve(self, candidates, *, scope, measured_usage, expected_version, now=None):
        """Candidates require exact fields validated below. Best eligible priority,
        then newest created_at wins. Usage=measurement+reservations+request.
        Reviewers/resumes consume slots and launch rate exactly like workers.
        Discovery/test_override configuration NEVER bypasses admission gates.
        """
        require(text(scope, 200) and type(candidates) is list and 0 < len(candidates) <= 600, "candidates/scope")
        for r in candidates:
            require(fields(r, "item_id priority created_at approved focused dependencies_done plan_version usage role mode"), "candidate fields")
            require(text(r["item_id"], 200) and r["priority"] in ("P1", "P2", "P3", "P4", "P5"), "candidate identity")
            require(number(r["created_at"], 1e15) and integer(r["plan_version"]) and number(r["usage"]), "candidate values")
            require(all(type(r[k]) is bool for k in ("approved", "focused", "dependencies_done")), "candidate gates")
            require(r["role"] in ("worker", "reviewer", "resume") and r["mode"] in ("fork", "fresh"), "candidate mode")
        require(len({r["item_id"] for r in candidates}) == len(candidates), "duplicate candidates")
        require(measured_usage is None or number(measured_usage), "measured usage")
        stamp = time.time() if now is None else now
        require(number(stamp, 1e15), "clock")
        def apply(d):
            p = d["policy"]
            require(p["enabled"] and not p["paused"], "disabled/paused")
            active = [a for a in d["leases"].values() if a["slot"]]
            require(len(active) < p["max_active"], "hard cap reached")
            history = [t for t in d["launches"] if stamp - t < 60]
            require(len(history) < p["launches_per_minute"], "launch rate reached")
            require(len(d["leases"]) < MAX_LEASES, "lease bound")
            if any(r["role"] == "reviewer" for r in candidates) and any(a["role"] == "reviewer" for a in active):
                raise WorkParallelError("reviewer concurrency reached")
            for r in sorted(candidates, key=lambda a: (a["priority"], -a["created_at"], a["item_id"])):
                if not all(r[k] for k in ("approved", "focused", "dependencies_done")):
                    continue
                if any(a["scope"] == scope and a["item_id"] == r["item_id"] and a["role"] == r["role"] and (a["slot"] or a["state"] != "done") for a in d["leases"].values()):
                    continue
                if r["priority"] == "P1":
                    if r["mode"] != "fork":
                        continue
                elif (d.get("admission", {}).get(r["priority"], {}).get("blocked", False) or len(active) >= max(1, p["max_active"] - p["p1_reserved"]) or measured_usage is None or
                      measured_usage + sum(a["usage"] for a in active) + r["usage"] >= p["thresholds"][r["priority"]]):
                    continue
                ident = uuid.uuid4().hex
                d["leases"][ident] = dict(id=ident, scope=scope, item_id=r["item_id"], priority=r["priority"],
                    created_at=r["created_at"], role=r["role"], mode=r["mode"], plan_version=r["plan_version"],
                    state="reserved", host_id=None, slot=True, usage=r["usage"], artifact=None,
                    rework=0, evidence=None, idle_proof=None, cleanup_proof=None)
                d["launches"] = history + [stamp]
                return ident
            raise WorkParallelError("no eligible candidate")
        return self._change(apply, expected_version)

    @staticmethod
    def _lease(d, ident):
        require(text(ident, 200) and ident in d["leases"], "unknown lease")
        return d["leases"][ident]

    def bind(self, ident, host_id, *, expected_version):
        """Bind once after launch. A failed launch still needs idle reconciliation."""
        require(text(host_id, 200), "host id")
        def apply(d):
            r = self._lease(d, ident)
            require(r["state"] == "reserved" and r["slot"] and r["host_id"] is None, "not bindable")
            require(not any(a["slot"] and a["host_id"] == host_id for a in d["leases"].values()), "host already bound")
            r.update(host_id=host_id, state="running")
        return self._change(apply, expected_version)

    def reconcile(self, ident, *, host_state, proof=None, expected_version):
        """Unknown/running retain slot forever; positive idle proof releases it.
        Released runs cannot execute again: reserve a new resume-role lease.
        """
        require(host_state in ("unknown", "running", "idle"), "host observation")
        require(host_state != "idle" or text(proof), "idle proof required")
        def apply(d):
            r = self._lease(d, ident)
            if host_state == "idle":
                r.update(slot=False, usage=0, idle_proof=proof)
        return self._change(apply, expected_version)

    def transition(self, ident, target, *, plan_version, expected_version, artifact=None, evidence=None, cleanup_proof=None, cleanup_action=None):
        """submitted->verifying->reviewing->accepted->cleanup_pending->done.
        verifying/reviewing->running requests bounded rework. Each transition
        requires evidence, matching plan version, and exact submitted artifact.
        done requires separate idle reconciliation plus cleanup proof. No deletion.
        """
        require(integer(plan_version), "plan version")
        def apply(d):
            r = self._lease(d, ident)
            if r["plan_version"] != plan_version:
                raise WorkParallelConflict("stale plan version")
            edges = dict(running={"submitted", "waiting_input"}, waiting_input={"running"},
                submitted={"verifying"}, verifying={"reviewing", "running"}, reviewing={"accepted", "running"},
                accepted={"cleanup_pending"}, cleanup_pending={"done"})
            require(type(target) is str and target in edges.get(r["state"], set()), "transition")
            if target not in ("cleanup_pending", "done"):
                require(r["slot"] and r["host_id"] is not None, "no active slot")
            require(text(evidence), "transition evidence")
            if target == "submitted":
                require(digest(artifact), "artifact SHA256 required")
                r["artifact"] = artifact
            elif r["artifact"] is not None and artifact != r["artifact"]:
                raise WorkParallelConflict("stale artifact")
            if target == "running" and r["state"] in ("verifying", "reviewing"):
                require(r["rework"] < d["policy"]["max_rework"], "rework limit")
                r.update(rework=r["rework"] + 1, artifact=None)
            if target == "done":
                require(not r["slot"] and r["idle_proof"] is not None and text(cleanup_proof), "cleanup/idle proofs required")
                r["cleanup_proof"] = cleanup_proof
            r.update(state=target, evidence=evidence)
            # Validate the proposed transition under the CAS lock before filesystem effects.
            validate(d)
            if cleanup_action is not None:
                require(target == "cleanup_pending" and not r["slot"], "cleanup requires idle lease")
                r["evidence"] = json.dumps(cleanup_action())
        return self._change(apply, expected_version)
