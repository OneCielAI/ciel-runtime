"""Isolated focused tests; no router/session processes."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from ciel_runtime_support.work_parallel import WorkParallelStore, WorkParallelError, WorkParallelConflict, hash_worker_token


def candidate(name="n1", **changes):
    r = dict(item_id=name, priority="P2", created_at=10, approved=True, focused=True,
             dependencies_done=True, plan_version=7, usage=.05, role="worker", mode="fork")
    r.update(changes)
    return r

def _make_store(tmp_path):
    s = WorkParallelStore(tmp_path / "parallel.json")
    # Defaults are already enabled; persist a real file so CAS/atomicity tests have bytes.
    s.configure(dict(paused=True), capability_ready=True, expected_version=0)
    s.configure(dict(paused=False), capability_ready=True, expected_version=s.read()["version"])
    return s

def call(s, method, *args, **kwargs):
    return getattr(s, method)(*args, expected_version=s.read()["version"], **kwargs)

def reserve(s, name="n1", **kwargs):
    return call(s, "reserve", [candidate(name, **kwargs)], scope="scope", now=100)[1]

def transition(s, ident, target, **kwargs):
    return call(s, "transition", ident, target, plan_version=7, artifact="a"*64, evidence="observed", **kwargs)


class WorkParallelTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp_path = Path(temporary.name)
        self.store = _make_store(self.tmp_path)

    def test_defaults_capability_and_enabled_by_default(self):
        tmp_path = self.tmp_path
        s = WorkParallelStore(tmp_path / "state")
        p = s.read()["policy"]
        # Parallel dispatch is on by default; the per-priority limits are execution
        # concurrency, not a queue-registration cap (user, 2026-11-09).
        assert p["enabled"] and p["test_override"]
        assert p["priority_limits"] == dict(P1=5, P2=3, P3=1, P4=1, P5=1)
        assert "max_active" not in p and "p1_reserved" not in p
        assert p["max_rework"] == 2 and p["launches_per_minute"] == 4
        assert p["thresholds"] == dict(P2=1.0, P3=1.0, P4=1.0, P5=1.0)
        assert "미결 아이템" in p["discovery_text"]
        with self.assertRaises(WorkParallelError):
            s.configure(dict(enabled=True), capability_ready=False, expected_version=0)
        assert s.read()["version"] == 0
        assert reserve(s)


    def test_caller_order_is_authoritative(self):
        store = self.store
        # runnable_leaves already projects focused rows first. The ledger must not
        # undo that tree ordering by sorting priority or creation time again.
        rows = [candidate("focused-old", created_at=1, focused=True),
                candidate("new", created_at=20, focused=False),
                candidate("blocked", priority="P1", dependencies_done=False)]
        d, ident = call(store, "reserve", rows, scope="s", now=100)
        assert d["leases"][ident]["item_id"] == "focused-old"
        rows = [candidate("p2-first", created_at=30), candidate("p1-second", priority="P1", created_at=0)]
        d, ident = call(store, "reserve", rows, scope="s", now=100)
        assert d["leases"][ident]["item_id"] == "p2-first"


    def test_gates_1(self):
        store = self.store
        gate = 'approved'
        with self.assertRaises(WorkParallelError):
            reserve(store, **{gate: False})


    def test_gates_2(self):
        store = self.store
        gate = 'dependencies_done'
        with self.assertRaises(WorkParallelError):
            reserve(store, **{gate: False})


    def test_focus_flag_does_not_block_dispatch(self):
        # Focus orders Work; it never holds an approved item.
        store = self.store
        reserve(store, focused=False)
        assert sum(r["slot"] for r in store.read()["leases"].values()) == 1


    def test_slots_are_the_only_execution_admission_gate(self):
        store = self.store
        call(store, "configure", dict(priority_limits=dict(P1=1, P2=2), launches_per_minute=20), capability_ready=True)
        # Each priority uses its own concurrent slots; usage is not consulted.
        reserve(store, "a", usage=.99)
        reserve(store, "b", usage=.99)
        with self.assertRaises(WorkParallelError):
            reserve(store, "c", usage=0)
        reserve(store, "urgent", priority="P1")
        with self.assertRaises(WorkParallelError):
            reserve(store, "urgent2", priority="P1")
        assert sum(r["slot"] for r in store.read()["leases"].values()) == 3

    def test_default_priority_limits_run_independently(self):
        store = self.store
        call(store, "configure", dict(launches_per_minute=60), capability_ready=True)
        # Defaults P1 5, P2 3, P3-P5 1 each: a full priority never blocks another,
        # and the device maximum is their sum.
        for priority, limit in dict(P1=5, P2=3, P3=1, P4=1, P5=1).items():
            for i in range(limit):
                reserve(store, f"{priority}-{i}", priority=priority)
            with self.assertRaisesRegex(WorkParallelError, rf"capacity reached for {priority} \({limit}/{limit}\)"):
                reserve(store, f"{priority}-waiting", priority=priority)
        assert sum(r["slot"] for r in store.read()["leases"].values()) == 11

    def test_mixed_candidates_skip_a_full_priority(self):
        store = self.store
        call(store, "configure", dict(priority_limits=dict(P1=1, P2=1), launches_per_minute=20), capability_ready=True)
        reserve(store, "urgent", priority="P1")
        rows = [candidate("urgent2", priority="P1"), candidate("normal", priority="P2")]
        d, ident = call(store, "reserve", rows, scope="scope", now=100)
        assert d["leases"][ident]["item_id"] == "normal"


    def test_shared_cap(self):
        store = self.store
        call(store, "configure", dict(priority_limits=dict(P1=1, P2=2), launches_per_minute=20), capability_ready=True)
        reserve(store, "worker")
        other = WorkParallelStore(store.path)
        reserve(other, "second", role="resume")
        with self.assertRaises(WorkParallelError):
            reserve(other, "resume", role="resume")
        with self.assertRaises(WorkParallelError):
            reserve(other, "fresh", priority="P1", mode="fresh")
        reserve(other, "urgent", priority="P1")
        with self.assertRaises(WorkParallelError):
            reserve(other, "urgent2", priority="P1")
        assert sum(r["slot"] for r in store.read()["leases"].values()) == 3

    def test_new_reviewer_reservations_are_refused_and_legacy_rows_stay_readable(self):
        store = self.store
        before = store.read()["version"]
        with self.assertRaisesRegex(WorkParallelError, "reviewer reservations are retired"):
            reserve(store, "review-one", role="reviewer")
        assert store.read()["version"] == before
        # A reviewer row persisted by an older build keeps validating and counts
        # against its priority slot until it drains.
        import json
        doc = store.read()
        doc["leases"]["legacy-reviewer"] = dict(
            id="legacy-reviewer", scope="scope", item_id="n1", priority="P2", created_at=1,
            role="reviewer", mode="fork", plan_version=7, state="running", host_id="old-reviewer",
            slot=True, usage=.05, artifact=None, rework=0, evidence=None, idle_proof=None,
            cleanup_proof=None, report_hash="c" * 64)
        store.path.write_text(json.dumps(doc), encoding="utf-8")
        row = store.read()["leases"]["legacy-reviewer"]
        assert row["role"] == "reviewer" and row["slot"]


    def test_pause_cas(self):
        store = self.store
        ident = reserve(store)
        old = store.read()["version"]
        call(store, "configure", dict(paused=True), capability_ready=True)
        with self.assertRaises(WorkParallelConflict):
            store.bind(ident, "host", expected_version=old)
        with self.assertRaises(WorkParallelError):
            reserve(store, "n2", priority="P1")
        assert WorkParallelStore(store.path).read()["leases"][ident]["slot"]


    def test_wait_releases_slot_then_same_worker_reacquires_before_resume(self):
        store = self.store
        ident = reserve(store, "waiting-item", priority="P1")
        call(store, "bind", ident, "host")
        transition(store, ident, "waiting_input")
        before = store.read()
        d, _ = call(store, "reconcile", ident, host_state="unknown")
        assert d == before and d["leases"][ident]["slot"]

        call(store, "wait", ident, proof="native host waiting for input")
        parked = store.read()["leases"][ident]
        assert parked["state"] == "waiting_input" and parked["host_id"] == "host" and not parked["slot"]
        # The freed execution slot admits another queued item immediately.
        other = reserve(store, "next-item", priority="P1")
        assert store.read()["leases"][other]["slot"]
        # A slot-released wait cannot silently resume by a lifecycle transition.
        with self.assertRaises(WorkParallelError):
            transition(store, ident, "running")

        call(store, "reacquire", ident)
        resumed = store.read()["leases"][ident]
        assert resumed["slot"] and resumed["idle_proof"] is None
        transition(store, ident, "running")
        assert store.read()["leases"][ident]["state"] == "running"

    def test_waiting_worker_reacquires_only_within_its_priority(self):
        store = self.store
        call(store, "configure", dict(priority_limits=dict(P1=1, P2=1), launches_per_minute=20), capability_ready=True)
        waiting = reserve(store, "waiting-p2", priority="P2")
        call(store, "bind", waiting, "host-wait")
        transition(store, waiting, "waiting_input")
        call(store, "wait", waiting, proof="waiting for input")
        reserve(store, "busy-p2", priority="P2")
        with self.assertRaises(WorkParallelError):
            call(store, "reacquire", waiting)
        # P1 has its own slots.
        reserve(store, "urgent", priority="P1")
        assert sum(r["slot"] for r in store.read()["leases"].values()) == 2

    def test_idle_released_worker_still_uses_a_new_resume_lease(self):
        store = self.store
        ident = reserve(store)
        call(store, "bind", ident, "host")
        transition(store, ident, "waiting_input")
        call(store, "reconcile", ident, host_state="idle", proof="host idle observed")
        with self.assertRaises(WorkParallelError):
            transition(store, ident, "running")
        # Worker and resume are one execution family: the parked worker can
        # still reacquire, so a second execution lease is refused while it is open.
        with self.assertRaisesRegex(WorkParallelError, "no eligible candidate"):
            reserve(store, role="resume")
        with self.assertRaisesRegex(WorkParallelError, "no eligible candidate"):
            reserve(store, role="worker")
        # Only a terminal predecessor admits a new resume lease.
        doc = store.read()
        doc["leases"][ident].update(state="unreported", report_hash=None,
                                    terminal={"reason": "unreported", "observed_at": 1, "proof": "terminal"})
        import json
        store.path.write_text(json.dumps(doc), encoding="utf-8")
        assert reserve(store, role="resume") != ident


    def test_lifecycle(self):
        store = self.store
        ident = reserve(store)
        call(store, "bind", ident, "host")
        with self.assertRaises(WorkParallelConflict):
            call(store, "transition", ident, "submitted", plan_version=8, artifact="a"*64, evidence="proof")
        transition(store, ident, "submitted")
        with self.assertRaises(WorkParallelConflict):
            call(store, "transition", ident, "verifying", plan_version=7, artifact="b"*64, evidence="proof")
        for target in ("verifying", "reviewing", "accepted", "cleanup_pending"):
            transition(store, ident, target)
        with self.assertRaises(WorkParallelError):
            transition(store, ident, "done", cleanup_proof="cleanup verified")
        call(store, "reconcile", ident, host_state="idle", proof="idle verified")
        with self.assertRaises(WorkParallelError):
            transition(store, ident, "done")
        transition(store, ident, "done", cleanup_proof="artifact cleanup verified")
        r = store.read()["leases"][ident]
        assert r["state"] == "done" and not r["slot"] and r["cleanup_proof"]


    def test_rework(self):
        store = self.store
        ident = reserve(store)
        call(store, "bind", ident, "host")
        for _ in range(2):
            transition(store, ident, "submitted")
            transition(store, ident, "verifying")
            transition(store, ident, "running")
        transition(store, ident, "submitted")
        transition(store, ident, "verifying")
        with self.assertRaises(WorkParallelError):
            transition(store, ident, "running")
        assert store.read()["leases"][ident]["rework"] == 2


    def test_rate_clock(self):
        store = self.store
        for i in range(4):
            ident = reserve(store, str(i), priority="P1")
            call(store, "reconcile", ident, host_state="idle", proof="launch failed; host idle")
        with self.assertRaises(WorkParallelError):
            reserve(store, "fifth", priority="P1")
        with self.assertRaises(WorkParallelError):
            call(store, "reserve", [candidate("rollback", priority="P1")], scope="s", now=90)
        call(store, "reserve", [candidate("later", priority="P1")], scope="s", now=160)


    def test_corruption_json(self):
        tmp_path = self.tmp_path
        payload = b'{'
        path = tmp_path / "bad"
        path.write_bytes(payload)
        s = WorkParallelStore(path)
        with self.assertRaises(WorkParallelError):
            s.read()
        with self.assertRaises(WorkParallelError):
            s.configure({}, capability_ready=True, expected_version=0)
        assert path.read_bytes() == payload
        changes = dict(unknown=1)
        before = self.store.read()
        with self.assertRaises(WorkParallelError):
            call(self.store, "configure", changes, capability_ready=True)
        assert self.store.read() == before


    def test_strict_policy_6(self):
        store = self.store
        changes = dict(thresholds=dict(P2=float('nan'), P3=0.6, P4=0.4, P5=0.2))
        before = store.read()
        with self.assertRaises(WorkParallelError):
            call(store, "configure", changes, capability_ready=True)
        assert store.read() == before


    def test_atomic_bounds(self):
        store = self.store
        before = store.path.read_bytes()
        with patch("ciel_runtime_support.work_parallel.os.replace", side_effect=OSError("write failure")):
            with self.assertRaises(OSError):
                call(store, "configure", dict(paused=True), capability_ready=True)
        assert store.path.read_bytes() == before
        assert not list(store.path.parent.glob("*.tmp"))
        with patch("ciel_runtime_support.work_parallel.MAX_LEASES", 0):
            with self.assertRaises(WorkParallelError):
                reserve(store)


    def test_concurrent_cas(self):
        store = self.store
        version = store.read()["version"]
        def attempt(i):
            try:
                WorkParallelStore(store.path).reserve([candidate(str(i), priority="P1")], scope="s", expected_version=version, now=100)
                return True
            except WorkParallelConflict:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert sum(pool.map(attempt, range(4))) == 1
        assert len(store.read()["leases"]) == 1


    def test_duplicate_json_and_invalid_stored_state(self):
        store = self.store
        import json
        ident = reserve(store, priority="P1")
        call(store, "configure", dict(priority_limits=dict(P1=1)), capability_ready=True)
        with self.assertRaises(WorkParallelError):
            reserve(store, "three", priority="P1")
        doc = store.read()
        doc["leases"][ident]["state"] = "done"
        store.path.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(WorkParallelError):
            store.read()
        store.path.write_text("{" + chr(34) + "schema" + chr(34) + ":1," + chr(34) + "schema" + chr(34) + ":1}", encoding="utf-8")
        with self.assertRaises(WorkParallelError):
            store.read()


    def test_strict_candidates_1(self):
        store = self.store
        changes = dict(approved=1)
        before = store.read()
        with self.assertRaises(WorkParallelError):
            reserve(store, **changes)
        assert store.read() == before


    def test_strict_candidates_2(self):
        store = self.store
        changes = dict(usage=True)
        before = store.read()
        with self.assertRaises(WorkParallelError):
            reserve(store, **changes)
        assert store.read() == before


    def test_strict_candidates_3(self):
        store = self.store
        changes = dict(created_at=float('inf'))
        before = store.read()
        with self.assertRaises(WorkParallelError):
            reserve(store, **changes)
        assert store.read() == before


    def test_strict_candidates_4(self):
        store = self.store
        changes = dict(plan_version=True)
        before = store.read()
        with self.assertRaises(WorkParallelError):
            reserve(store, **changes)
        assert store.read() == before


    def test_strict_candidates_5(self):
        store = self.store
        changes = dict(extra=1)
        before = store.read()
        with self.assertRaises(WorkParallelError):
            reserve(store, **changes)
        assert store.read() == before


    def test_duplicate_binding_and_disable_retention(self):
        store = self.store
        first = reserve(store)
        second = reserve(store, "n2", role="resume")
        call(store, "bind", first, "host")
        with self.assertRaises(WorkParallelError):
            call(store, "bind", second, "host")
        call(store, "configure", dict(enabled=False), capability_ready=False)
        assert sum(r["slot"] for r in store.read()["leases"].values()) == 2

    def test_full_active_slots_do_not_block_board_queue_additions(self):
        # Execution admission and board intake are separate ledgers: with all
        # five slots busy, a new goal still records on the board (goal 23/27).
        from ciel_runtime_support import work_plan_tree as tree
        store = self.store
        call(store, "configure", dict(launches_per_minute=20), capability_ready=True)
        for i in range(5):
            reserve(store, f"busy-{i}", priority="P1")
        with self.assertRaises(WorkParallelError):
            reserve(store, "sixth", priority="P1")
        doc = tree.empty_document()
        active = tree.add_node(doc, None, "active", now=10)
        queued = tree.add_node(doc, None, "arrived while full", now=11, priority="P1")
        assert [n["id"] for n in tree.queued_roots(doc)] == [queued]
        assert doc["active_root"] == active

    def test_queue_storage_bound_is_100_not_execution_slots(self):
        from ciel_runtime_support.work_parallel import MAX_LEASES
        assert MAX_LEASES == 100

    def test_terminal_history_above_bound_stays_readable_and_allows_admission(self):
        # MAX_LEASES bounds open leases, not retained terminal audit history.
        import json
        store = self.store
        ident = reserve(store, priority="P1")
        doc = store.read()
        leases = doc["leases"]
        template = dict(leases[ident])
        for i in range(150):
            row = dict(template)
            row["id"] = f"old-{i}"
            row["item_id"] = f"old-item-{i}"
            row["state"] = "done"
            row["slot"] = False
            row["usage"] = 0
            row["idle_proof"] = "observed idle"
            row["host_id"] = f"old-host-{i}"
            row["artifact"] = "a" * 64
            row["cleanup_proof"] = "kept"
            leases[f"old-{i}"] = row
        store.path.write_text(json.dumps(doc), encoding="utf-8")
        reread = WorkParallelStore(store.path).read()
        assert len(reread["leases"]) == 151
        admitted = reserve(store, "after-history", priority="P2")
        assert store.read()["leases"][admitted]["state"] == "reserved"


    def test_single_slot_allows_non_p1(self):
        tmp_path = self.tmp_path
        s = WorkParallelStore(tmp_path / "single.json")
        call(s, "configure", dict(enabled=True, priority_limits=dict(P1=1, P2=1, P3=1, P4=1, P5=1)), capability_ready=True)
        ident = reserve(s)
        assert s.read()["leases"][ident]["priority"] == "P2"


    def test_provisional_unbound_reserve_is_released_without_touching_board(self):
        store = self.store
        ident = reserve(store)
        row = store.read()["leases"][ident]
        assert row["state"] == "reserved" and row["host_id"] is None and row["slot"]
        # A worker that never launched must not keep occupying a device slot.
        call(store, "release_unbound", ident, proof="native spawn denied before launch")
        assert ident not in store.read()["leases"]
        # The failure does not delete or mutate the Work item, so it remains queued
        # for a later dispatch.


    def test_worker_token_terminal_without_report_and_orphan_lifecycle(self):
        store = self.store
        token = "worker-token-" + "x" * 40
        d, ident = call(store, "reserve", [candidate("worker", priority="P1")], scope="scope", now=100,
                        dispatch_name="work-one", report_hash=hash_worker_token(token),
                        owner={"session_pid": 42, "identity": "birth-42"})
        row = d["leases"][ident]
        assert row["dispatch_name"] == "work-one"
        assert row["report_hash"] == hash_worker_token(token)
        assert "worker-token" not in str(row)
        call(store, "bind", ident, "host", agent_name="work-one")
        call(store, "terminate_unreported", ident, proof="turn.complete answer")
        terminal = store.read()["leases"][ident]
        assert terminal["state"] == "unreported" and not terminal["slot"]
        assert terminal["report_hash"] is None and terminal["idle_proof"]
        replacement = reserve(store, "worker", priority="P1")
        assert replacement != ident

        d, orphan = call(store, "reserve", [candidate("orphan", priority="P1")], scope="old", now=101,
                         dispatch_name="work-old", owner={"session_pid": 7, "identity": "birth-7"})
        call(store, "bind", orphan, "old-host", agent_name="work-old")
        with self.assertRaises(WorkParallelError):
            call(store, "orphan_release", orphan, proof="guess", observation={"owner_state": "unknown"})
        call(store, "orphan_release", orphan, proof="process identity absent",
             observation={"owner_state": "dead", "observed_identity": ""})
        stale = store.read()["leases"][orphan]
        assert stale["state"] == "orphaned" and not stale["slot"]

    def test_legacy_orphan_requires_explicit_operator_observation(self):
        store = self.store
        ident = reserve(store, "legacy", priority="P1")
        call(store, "bind", ident, "old-host")
        with self.assertRaises(WorkParallelError):
            call(store, "orphan_release", ident, proof="legacy", observation={"owner_state": "legacy_no_owner"})
        observed = {"owner_state": "legacy_no_owner", "host_dead": True,
                    "scope_board_missing": True, "pid_absent": True}
        call(store, "orphan_release", ident, proof="operator verified shutdown", observation=observed)
        assert store.read()["leases"][ident]["state"] == "orphaned"

    def test_legacy_unbound_orphan_is_released_not_recorded_without_host(self):
        store = self.store
        ident = reserve(store, "legacy-unbound", priority="P1")
        assert store.read()["leases"][ident]["host_id"] is None
        observed = {"owner_state": "legacy_no_owner", "host_dead": True,
                    "scope_board_missing": True, "pid_absent": True}
        call(store, "orphan_release", ident, proof="operator verified shutdown", observation=observed)
        assert ident not in store.read()["leases"]
        # Unknown/live owners stay conservative even for an unbound reservation.
        kept = reserve(store, "unbound-unknown", priority="P1")
        for observation in ({"owner_state": "unknown"}, {"owner_state": "live"}):
            with self.assertRaises(WorkParallelError):
                call(store, "orphan_release", kept, proof="guess", observation=observation)
        assert store.read()["leases"][kept]["slot"]

    def test_question_report_releases_slot_in_one_version(self):
        store = self.store
        ident = reserve(store, "asks", priority="P1")
        call(store, "bind", ident, "host")
        before = store.read()["version"]
        d, _ = call(store, "report_question", ident, plan_version=7, evidence="which branch?")
        row = d["leases"][ident]
        assert d["version"] == before + 1
        assert row["state"] == "waiting_input" and not row["slot"] and row["usage"] == 0
        assert row["idle_proof"] == "which branch?" and row["report_hash"] is None
        call(store, "reacquire", ident)
        transition(store, ident, "running")
        with self.assertRaises(WorkParallelConflict):
            call(store, "report_question", ident, plan_version=8, evidence="stale")

    def test_completion_report_requires_artifact_evidence_and_revokes_capability(self):
        store = self.store
        token = "worker-token-" + "c" * 40
        d, ident = call(store, "reserve", [candidate("done-item", priority="P1")], scope="scope", now=100,
                        report_hash=hash_worker_token(token))
        call(store, "bind", ident, "host")
        before = store.read()
        for bad in (dict(artifact=None, evidence="ok"), dict(artifact="z" * 64, evidence="ok"),
                    dict(artifact="a" * 64, evidence="")):
            with self.assertRaises(WorkParallelError):
                call(store, "report_completion", ident, plan_version=7, **bad)
        assert store.read() == before
        d, _ = call(store, "report_completion", ident, plan_version=7, artifact="a" * 64, evidence="tests passed")
        row = d["leases"][ident]
        assert d["version"] == before["version"] + 1
        assert row["state"] == "submitted" and not row["slot"] and row["artifact"] == "a" * 64
        assert row["report_hash"] is None and row["evidence"] == "tests passed"
        with self.assertRaises(WorkParallelError):
            call(store, "report_completion", ident, plan_version=7, artifact="a" * 64, evidence="again")
        # Review still proceeds on the released submission; rework needs a slot.
        transition(store, ident, "verifying")
        with self.assertRaises(WorkParallelError):
            transition(store, ident, "running")
        for target in ("reviewing", "accepted", "cleanup_pending"):
            transition(store, ident, target)
        transition(store, ident, "done", cleanup_proof="artifact cleanup verified")
        assert store.read()["leases"][ident]["state"] == "done"

    def test_handoff_report_requires_receipt_and_frees_item_for_replacement(self):
        store = self.store
        token = "worker-token-" + "h" * 40
        d, ident = call(store, "reserve", [candidate("handed", priority="P1")], scope="scope", now=100,
                        report_hash=hash_worker_token(token))
        call(store, "bind", ident, "host")
        before = store.read()
        for bad in (dict(receipt_digest="short", proof="ok"), dict(receipt_digest="b" * 64, proof="")):
            with self.assertRaises(WorkParallelError):
                call(store, "report_handoff", ident, plan_version=7, **bad)
        assert store.read() == before
        d, _ = call(store, "report_handoff", ident, plan_version=7, receipt_digest="b" * 64,
                    proof="receipt stored", now=200)
        row = d["leases"][ident]
        assert d["version"] == before["version"] + 1
        assert row["state"] == "unreported" and not row["slot"] and row["artifact"] is None
        assert row["report_hash"] is None and row["terminal"]["reason"] == "handoff"
        assert "b" * 64 in row["terminal"]["proof"]
        assert reserve(store, "handed", priority="P1") != ident

    def test_persisted_leases_use_only_4eebfdc_fields(self):
        import json
        store = self.store
        allowed = set(("id scope item_id priority created_at role mode plan_version state host_id slot usage "
                       "artifact rework evidence idle_proof cleanup_proof agent_name workspace dispatch_name "
                       "owner report_hash terminal").split())
        first = reserve(store, "one", priority="P1")
        call(store, "bind", first, "host-1", agent_name="work-one")
        call(store, "report_completion", first, plan_version=7, artifact="a" * 64, evidence="done")
        second = reserve(store, "two", priority="P1")
        call(store, "bind", second, "host-2")
        call(store, "report_handoff", second, plan_version=7, receipt_digest="c" * 64, proof="receipt")
        stored = json.loads(store.path.read_text(encoding="utf-8"))["leases"]
        assert stored and all(set(row) <= allowed for row in stored.values())

    def test_beta_lifecycle_times_are_read_and_dropped_on_next_write(self):
        import json
        store = self.store
        ident = reserve(store, "beta", priority="P1")
        doc = json.loads(store.path.read_text(encoding="utf-8"))
        doc["leases"][ident].update(reserved_at=100.5, bound_at=None, state_changed_at=101)
        store.path.write_text(json.dumps(doc), encoding="utf-8")
        assert "reserved_at" not in store.read()["leases"][ident]
        call(store, "bind", ident, "host")
        stored = json.loads(store.path.read_text(encoding="utf-8"))["leases"][ident]
        assert not {"reserved_at", "bound_at", "state_changed_at"} & set(stored)
        doc = json.loads(store.path.read_text(encoding="utf-8"))
        doc["leases"][ident]["state_changed_at"] = "bad"
        store.path.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(WorkParallelError):
            store.read()

    def test_lowering_cap_drains_existing_leases(self):
        store = self.store
        reserve(store, "one", priority="P1")
        reserve(store, "two", priority="P1")
        call(store, "configure", dict(priority_limits=dict(P1=1)), capability_ready=True)
        assert len([r for r in store.read()["leases"].values() if r["slot"]]) == 2
        with self.assertRaises(WorkParallelError):
            reserve(store, "three", priority="P1")


    def test_ledger_written_with_a_device_cap_migrates_and_keeps_leases(self):
        import json
        store = self.store
        ident = reserve(store, "kept", priority="P2")
        doc = json.loads(store.path.read_text(encoding="utf-8"))
        limits = doc["policy"].pop("priority_limits")
        assert limits == dict(P1=5, P2=3, P3=1, P4=1, P5=1)
        doc["policy"].update(max_active=8, p1_reserved=3)
        store.path.write_text(json.dumps(doc), encoding="utf-8")
        migrated = store.read()
        assert migrated["policy"]["priority_limits"] == dict(P1=5, P2=3, P3=1, P4=1, P5=1)
        assert "max_active" not in migrated["policy"] and ident in migrated["leases"]
        # The next write persists the migrated policy.
        call(store, "configure", dict(priority_limits=dict(P2=2)), capability_ready=True)
        stored = json.loads(store.path.read_text(encoding="utf-8"))["policy"]
        assert stored["priority_limits"]["P2"] == 2 and "max_active" not in stored

    def test_priority_limits_are_bounded(self):
        store = self.store
        for bad in (dict(P1=0), dict(P2=17), dict(P6=1), dict(P3="2")):
            with self.assertRaises(WorkParallelError):
                call(store, "configure", dict(priority_limits=bad), capability_ready=True)


if __name__ == "__main__":
    unittest.main()
