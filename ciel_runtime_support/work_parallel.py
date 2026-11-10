"""Standalone Work parallel policy. Use the SAME caller-selected state path across
workspaces for a device-wide cap, otherwise place beside work-plan.json. Host
supplies fresh tree approval/focus/dependency projections and measured usage.
No process actions, tree mutations or artifact deletions. Unknown leases never
expire. Every mutation requires snapshot-version CAS and returns (state, result).
"""
from __future__ import annotations
import copy
import hashlib
import hmac
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
# New lease records stop at this backstop; a stored ledger above it (written
# under the old bound) must stay readable — validate uses the storage ceiling
# and the history drains as leases complete. Active execution slots are a
# separate, much smaller limit (policy.priority_limits).
MAX_LEASES = 100
STORAGE_MAX_LEASES = 100_000
DEFAULT_DISCOVERY_TEXT = DEFAULT_INSTRUCTION
STATES = ("reserved", "running", "waiting_input", "submitted", "verifying", "reviewing", "accepted", "cleanup_pending", "done", "unreported", "orphaned")
ACTIVE_STATES = frozenset(("reserved", "running", "waiting_input", "submitted", "verifying", "reviewing", "accepted", "cleanup_pending"))

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

def hash_worker_token(value):
    require(text(value, 512), "worker token")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def worker_token_matches(lease, value):
    expected = lease.get("report_hash")
    return digest(expected) and hmac.compare_digest(expected, hash_worker_token(value))

def fields(v, names):
    return type(v) is dict and set(v) == set(names.split())

PRIORITIES = ("P1", "P2", "P3", "P4", "P5")
# Concurrent fork sessions per priority (user, 2026-11-09). There is no
# separate device total: the device maximum is the sum of these limits.
DEFAULT_PRIORITY_LIMITS = dict(P1=5, P2=3, P3=1, P4=1, P5=1)
MAX_PRIORITY_LIMIT = 16

def default_policy():
    # Parallel dispatch is on by default. This policy controls only concurrent
    # execution slots and launch rate; it never limits Work-board queue intake.
    # Each priority has its own number of concurrent fork sessions.
    # The thresholds field is retained solely so older ledgers stay readable;
    # it no longer gates anything.
    return dict(enabled=True, paused=False, priority_limits=dict(DEFAULT_PRIORITY_LIMITS),
                thresholds=dict(P2=1.0, P3=1.0, P4=1.0, P5=1.0), launches_per_minute=4,
                max_rework=2, discovery_text=DEFAULT_DISCOVERY_TEXT, test_override=True)

def migrate_policy(p):
    """A ledger written before per-priority limits keeps its leases; its single
    device cap and P1 reserve are replaced by the default per-priority limits."""
    if type(p) is dict and ("max_active" in p or "p1_reserved" in p or "priority_limits" not in p):
        p.pop("max_active", None)
        p.pop("p1_reserved", None)
        p.setdefault("priority_limits", dict(DEFAULT_PRIORITY_LIMITS))
    return p

def active_by_priority(leases):
    counts = dict.fromkeys(PRIORITIES, 0)
    for lease in leases.values():
        if lease["slot"]:
            counts[lease["priority"]] += 1
    return counts

