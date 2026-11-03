"""Isolated focused tests; no router/session processes."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from ciel_runtime_support.work_parallel import WorkParallelStore, WorkParallelError, WorkParallelConflict


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
        # Parallel dispatch is on by default; admission is slot-based only.
        assert p["enabled"] and p["test_override"] and p["max_active"] == 3
        assert p["p1_reserved"] == 1 and p["max_rework"] == 2 and p["launches_per_minute"] == 4
        assert p["thresholds"] == dict(P2=1.0, P3=1.0, P4=1.0, P5=1.0)
        assert "와키에서 최근 미결 아이템" in p["discovery_text"]
        with self.assertRaises(WorkParallelError):
            s.configure(dict(enabled=True), capability_ready=False, expected_version=0)
        assert s.read()["version"] == 0
        assert reserve(s)


    def test_order(self):
        store = self.store
        rows = [candidate("old", created_at=1), candidate("new", created_at=20), candidate("blocked", priority="P1", dependencies_done=False)]
        d, ident = call(store, "reserve", rows, scope="s", now=100)
        assert d["leases"][ident]["item_id"] == "new"
        rows = [candidate("p2", created_at=30), candidate("p1", priority="P1", created_at=0)]
        d, ident = call(store, "reserve", rows, scope="s", now=100)
        assert d["leases"][ident]["item_id"] == "p1"


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


    def test_slots_are_the_only_admission_gate(self):
        store = self.store
        # P2-P5 may use max_active - p1_reserved concurrent slots; usage is not consulted.
        reserve(store, "a", usage=.99)
        reserve(store, "b", usage=.99)
        with self.assertRaises(WorkParallelError):
            reserve(store, "c", usage=0)
        reserve(store, "urgent", priority="P1")
        with self.assertRaises(WorkParallelError):
            reserve(store, "urgent2", priority="P1")
        assert sum(r["slot"] for r in store.read()["leases"].values()) == 3


    def test_shared_cap(self):
        store = self.store
        reserve(store, "worker")
        other = WorkParallelStore(store.path)
        reserve(other, "review", role="reviewer")
        with self.assertRaises(WorkParallelError):
            reserve(other, "resume", role="resume")
        with self.assertRaises(WorkParallelError):
            reserve(other, "fresh", priority="P1", mode="fresh")
        reserve(other, "urgent", priority="P1")
        with self.assertRaises(WorkParallelError):
            reserve(other, "urgent2", priority="P1")
        assert sum(r["slot"] for r in store.read()["leases"].values()) == 3


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


    def test_wait_idle_resume(self):
        store = self.store
        ident = reserve(store)
        call(store, "bind", ident, "host")
        transition(store, ident, "waiting_input")
        before = store.read()
        d, _ = call(store, "reconcile", ident, host_state="unknown")
        assert d == before and d["leases"][ident]["slot"]
        with self.assertRaises(WorkParallelError):
            call(store, "reconcile", ident, host_state="idle")
        call(store, "reconcile", ident, host_state="idle", proof="host idle observed")
        with self.assertRaises(WorkParallelError):
            transition(store, ident, "running")
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
        call(store, "configure", dict(max_active=1), capability_ready=True)
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


    def test_single_slot_allows_non_p1(self):
        tmp_path = self.tmp_path
        s = WorkParallelStore(tmp_path / "single.json")
        call(s, "configure", dict(enabled=True, max_active=1), capability_ready=True)
        ident = reserve(s)
        assert s.read()["leases"][ident]["priority"] == "P2"


    def test_lowering_cap_drains_existing_leases(self):
        store = self.store
        reserve(store, "one", priority="P1")
        reserve(store, "two", priority="P1")
        call(store, "configure", dict(max_active=1), capability_ready=True)
        assert len([r for r in store.read()["leases"].values() if r["slot"]]) == 2
        with self.assertRaises(WorkParallelError):
            reserve(store, "three", priority="P1")


if __name__ == "__main__":
    unittest.main()