def validate_policy(p):
    require(type(p) is dict and set(p) == set(default_policy()), "policy fields")
    require(all(type(p[k]) is bool for k in ("enabled", "paused", "test_override")), "policy flags")
    limits = p["priority_limits"]
    require(type(limits) is dict and set(limits) == set(PRIORITIES)
            and all(integer(limits[k], 1, MAX_PRIORITY_LIMIT) for k in PRIORITIES), "priority limits")
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
    require(type(d["leases"]) is dict and len(d["leases"]) <= STORAGE_MAX_LEASES, "lease storage bound")
    for ident, r in d["leases"].items():
        # Optional fields keep ledgers written before the corresponding lifecycle
        # evidence readable. Plain worker capabilities are never persisted.
        base = "id scope item_id priority created_at role mode plan_version state host_id slot usage artifact rework evidence idle_proof cleanup_proof"
        optional = "agent_name workspace dispatch_name owner report_hash terminal reserved_at bound_at state_changed_at"
        require(type(r) is dict and set(base.split()) <= set(r) and not set(r) - set((base + " " + optional).split()), "lease fields")
        require(r.get("agent_name") is None or text(r["agent_name"], 200), "agent name")
        require(r.get("dispatch_name") is None or text(r["dispatch_name"], 200), "dispatch name")
        require(all(r.get(k) is None or number(r[k], 1e15) for k in ("reserved_at", "bound_at", "state_changed_at")),
                "lifecycle times")
        owner = r.get("owner")
        require(owner is None or (fields(owner, "session_pid identity") and integer(owner["session_pid"], 1)
                and (owner["identity"] is None or type(owner["identity"]) is str)), "owner identity")
        require(r.get("report_hash") is None or digest(r["report_hash"]), "report hash")
        terminal = r.get("terminal")
        require(terminal is None or (fields(terminal, "reason observed_at proof") and text(terminal["reason"], 100)
                and number(terminal["observed_at"], 1e15) and text(terminal["proof"])), "terminal proof")
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
        if r["state"] in ("reserved", "running", "waiting_input", "unreported", "orphaned"):
            require(r["artifact"] is None, "unexpected artifact")
        if r["state"] == "reserved":
            require(r["host_id"] is None, "reserved host already bound")
        if r["state"] == "done":
            require(not r["slot"] and r["cleanup_proof"] is not None, "cleanup missing")
        if r["state"] in ("unreported", "orphaned"):
            require(not r["slot"] and r["idle_proof"] is not None and r.get("terminal") is not None, "terminal release missing")
            require(r.get("report_hash") is None, "terminal worker capability remains active")
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
            if type(d) is dict:
                migrate_policy(d.get("policy"))
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
                # Lifecycle time for the dashboard: when a lease last changed
                # state or slot. Written only alongside a real mutation.
                stamp = time.time()
                for ident, lease in d["leases"].items():
                    old = before["leases"].get(ident)
                    if old is None:
                        lease.setdefault("reserved_at", stamp)
                        lease["state_changed_at"] = stamp
                    elif (old["state"], old["slot"]) != (lease["state"], lease["slot"]):
                        lease["state_changed_at"] = stamp
                    if old is not None and old["host_id"] is None and lease["host_id"] is not None:
                        lease["bound_at"] = stamp
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
            for nested in ("thresholds", "priority_limits"):
                if isinstance(patch.get(nested), dict):
                    patch[nested] = {**d["policy"][nested], **patch[nested]}
            d["policy"].update(patch)
            validate_policy(d["policy"])
            require(not d["policy"]["enabled"] or capability_ready, "capability not ready")
        return self._change(apply, expected_version)

    def reserve(self, candidates, *, scope, expected_version, now=None, dispatch_name=None,
                report_hash=None, owner=None):
        """Candidates require exact fields validated below. Best eligible priority,
        then newest created_at wins. Admission is limited only by the number of
        concurrently executing fork slots and the launch rate; reviewers/resumes
        consume a slot. P1 may use every free slot, while lower priorities leave
        the configured P1 reserve available. Registering/queueing Work items is
        never an admission decision. Discovery/test_override configuration NEVER
        bypasses admission gates.
        """
        require(text(scope, 200) and type(candidates) is list and 0 < len(candidates) <= MAX_LEASES, "candidates/scope")
        require(dispatch_name is None or text(dispatch_name, 200), "dispatch name")
        require(report_hash is None or digest(report_hash), "report hash")
        require(owner is None or (fields(owner, "session_pid identity") and integer(owner["session_pid"], 1)
                and (owner["identity"] is None or type(owner["identity"]) is str)), "owner identity")
        for r in candidates:
            require(fields(r, "item_id priority created_at approved focused dependencies_done plan_version usage role mode"), "candidate fields")
            require(text(r["item_id"], 200) and r["priority"] in ("P1", "P2", "P3", "P4", "P5"), "candidate identity")
            require(number(r["created_at"], 1e15) and integer(r["plan_version"]) and number(r["usage"]), "candidate values")
            require(all(type(r[k]) is bool for k in ("approved", "focused", "dependencies_done")), "candidate gates")
            require(r["role"] in ("worker", "reviewer", "resume") and r["mode"] in ("fork", "fresh"), "candidate mode")
        require(len({r["item_id"] for r in candidates}) == len(candidates), "duplicate candidates")
        stamp = time.time() if now is None else now
        require(number(stamp, 1e15), "clock")
        def apply(d):
            p = d["policy"]
            require(p["enabled"] and not p["paused"], "disabled/paused")
            active = [a for a in d["leases"].values() if a["slot"]]
            counts, limits = active_by_priority(d["leases"]), p["priority_limits"]
            full = sorted({r["priority"] for r in candidates if counts[r["priority"]] >= limits[r["priority"]]})
            require(len(full) < len({r["priority"] for r in candidates}),
                    "active slot capacity reached for " + ", ".join(
                        f"{k} ({counts[k]}/{limits[k]})" for k in full))
            history = [t for t in d["launches"] if stamp - t < 60]
            require(len(history) < p["launches_per_minute"], "launch rate reached")
            require(len(d["leases"]) < MAX_LEASES, "lease bound")
            if any(r["role"] == "reviewer" for r in candidates) and any(a["role"] == "reviewer" for a in active):
                raise WorkParallelError("reviewer concurrency reached")
            for r in sorted(candidates, key=lambda a: (a["priority"], -a["created_at"], a["item_id"])):
                # Focus only orders Work; it never blocks another approved item.
                if not all(r[k] for k in ("approved", "dependencies_done")):
                    continue
                if any(a["scope"] == scope and a["item_id"] == r["item_id"] and a["role"] == r["role"]
                       and a["state"] in ACTIVE_STATES for a in d["leases"].values()):
                    continue
                if r["priority"] == "P1" and r["mode"] != "fork":
                    continue
                # Each priority runs up to its own number of fork sessions.
                if counts[r["priority"]] >= limits[r["priority"]]:
                    continue
                ident = uuid.uuid4().hex
                d["leases"][ident] = dict(id=ident, scope=scope, item_id=r["item_id"], priority=r["priority"],
                    created_at=r["created_at"], role=r["role"], mode=r["mode"], plan_version=r["plan_version"],
                    state="reserved", host_id=None, slot=True, usage=r["usage"], artifact=None,
                    rework=0, evidence=None, idle_proof=None, cleanup_proof=None,
                    dispatch_name=dispatch_name, owner=copy.deepcopy(owner), report_hash=report_hash, terminal=None)
                d["launches"] = history + [stamp]
                return ident
            raise WorkParallelError("no eligible candidate")
        return self._change(apply, expected_version)

    @staticmethod
    def _lease(d, ident):
        require(text(ident, 200) and ident in d["leases"], "unknown lease")
        return d["leases"][ident]

    def bind(self, ident, host_id, *, expected_version, agent_name=None):
        """Bind once after launch. A failed launch still needs idle reconciliation."""
        require(text(host_id, 200), "host id")
        require(agent_name is None or text(agent_name, 200), "agent name")
        def apply(d):
            r = self._lease(d, ident)
            require(r["state"] == "reserved" and r["slot"] and r["host_id"] is None, "not bindable")
            require(not any(a["slot"] and a["host_id"] == host_id for a in d["leases"].values()), "host already bound")
            r.update(host_id=host_id, state="running")
            if agent_name is not None:
                r["agent_name"] = agent_name
        return self._change(apply, expected_version)

    def release_unbound(self, ident, *, proof, expected_version):
        """A provisional reservation never became a worker. Release only that
        execution lease; the Work item itself stays queued for a later dispatch.
        A host-bound lease is a real run and must keep its slot/lifecycle.
        """
        require(text(proof), "release proof required")
        def apply(d):
            r = self._lease(d, ident)
            require(r["state"] == "reserved" and r["host_id"] is None and r["slot"], "not an unbound reservation")
            del d["leases"][ident]
        return self._change(apply, expected_version)

    def attach_workspace(self, ident, workspace, *, expected_version):
        require(type(workspace) is dict and text(workspace.get("path")) and text(workspace.get("repo")), "workspace binding")
        def apply(d):
            r = self._lease(d, ident)
            require(r["state"] == "reserved" and r["host_id"] is None, "workspace must precede launch")
            r["workspace"] = copy.deepcopy(workspace)
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

    def terminate_unreported(self, ident, *, proof, expected_version, now=None):
        """Record a positive terminal host observation without a worker report.

        The Work item remains unfinished and can receive a replacement lease.
        """
        require(text(proof, 500), "terminal proof required")
        stamp = time.time() if now is None else now
        require(number(stamp, 1e15), "clock")
        def apply(d):
            r = self._lease(d, ident)
            if r["state"] == "unreported":
                return
            require(r["state"] == "running" and r["host_id"] is not None, "worker is not running")
            r.update(state="unreported", slot=False, usage=0, report_hash=None,
                     idle_proof=proof, evidence=proof,
                     terminal={"reason": "unreported", "observed_at": stamp, "proof": proof})
        return self._change(apply, expected_version)

    def orphan_release(self, ident, *, proof, observation, expected_version, now=None):
        """Release a positively proven dead owner while retaining an audit row."""
        require(text(proof, 500) and type(observation) is dict, "orphan proof required")
        state = observation.get("owner_state")
        if state == "dead":
            require(observation.get("observed_identity") == "", "dead process identity proof required")
        elif state == "legacy_no_owner":
            require(all(observation.get(k) is True for k in
                        ("host_dead", "scope_board_missing", "pid_absent")),
                    "legacy orphan requires explicit operator observations")
        else:
            raise WorkParallelError("live or unknown owner is retained")
        stamp = time.time() if now is None else now
        require(number(stamp, 1e15), "clock")
        def apply(d):
            r = self._lease(d, ident)
            if r["state"] == "orphaned":
                return
            require(r["slot"] and r["state"] in ACTIVE_STATES, "lease does not hold an active slot")
            if state == "dead":
                require(r.get("owner") is not None, "owner identity unavailable")
            else:
                require(r.get("owner") is None, "legacy observation does not match lease")
            r.update(state="orphaned", slot=False, usage=0, report_hash=None,
                     idle_proof=proof, evidence=proof,
                     terminal={"reason": "orphaned", "observed_at": stamp, "proof": proof})
        return self._change(apply, expected_version)

    def wait(self, ident, *, proof, expected_version):
        """Release only the execution slot when a bound worker is waiting.

        A question/blocked wait is not completion: the durable lease, host
        binding and Work item remain recorded so the same worker can later be
        resumed.  The slot can immediately admit another queued candidate.
        """
        require(text(proof, 500), "wait proof required")
        def apply(d):
            r = self._lease(d, ident)
            require(r["state"] == "waiting_input" and r["host_id"] is not None and r["slot"], "not a waiting worker")
            r.update(slot=False, usage=0, idle_proof=proof)
        return self._change(apply, expected_version)

    def reacquire(self, ident, *, expected_version):
        """Reclaim a slot for the same waiting lease before resuming it."""
        def apply(d):
            p = d["policy"]
            require(p["enabled"] and not p["paused"], "disabled/paused")
            r = self._lease(d, ident)
            require(r["state"] == "waiting_input" and r["host_id"] is not None and not r["slot"], "not a slot-released wait")
            counts = active_by_priority(d["leases"])
            require(counts[r["priority"]] < p["priority_limits"][r["priority"]],
                    f"active slot capacity reached for {r['priority']}; item remains waiting")
            r.update(slot=True, idle_proof=None)
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
            if target in ("submitted", "running"):
                # Entering running includes the waiting_input -> running resume;
                # a slot-released wait must first pass through reacquire().
                require(r["slot"] and r["host_id"] is not None, "no active slot")
            elif target == "waiting_input":
                # A running worker may report a question before its slot is
                # released by the authenticated wait observation.
                require(r["host_id"] is not None, "no bound worker")
            require(text(evidence), "transition evidence")
            if target == "submitted":
                require(digest(artifact), "artifact SHA256 required")
                r["artifact"] = artifact
                r["report_hash"] = None
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
