"""Production MCP boundary tests for P0 launch lifecycle."""
import importlib.util
import json
import os
import subprocess
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "ciel_runtime_support"
PACKAGE = "ciel_runtime_support"


def load(name):
    # Import the installed package module itself. Re-executing a file under
    # the same sys.modules name gives later tests a second copy of the store
    # and error classes than the modules that already imported it.
    return importlib.import_module(PACKAGE + "." + name)


parallel = load("work_parallel")
p0 = load("work_parallel_p0")
receipts = load("work_parallel_receipts")
key_module = load("work_p0_assignment_key")
m = load("work_parallel_mcp")
WorkParallelStore = parallel.WorkParallelStore
WorkParallelError = parallel.WorkParallelError
p0_store = p0.p0_store

SECRET = "p0-event-secret-" * 3
SCOPE = "claude:p0-events"


class Board:
    def __init__(self):
        self.doc = {"version": 1, "phase": "planning", "active_root": "root",
                    "focus_ids": [], "nodes": [
            {"id": "root", "parent_id": None, "title": "goal", "status": "pending",
             "note": "", "progress": "", "evidence": "", "waiting_on": "", "ref": "",
             "urgent": False, "updated_at": 1.0, "created_at": 1.0,
             "phase": "planning", "priority": "P1"},
            {"id": "item", "parent_id": "root", "title": "item", "status": "pending",
             "note": "", "progress": "", "evidence": "", "waiting_on": "", "ref": "",
             "urgent": False, "updated_at": 1.0, "created_at": 1.0},
        ]}

    def read(self):
        return self.doc


class P0EventTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.store = WorkParallelStore(self.tmp / "work-parallel.json")
        self.board = Board()
        self.config = {"work_parallel_host": {"token": SECRET, "capability_ready": True}}
        side = p0_store(self.store)
        side.activate_root(SCOPE, "root", 1.0, set_by="user", expected_version=0, now=1.0)

    def call(self, event, host=None, **args):
        args.setdefault("expected_version", self.store.read()["version"])
        args["event"] = event
        args["_host"] = {"token": SECRET, **(host or {})}
        with patch.object(m, "ledger", return_value=self.store):
            return json.loads(m.run("work_parallel_event", args, self.board, SCOPE,
                                    self.config, None))

    def reserve(self):
        return self.call("p0_reserve_next", {"fork": True}, role="worker")

    def test_reserve_assignment_spawning_bind_status(self):
        reserved = self.reserve()
        self.assertEqual("ready", reserved["dispatch"])
        self.assertRegex(reserved["intent_id"], r"^[0-9a-f]{32}$")
        self.assertEqual("work-p0-" + reserved["intent_id"], reserved["dispatch_name"])
        self.assertNotIn("worker_token", reserved)
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        one = self.call("p0_launch_assignment", {"internal": True}, **args)
        two = self.call("p0_launch_assignment", {"internal": True}, **args)
        self.assertEqual(one["worker_token"], two["worker_token"])
        spawned = self.call("p0_launch_spawning", {"internal": True}, **args)
        self.assertEqual("spawning", spawned["state"])
        status = self.call("p0_launch_status", {"internal": True})
        self.assertEqual("spawning", status["intents"][0]["state"])
        self.assertEqual("ready", status["assignment_key"])
        self.assertTrue(status["spawn_allowed"])
        self.call("bind", {"host_id": "agent", "agent_name": reserved["dispatch_name"]},
                  lease_id=reserved["lease_id"])
        intent = p0_store(self.store).read()["intents"][reserved["intent_id"]]
        self.assertEqual("bound", intent["state"])

    def test_full_p0_journal_retires_old_cancelled_intent_before_reserving(self):
        side = p0_store(self.store)
        with patch.object(p0, "MAX_INTENTS", 2):
            for stamp in (2.0, 3.0):
                _, row = side.create_intent({"scope": SCOPE, "root_id": "root", "root_created_at": 1.0,
                                             "item_id": "item", "continuation": "initial",
                                             "predecessor_lease_id": None},
                                            expected_version=side.read()["version"], now=stamp)
                side.transition_intent(row["intent_id"], "cancelled", expected_version=side.read()["version"], now=stamp)
            before = set(side.read()["intents"])
            reserved = self.reserve()
            after = side.read()["intents"]
            self.assertEqual("ready", reserved["dispatch"])
            self.assertEqual(2, len(after))
            self.assertIn(row["intent_id"], after)
            self.assertEqual(1, len(before & set(after)))

    def test_a_p0_fork_row_says_it_runs_as_p0(self):
        # Celly 2026-11-17: the lease keeps the goal's canonical P1 and the panes
        # labelled the running P0 fork P1. The row carries the effective priority.
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        self.call("p0_launch_assignment", {"internal": True}, **args)
        self.call("p0_launch_spawning", {"internal": True}, **args)
        self.call("bind", {"host_id": "agent", "agent_name": reserved["dispatch_name"]},
                  lease_id=reserved["lease_id"])
        view = m.project(self.store, SCOPE, self.board.doc, host_view=True)
        rows = [row for group in view["forks"].values() if isinstance(group, list) for row in group
                if isinstance(row, dict) and row.get("lease_id") == reserved["lease_id"]]
        self.assertEqual([("P1", "P0")], [(row["priority"], row["effective_priority"]) for row in rows])

    def test_a_terminal_lease_does_not_keep_its_old_p0_intent_open(self):
        # Andy: orphaned n2471/n2452 kept a bound intent; reserve reused the
        # old run and failed "assignment already exists with a different manifest".
        first = self.reserve()
        launch = {key: first[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        self.call("p0_launch_spawning", {"internal": True}, **launch)
        self.call("bind", {"host_id": "agent", "agent_name": first["dispatch_name"]},
                  lease_id=first["lease_id"])
        self.store._change(lambda doc: doc["leases"][first["lease_id"]].update(
            state="orphaned", slot=False, usage=0, report_hash=None, idle_proof="owner ended",
            evidence="owner ended", terminal={"reason": "orphaned", "observed_at": 10.0, "proof": "owner ended"}),
            self.store.read()["version"])
        old_run = m.coordination(self.store).read()["runs"][first["lease_id"]]
        second = self.reserve()
        self.assertNotEqual(first["lease_id"], second["lease_id"])
        side = p0_store(self.store).read()
        self.assertEqual("terminal", side["intents"][first["intent_id"]]["state"])
        self.assertEqual("reserved", side["intents"][second["intent_id"]]["state"])
        self.assertEqual(old_run, m.coordination(self.store).read()["runs"][first["lease_id"]])

    def test_closing_an_ended_intent_with_a_reacquire_send_keeps_the_p0_store_valid(self):
        # Celly 2026-11-20 n1041: the ended lease's bound intent had an open
        # reacquire send; closing the intent was refused as "P0 reacquire parent
        # intent invalid", so the next P0 launch of the item failed every time.
        first = self.reserve()
        launch = {key: first[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        self.call("p0_launch_spawning", {"internal": True}, **launch)
        self.call("bind", {"host_id": "agent", "agent_name": first["dispatch_name"]}, lease_id=first["lease_id"])
        side = p0_store(self.store)
        intent = side.read()["intents"][first["intent_id"]]
        side._change(lambda state: state["sends"].__setitem__("5" * 32, {
            "send_id": "5" * 32, "parent_intent_id": intent["intent_id"], "lease_id": first["lease_id"],
            "scope": intent["scope"], "root_id": intent["root_id"], "item_id": intent["item_id"],
            "host_id": "agent", "native_name": first["dispatch_name"], "state": "slot_acquired",
            "send_receipt": "a" * 64, "child_generation": 1, "slot_before_version": 1, "slot_after_version": 2,
            "rollback_receipt_digest": None, "outcome": None, "operation_digest": None,
            "created_at": 10.0, "updated_at": 10.0}), side.read()["version"])
        self.store._change(lambda doc: doc["leases"][first["lease_id"]].update(
            state="orphaned", slot=False, usage=0, report_hash=None, idle_proof="owner ended",
            evidence="owner ended", terminal={"reason": "orphaned", "observed_at": 10.0, "proof": "owner ended"}),
            self.store.read()["version"])
        second = self.reserve()
        self.assertNotEqual(first["lease_id"], second["lease_id"])
        after = p0_store(self.store).read()
        self.assertEqual("terminal", after["intents"][first["intent_id"]]["state"])
        self.assertEqual([], [row for row in after["sends"].values() if row["lease_id"] == first["lease_id"]])

    def test_a_cancelled_intent_does_not_block_a_new_intent_on_the_same_replacement_lease(self):
        # Celly 2026-11-20 (n1069 n1070 n1074 n1097): a stranded recovery's P0
        # intent was cancelled when the orphan sweep released its unbound
        # lease. The re-reserved recovery reuses the same deterministic lease
        # id, and adopting a new intent was refused as "multiple original P0
        # intents for one lease", so four reservations held P1 slots forever.
        side = p0_store(self.store)
        def intent(intent_id, state, lease_id):
            return {"intent_id": intent_id, "scope": SCOPE, "root_id": "root", "root_created_at": 1.0,
                    "item_id": "item", "continuation": "stranded_recovery", "predecessor_lease_id": "old",
                    "dispatch_name": "work-p0-" + intent_id, "state": state, "lease_id": lease_id,
                    "dispatch_without_approval": True, "created_at": 2.0, "updated_at": 2.0}
        lease = "r" * 32
        side._change(lambda state: state["intents"].update({
            "a" * 32: intent("a" * 32, "cancelled", lease), "b" * 32: intent("b" * 32, "reserved", lease)}),
            side.read()["version"])
        self.assertEqual({lease}, side.active_lease_ids())
        # Two live intents on one lease are still refused.
        with self.assertRaisesRegex(WorkParallelError, "multiple original P0 intents for one lease"):
            side._change(lambda state: state["intents"].__setitem__("c" * 32, intent("c" * 32, "reserved", lease)),
                         side.read()["version"])

    def test_a_replacement_run_on_an_unapproved_p0_goal_is_recorded_with_its_p0_intent(self):
        # Celly 2026-11-20 (n1097 under P0 goal n1006, planning): the stranded
        # recovery's coordination run was refused as "stale root approval"
        # because, unlike the initial P0 launch, it carried no P0 authorization.
        reserved = self.reserve()
        lease_id = reserved["lease_id"]
        coord = m.coordination(self.store)
        run = coord.read()["runs"][lease_id]
        coord._change(lambda state: state["runs"].pop(lease_id), coord.read()["version"])
        manifest = {key: run[key] for key in ("item_id", "root_id", "priority", "role", "generation",
                                              "approval_generation", "assigned_todos", "fingerprint", "workspace")}
        manifest["created_at"] = run["reserved_at"]
        with self.assertRaisesRegex(WorkParallelError, "stale root approval"):
            coord.record_assignment(SCOPE, lease_id, manifest, expected_version=coord.read()["version"])
        authorization = m._replacement_p0_authorization(self.store, SCOPE, lease_id, "item")
        self.assertTrue(authorization["dispatch_without_approval"])
        coord.record_assignment(SCOPE, lease_id, manifest, expected_version=coord.read()["version"],
                                **authorization)
        self.assertEqual("reserved", coord.read()["runs"][lease_id]["state"])
        self.assertEqual({}, m._replacement_p0_authorization(self.store, SCOPE, "x" * 32, "item"))

    def test_a_rework_of_a_rework_result_gets_its_own_p0_intent(self):
        # Andy 2026-11-20 (n2471 gen-3): the result under review was itself a
        # rework, launched under an open "rework" intent that stays bound until
        # its lease ends. Main's next rework matched that intent and every
        # roll-forward was refused as "open P0 intent facts changed".
        reserved = self.reserve()
        launch = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        self.call("p0_launch_spawning", {"internal": True}, **launch)
        self.call("bind", {"host_id": "agent", "agent_name": reserved["dispatch_name"]},
                  lease_id=reserved["lease_id"])
        side = p0_store(self.store)
        side._change(lambda state: state["intents"][reserved["intent_id"]].update(
            continuation="rework", predecessor_lease_id="p" * 32), side.read()["version"])
        self.store._change(lambda doc: doc["leases"][reserved["lease_id"]].update(
            state="submitted", slot=False, usage=0, artifact="a" * 64, evidence="done",
            idle_proof="done", report_hash=None), self.store.read()["version"])
        with patch.object(m, "ledger", return_value=self.store):
            intent = m._p0_continuation_intent(self.store, self.board, SCOPE, "item", "rework",
                                               reserved["lease_id"])
        after = p0_store(self.store).read()["intents"]
        self.assertEqual(("intent", reserved["lease_id"]), (intent["state"], intent["predecessor_lease_id"]))
        self.assertEqual("terminal", after[reserved["intent_id"]]["state"])

    def test_replaying_a_prepared_assignment_preserves_its_workspace_and_run(self):
        first = self.reserve()
        before = m.coordination(self.store).read()["runs"][first["lease_id"]]
        # A scope edit between reservation and spawn must not rewrite this run.
        self.board.doc["nodes"][1]["note"] = "scope edited after reservation"
        replay = self.call("reserve", {"fork": True}, item_id="item", priority="P1")
        self.assertEqual(first["lease_id"], replay["lease_id"])
        self.assertEqual(before, m.coordination(self.store).read()["runs"][first["lease_id"]])

    def test_explicit_deny_cancels_unknown_spawn_does_not(self):
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        self.call("p0_launch_spawning", {"internal": True}, **args)
        # No event for throw/no agent id: durable state remains spawning.
        self.assertEqual("spawning", p0_store(self.store).read()["intents"][reserved["intent_id"]]["state"])
        denied = self.call("p0_launch_denied", {"internal": True, "proof": "native deny"}, **args)
        self.assertEqual({"state": "cancelled", "released": True}, denied)
        replay = self.call("p0_launch_denied", {"internal": True, "proof": "native deny"}, **args)
        self.assertEqual(denied, replay)
        self.assertNotIn(reserved["lease_id"], self.store.read()["leases"])

    def test_crash_after_ledger_reserve_is_adopted_by_public_replay(self):
        side = p0_store(self.store)
        _, intent = side.create_intent({
            "scope": SCOPE, "root_id": "root", "root_created_at": 1.0,
            "item_id": "item", "continuation": "initial", "predecessor_lease_id": None},
            expected_version=side.read()["version"], now=2.0)
        # This fixture simulates a crash after the durable intent, so the key
        # that precedes first intent creation must also already exist.
        key_module.P0AssignmentKeyStore.beside_ledger(self.store.path).ensure_before_first_intent()
        candidate = m.candidate(self.board.read(), "item", "P1", "worker")
        _, lease_id = self.store.reserve(
            [candidate], scope=SCOPE, expected_version=self.store.read()["version"],
            dispatch_name=intent["dispatch_name"], admission_bypass="p0", p0_intent=intent)
        replay = self.reserve()
        self.assertEqual(intent["intent_id"], replay["intent_id"])
        self.assertEqual(lease_id, replay["lease_id"])
        self.assertEqual(1, len(self.store.read()["leases"]))
        self.assertEqual("reserved", side.read()["intents"][intent["intent_id"]]["state"])

    def test_sidecar_adopt_unknown_keeps_exact_reservation_for_replay(self):
        side = p0_store(self.store)
        original = side.adopt_reserved
        calls = {"count": 0}

        def fail_after_reserve(*args, **kwargs):
            calls["count"] += 1
            # Pre-reserve adoption sees no matching lease; post-reserve outcome
            # becomes unknown and must not trigger release_unbound.
            if calls["count"] == 1:
                return original(*args, **kwargs)
            raise OSError("sidecar unavailable")

        with patch.object(side.__class__, "adopt_reserved", side_effect=fail_after_reserve):
            with self.assertRaisesRegex(WorkParallelError, "outcome unknown; recovery required"):
                self.reserve()
        ledger = self.store.read()
        self.assertEqual(1, len(ledger["leases"]))
        lease = next(iter(ledger["leases"].values()))
        self.assertEqual("reserved", lease["state"])
        self.assertIsNone(lease["host_id"])
        intent = next(iter(side.read()["intents"].values()))
        self.assertEqual(intent["dispatch_name"], lease["dispatch_name"])
        replay = self.reserve()
        self.assertEqual(lease["id"], replay["lease_id"])
        self.assertEqual(1, len(self.store.read()["leases"]))

    def test_known_coordination_failure_releases_p0_reservation_and_cancels_intent(self):
        with patch.object(receipts.WorkCoordinationStore, "record_assignment",
                          side_effect=OSError("coordination unavailable")):
            with self.assertRaisesRegex(WorkParallelError,
                                        "coordination persistence failed; reservation released"):
                self.reserve()
        self.assertEqual({}, self.store.read()["leases"])
        intent = next(iter(p0_store(self.store).read()["intents"].values()))
        self.assertEqual("cancelled", intent["state"])

    def test_workspace_unknown_keeps_p0_reservation_and_intent(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "fixture"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email",
                        "fixture@example.invalid"], check=True)
        (repo / "base.txt").write_text("base", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "base.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"], check=True,
                       capture_output=True)
        original_coordination = m.coordination

        class CoordinationProxy:
            def __init__(self, actual):
                self.actual = actual

            def __getattr__(self, name):
                return getattr(self.actual, name)

            def attach_workspace(self, *_args, **_kwargs):
                raise OSError("coordination workspace unavailable")

        with patch.object(m, "coordination",
                          side_effect=lambda store=None: CoordinationProxy(
                              original_coordination(store))):
            with self.assertRaisesRegex(WorkParallelError,
                                        "P0 workspace.*unknown; recovery required"):
                self.call("p0_reserve_next", {"fork": True, "workspace_repo": str(repo)},
                          role="worker")
        lease = next(iter(self.store.read()["leases"].values()))
        intent = next(iter(p0_store(self.store).read()["intents"].values()))
        self.assertEqual("reserved", lease["state"])
        self.assertEqual(lease["id"], intent["lease_id"])
        self.assertIsNotNone(lease.get("workspace"))
        replay = self.reserve()
        self.assertEqual(lease["id"], replay["lease_id"])
        self.assertEqual(1, len(self.store.read()["leases"]))

    def test_unlaunched_p0_reservation_is_released_once_its_root_is_no_longer_p0(self):
        # Celly 2026-11-22 n1326: a P0 reservation kept for replay (its
        # coordination workspace attach failed) lost its P0 marker four minutes
        # later. The P0 pump no longer saw the item, the ordinary lane saw it
        # fenced by that lease, and the P1 slot stayed reserved with no fork.
        repo = self.tmp / "repo"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "fixture"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
        (repo / "base.txt").write_text("base", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "base.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True)
        original_coordination = m.coordination

        class CoordinationProxy:
            def __init__(self, actual):
                self.actual = actual

            def __getattr__(self, name):
                return getattr(self.actual, name)

            def attach_workspace(self, *_args, **_kwargs):
                raise OSError("coordination workspace unavailable")

        with patch.object(m, "coordination",
                          side_effect=lambda store=None: CoordinationProxy(original_coordination(store))):
            with self.assertRaisesRegex(WorkParallelError, "P0 workspace.*unknown; recovery required"):
                self.call("p0_reserve_next", {"fork": True, "workspace_repo": str(repo)}, role="worker")
        lease = next(iter(self.store.read()["leases"].values()))
        intent = next(iter(p0_store(self.store).read()["intents"].values()))
        self.assertEqual(("reserved", "reserved"), (lease["state"], intent["state"]))
        side = p0_store(self.store)
        side.prepare_clear(SCOPE, "root", 1.0, "P1", self.board.doc["version"], set_by="user",
                           expected_version=side.read()["version"])
        with self.assertRaisesRegex(WorkParallelError, "no eligible candidate"):
            self.reserve()
        self.assertNotIn(lease["id"], self.store.read()["leases"])
        self.assertNotIn(lease["id"], receipts.coordination_store(self.store).read()["runs"])
        intent = p0_store(self.store).read()["intents"][intent["intent_id"]]
        self.assertEqual(("intent", None), (intent["state"], intent["lease_id"]))

    def test_a_spawning_p0_launch_is_not_released_when_its_root_is_cleared(self):
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        self.call("p0_launch_assignment", {"internal": True}, **args)
        self.call("p0_launch_spawning", {"internal": True}, **args)
        side = p0_store(self.store)
        side.prepare_clear(SCOPE, "root", 1.0, "P1", self.board.doc["version"], set_by="user",
                           expected_version=side.read()["version"])
        with self.assertRaisesRegex(WorkParallelError, "no eligible candidate"):
            self.reserve()
        self.assertEqual("reserved", self.store.read()["leases"][reserved["lease_id"]]["state"])
        self.assertEqual("spawning", side.read()["intents"][reserved["intent_id"]]["state"])

    def test_p0_reacquire_accepts_a_bound_rework_fork_name(self):
        # Andy 2026-11-21 (n2453/n2042): the P0 intent authorized a rework
        # replacement that native bound as work-rework-<lease>-a1. Reacquire
        # compared that exact native name with the P0 dispatch_name and refused.
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        self.call("p0_launch_spawning", {"internal": True}, **args)
        rework_name = "work-rework-" + reserved["lease_id"] + "-a1"
        self.call("bind", {"host_id": "agent", "agent_name": rework_name}, lease_id=reserved["lease_id"])
        side = p0_store(self.store)
        side._change(lambda state: state["intents"][reserved["intent_id"]].update(
            continuation="rework", predecessor_lease_id="p" * 32), side.read()["version"])
        self.store._change(lambda doc: doc["leases"][reserved["lease_id"]].update(
            state="waiting_input", slot=False, usage=0, idle_proof="waiting"), self.store.read()["version"])
        result = self.call("p0_reacquire", {}, lease_id=reserved["lease_id"])
        self.assertEqual(rework_name, result["native_name"])
        self.assertEqual(reserved["intent_id"], result["parent_intent_id"])

    def test_p0_reacquire_preserves_same_lease_and_name(self):
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        self.call("p0_launch_spawning", {"internal": True}, **args)
        self.call("bind", {"host_id": "agent", "agent_name": reserved["dispatch_name"]},
                  lease_id=reserved["lease_id"])
        lease = self.store.read()["leases"][reserved["lease_id"]]
        def waiting(doc):
            row = doc["leases"][lease["id"]]
            row.update(state="waiting_input", slot=False, usage=0, idle_proof="waiting")
        self.store._change(waiting, self.store.read()["version"])
        before = set(self.store.read()["leases"])
        result = self.call("p0_reacquire", {}, lease_id=lease["id"])
        self.assertEqual(lease["id"], result["lease_id"])
        self.assertEqual(reserved["dispatch_name"], result["native_name"])
        self.assertEqual("slot_acquired", result["state"])
        self.assertRegex(result["send_id"], r"^[0-9a-f]{32}$")
        self.assertRegex(result["send_receipt"], r"^[0-9a-f]{64}$")
        self.assertEqual(before, set(self.store.read()["leases"]))
        key_path = key_module.P0AssignmentKeyStore.beside_ledger(self.store.path).path
        key_path.unlink()
        with self.assertRaisesRegex(Exception, "recovery required; reacquire blocked \\(missing\\)"):
            self.call("p0_reacquire", {}, lease_id=lease["id"])
        self.assertFalse(key_path.exists(), "reacquire refusal must not recreate the key")
        # Restore the exact key for the already-authorized child completion; this
        # test is about blocking a new continuation before its write.
        key_path.write_bytes(b"a" * 64)
        complete = self.call("p0_reacquire_complete", {"internal": True},
                             lease_id=lease["id"], parent_intent_id=result["parent_intent_id"],
                             send_id=result["send_id"], child_generation=result["child_generation"],
                             send_receipt=result["send_receipt"], outcome="sent")
        self.assertEqual({"state": "delivered", "outcome": "sent", "terminal": True,
                          "recovery_required": False}, complete)

    def test_reacquire_failed_rolls_back_and_allows_new_child(self):
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        self.call("p0_launch_spawning", {"internal": True}, **args)
        self.call("bind", {"host_id": "agent", "agent_name": reserved["dispatch_name"]},
                  lease_id=reserved["lease_id"])
        self.store._change(
            lambda doc: doc["leases"][reserved["lease_id"]].update(
                state="waiting_input", slot=False, usage=0, idle_proof="waiting"),
            self.store.read()["version"])
        child = self.call("p0_reacquire", {}, lease_id=reserved["lease_id"])
        failed = self.call("p0_reacquire_complete", {"internal": True},
                           lease_id=reserved["lease_id"],
                           parent_intent_id=child["parent_intent_id"], send_id=child["send_id"],
                           child_generation=child["child_generation"],
                           send_receipt=child["send_receipt"], outcome="failed")
        self.assertTrue(failed["slot_rolled_back"])
        replay = self.call("p0_reacquire_complete", {"internal": True},
                           lease_id=reserved["lease_id"],
                           parent_intent_id=child["parent_intent_id"], send_id=child["send_id"],
                           child_generation=child["child_generation"],
                           send_receipt=child["send_receipt"], outcome="failed")
        self.assertEqual(failed, replay)
        with self.assertRaisesRegex(WorkParallelError, "outcome changed"):
            self.call("p0_reacquire_complete", {"internal": True},
                      lease_id=reserved["lease_id"], parent_intent_id=child["parent_intent_id"],
                      send_id=child["send_id"], child_generation=child["child_generation"],
                      send_receipt=child["send_receipt"], outcome="sent")
        self.assertFalse(self.store.read()["leases"][reserved["lease_id"]]["slot"])
        later = self.call("p0_reacquire", {}, lease_id=reserved["lease_id"])
        self.assertNotEqual(child["send_id"], later["send_id"])
        self.assertEqual(child["child_generation"] + 1, later["child_generation"])

    def test_reacquire_uncertain_blocks_new_child_and_replays_same_completion(self):
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        self.call("p0_launch_spawning", {"internal": True}, **args)
        self.call("bind", {"host_id": "agent", "agent_name": reserved["dispatch_name"]},
                  lease_id=reserved["lease_id"])
        self.store._change(
            lambda doc: doc["leases"][reserved["lease_id"]].update(
                state="waiting_input", slot=False, usage=0, idle_proof="waiting"),
            self.store.read()["version"])
        child = self.call("p0_reacquire", {}, lease_id=reserved["lease_id"])
        payload = dict(lease_id=reserved["lease_id"], parent_intent_id=child["parent_intent_id"],
                       send_id=child["send_id"], child_generation=child["child_generation"],
                       send_receipt=child["send_receipt"], outcome="uncertain")
        first = self.call("p0_reacquire_complete", {"internal": True}, **payload)
        replay = self.call("p0_reacquire_complete", {"internal": True}, **payload)
        self.assertEqual(first, replay)
        self.assertTrue(first["recovery_required"])
        same = self.call("p0_reacquire", {}, lease_id=reserved["lease_id"])
        self.assertEqual(child["send_id"], same["send_id"])
        self.assertTrue(same["recovery_required"])

    def test_reacquire_settles_an_open_child_the_worker_reported_after(self):
        # Celly 2026-11-20 n1143: the resumed worker reported a question after
        # its P0 send, but no completion was recorded; every later reacquire
        # returned that old child and main's resume failed with "no active slot".
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        self.call("p0_launch_spawning", {"internal": True}, **args)
        self.call("bind", {"host_id": "agent", "agent_name": reserved["dispatch_name"]},
                  lease_id=reserved["lease_id"])
        def waiting(doc):
            doc["leases"][reserved["lease_id"]].update(
                state="waiting_input", slot=False, usage=0, idle_proof="waiting")
        self.store._change(waiting, self.store.read()["version"])
        old = self.call("reacquire", {}, lease_id=reserved["lease_id"])
        self.assertTrue(old["slot_reacquired"])
        coord = m.coordination(self.store)
        with patch.object(receipts.time, "time", return_value=p0_store(self.store).read()["sends"][old["send_id"]]["created_at"] + 1):
            coord.persist_receipt(reserved["lease_id"], "question",
                                  {"question": "q", "blocking_todos": ["item"], "decision_needed": "q"},
                                  expected_version=coord.read()["version"])
        self.store._change(waiting, self.store.read()["version"])
        fresh = self.call("reacquire", {}, lease_id=reserved["lease_id"])
        self.assertTrue(fresh.get("slot_reacquired"), fresh)
        self.assertNotEqual(old["send_id"], fresh["send_id"])
        self.assertEqual(old["child_generation"] + 1, fresh["child_generation"])
        sends = p0_store(self.store).read()["sends"]
        self.assertEqual("delivered", sends[old["send_id"]]["state"])
        self.assertTrue(self.store.read()["leases"][reserved["lease_id"]]["slot"])

    def test_reacquire_keeps_an_open_child_without_a_later_worker_report(self):
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        self.call("p0_launch_spawning", {"internal": True}, **args)
        self.call("bind", {"host_id": "agent", "agent_name": reserved["dispatch_name"]},
                  lease_id=reserved["lease_id"])
        def waiting(doc):
            doc["leases"][reserved["lease_id"]].update(
                state="waiting_input", slot=False, usage=0, idle_proof="waiting")
        self.store._change(waiting, self.store.read()["version"])
        old = self.call("reacquire", {}, lease_id=reserved["lease_id"])
        self.store._change(waiting, self.store.read()["version"])
        same = self.call("reacquire", {}, lease_id=reserved["lease_id"])
        self.assertEqual(old["send_id"], same["send_id"])
        self.assertNotIn("slot_reacquired", same)
        self.assertFalse(self.store.read()["leases"][reserved["lease_id"]]["slot"])

    def test_reacquire_mismatched_existing_name_refuses(self):
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        self.call("p0_launch_spawning", {"internal": True}, **args)
        self.call("bind", {"host_id": "agent", "agent_name": reserved["dispatch_name"]},
                  lease_id=reserved["lease_id"])
        def waiting(doc):
            row = doc["leases"][reserved["lease_id"]]
            row.update(state="waiting_input", slot=False, usage=0, idle_proof="waiting")
        self.store._change(waiting, self.store.read()["version"])
        self.store._change(
            lambda doc: doc["leases"][reserved["lease_id"]].update(agent_name="changed-name"),
            self.store.read()["version"])
        with self.assertRaisesRegex(WorkParallelError, "parent authority|identity changed"):
            self.call("p0_reacquire", {}, lease_id=reserved["lease_id"])

    def _spawning_with_owner(self):
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        self.call("p0_launch_spawning", {"internal": True}, **args)
        self.store._change(
            lambda doc: doc["leases"][reserved["lease_id"]].update(
                owner={"session_pid": 12345, "identity": "birth-1"}),
            self.store.read()["version"])
        return reserved, args

    def test_absence_requires_complete_list_and_dead_or_reused_owner(self):
        reserved, args = self._spawning_with_owner()
        with self.assertRaisesRegex(WorkParallelError, "complete P0 absence"):
            self.call("p0_launch_absent", {"internal": True,
                      "absent_dispatch_name": reserved["dispatch_name"]}, **args)
        with self.assertRaisesRegex(WorkParallelError, "same-process"):
            self.call("p0_launch_absent", {"internal": True, "session_pid": 12345,
                      "session_process_identity": "birth-1",
                      "absent_dispatch_name": reserved["dispatch_name"],
                      "absent_from_complete_list": True}, **args)
        with patch.object(m, "process_identity", return_value="birth-1"):
            with self.assertRaisesRegex(WorkParallelError, "another live"):
                self.call("p0_launch_absent", {"internal": True, "session_pid": 999,
                          "absent_dispatch_name": reserved["dispatch_name"],
                          "absent_from_complete_list": True}, **args)
        with patch.object(m, "process_identity", return_value=None):
            result = self.call("p0_launch_absent", {"internal": True, "session_pid": 999,
                      "absent_dispatch_name": reserved["dispatch_name"],
                      "absent_from_complete_list": True}, **args)
        self.assertEqual({"state": "cancelled", "released": True}, result)

    def test_absence_pid_reuse_is_positive_terminal_proof(self):
        reserved, args = self._spawning_with_owner()
        with patch.object(m, "process_identity", return_value="birth-2"):
            result = self.call("p0_launch_absent", {"internal": True, "session_pid": 999,
                      "absent_dispatch_name": reserved["dispatch_name"],
                      "absent_from_complete_list": True}, **args)
        self.assertTrue(result["released"])

    def test_absence_unknown_owner_fails_closed(self):
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        self.call("p0_launch_spawning", {"internal": True}, **args)
        with self.assertRaisesRegex(WorkParallelError, "owner unavailable"):
            self.call("p0_launch_absent", {"internal": True,
                      "absent_dispatch_name": reserved["dispatch_name"],
                      "absent_from_complete_list": True}, **args)

    def test_assignment_key_restart_missing_and_identity_domain(self):
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        first = self.call("p0_launch_assignment", {"internal": True}, **args)
        # New key-store instance (process restart) returns the identical capability.
        second = self.call("p0_launch_assignment", {"internal": True}, **args)
        self.assertEqual(first["worker_token"], second["worker_token"])
        key_path = key_module.P0AssignmentKeyStore.beside_ledger(self.store.path).path
        key_raw = key_path.read_bytes()
        token_raw = first["worker_token"].encode("ascii")
        for path in self.tmp.rglob("*"):
            if path.is_file() and path != key_path:
                raw = path.read_bytes()
                self.assertNotIn(key_raw, raw, str(path))
                self.assertNotIn(token_raw, raw, str(path))
        key_path.unlink()
        status = self.call("p0_launch_status", {"internal": True})
        self.assertEqual("recovery_required", status["assignment_key"])
        self.assertEqual("missing", status["assignment_key_reason"])
        self.assertTrue(status["scheduling_blocked"])
        self.assertFalse(status["spawn_allowed"])
        self.assertFalse(key_path.exists(), "status must not recreate a missing active-intent key")
        with self.assertRaisesRegex(Exception, "recovery required; spawn blocked"):
            self.call("p0_launch_assignment", {"internal": True}, **args)
        self.assertFalse(key_path.exists(), "assignment refusal must not rotate the missing key")
        # A direct admission helper call with an active intent must validate
        # rather than silently recreate the key.
        with self.assertRaisesRegex(Exception, "recovery required; admission blocked \\(missing\\)"):
            m._p0_key_for_admission(self.store, p0_store(self.store))
        self.assertFalse(key_path.exists(), "admission refusal must not rotate the missing key")
        self.assertNotIn(first["worker_token"], self.store.path.read_text(encoding="utf-8"))

    def test_assignment_key_status_not_required_does_not_create(self):
        key_store = key_module.P0AssignmentKeyStore.beside_ledger(self.store.path)
        self.assertFalse(key_store.path.exists())
        status = self.call("p0_launch_status", {"internal": True})
        self.assertEqual("not_required", status["assignment_key"])
        self.assertIsNone(status["assignment_key_reason"])
        self.assertTrue(status["spawn_allowed"])
        self.assertFalse(key_store.path.exists())

    def test_assignment_key_active_status_and_assignment_fail_closed_for_every_reason(self):
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        for reason in ("missing", "unreadable", "acl_invalid", "invalid_bytes"):
            error = key_module.P0AssignmentKeyUnavailable(
                "bounded key recovery required", reason=reason)
            side_before = p0_store(self.store).path.read_bytes()
            ledger_before = self.store.path.read_bytes()
            with self.subTest(reason=reason), patch.object(
                    key_module.P0AssignmentKeyStore, "read", side_effect=error):
                status = self.call("p0_launch_status", {"internal": True})
                self.assertEqual("recovery_required", status["assignment_key"])
                self.assertEqual(reason, status["assignment_key_reason"])
                self.assertTrue(status["scheduling_blocked"])
                self.assertFalse(status["spawn_allowed"])
                with self.assertRaisesRegex(
                        WorkParallelError,
                        r"recovery required; spawn blocked \(" + reason + r"\)"):
                    self.call("p0_launch_assignment", {"internal": True}, **args)
            self.assertEqual(side_before, p0_store(self.store).path.read_bytes())
            self.assertEqual(ledger_before, self.store.path.read_bytes())

    def test_assignment_key_spawn_transition_fail_closed_for_every_reason(self):
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        # Assignment has already derived/set the stable capability; the separate
        # spawning transition must still honor current key recovery status.
        self.call("p0_launch_assignment", {"internal": True}, **args)
        for reason in ("missing", "unreadable", "acl_invalid", "invalid_bytes"):
            error = key_module.P0AssignmentKeyUnavailable(
                "bounded key recovery required", reason=reason)
            side_before = p0_store(self.store).path.read_bytes()
            ledger_before = self.store.path.read_bytes()
            with self.subTest(reason=reason), patch.object(
                    key_module.P0AssignmentKeyStore, "read", side_effect=error):
                status = self.call("p0_launch_status", {"internal": True})
                self.assertEqual(reason, status["assignment_key_reason"])
                self.assertFalse(status["spawn_allowed"])
                with self.assertRaisesRegex(
                        WorkParallelError,
                        r"recovery required; spawn blocked \(" + reason + r"\)"):
                    self.call("p0_launch_spawning", {"internal": True}, **args)
            self.assertEqual(side_before, p0_store(self.store).path.read_bytes())
            self.assertEqual(ledger_before, self.store.path.read_bytes())
            self.assertEqual("reserved", p0_store(self.store).read()["intents"][
                reserved["intent_id"]]["state"])

    def test_assignment_key_open_send_status_and_reacquire_fail_closed_for_every_reason(self):
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        self.call("p0_launch_spawning", {"internal": True}, **args)
        self.call("bind", {"host_id": "agent", "agent_name": reserved["dispatch_name"]},
                  lease_id=reserved["lease_id"])
        self.store._change(
            lambda doc: doc["leases"][reserved["lease_id"]].update(
                state="waiting_input", slot=False, usage=0, idle_proof="waiting"),
            self.store.read()["version"])
        side = p0_store(self.store)
        parent = side.approval_intent(
            reserved["lease_id"], scope=SCOPE, item_id="item")
        side.prepare_reacquire_send(
            parent["intent_id"], self.store.read()["leases"][reserved["lease_id"]],
            ledger_before_version=self.store.read()["version"],
            expected_version=side.read()["version"], send_id="e" * 32,
            send_receipt="e" * 64)
        for reason in ("missing", "unreadable", "acl_invalid", "invalid_bytes"):
            error = key_module.P0AssignmentKeyUnavailable(
                "bounded key recovery required", reason=reason)
            side_before = p0_store(self.store).path.read_bytes()
            ledger_before = self.store.path.read_bytes()
            with self.subTest(reason=reason), patch.object(
                    key_module.P0AssignmentKeyStore, "read", side_effect=error):
                status = self.call("p0_launch_status", {"internal": True})
                self.assertEqual(reason, status["assignment_key_reason"])
                self.assertTrue(status["scheduling_blocked"])
                self.assertFalse(status["spawn_allowed"])
                with self.assertRaisesRegex(
                        WorkParallelError,
                        r"recovery required; reacquire blocked \(" + reason + r"\)"):
                    self.call("p0_reacquire", {}, lease_id=reserved["lease_id"])
            self.assertEqual(side_before, p0_store(self.store).path.read_bytes())
            self.assertEqual(ledger_before, self.store.path.read_bytes())

    def test_assignment_key_exact_file_grammar_and_link_refusal(self):
        key_store = key_module.P0AssignmentKeyStore.beside_ledger(self.store.path)
        key = key_store.ensure_before_first_intent()
        self.assertEqual(32, len(key))
        raw = key_store.path.read_bytes()
        self.assertEqual(64, len(raw))
        self.assertNotIn(b"\n", raw)
        key_store.path.write_bytes(raw + b"\n")
        with self.assertRaisesRegex(Exception, "invalid P0 assignment"):
            key_store.read()
        key_store.path.unlink()
        target = self.tmp / "other.key"
        target.write_bytes(raw)
        try:
            os.symlink(target, key_store.path)
        except (NotImplementedError, OSError):
            return
        with self.assertRaisesRegex(Exception, "link refused|unavailable"):
            key_store.read()

    def test_assignment_key_external_create_race_fails_closed(self):
        key_store = key_module.P0AssignmentKeyStore.beside_ledger(self.store.path)
        original_link = key_module.os.link
        external = b"a" * 64

        def race(_source, destination):
            Path(destination).write_bytes(external)
            return original_link(_source, destination)

        with patch.object(key_module.os, "link", side_effect=race):
            with self.assertRaises(FileExistsError):
                key_store.ensure_before_first_intent()
        self.assertEqual(external, key_store.path.read_bytes())
        if os.name == "nt":
            with self.assertRaisesRegex(Exception, "ACL has unexpected entries"):
                key_store.read()
        else:
            # The racing writer used the default umask, so the key is not private
            # (0644): the store refuses it instead of trusting the external bytes.
            with self.assertRaisesRegex(Exception, "not private"):
                key_store.read()
        self.assertEqual([], list(self.tmp.glob("*.tmp")))

    def test_assignment_replay_refuses_after_capability_consumed(self):
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        assignment = self.call("p0_launch_assignment", {"internal": True}, **args)
        self.call("p0_launch_spawning", {"internal": True}, **args)
        self.call("bind", {"host_id": "agent", "agent_name": reserved["dispatch_name"]},
                  lease_id=reserved["lease_id"])
        with patch.object(m, "ledger", return_value=self.store):
            m.report({"lease_id": reserved["lease_id"], "worker_token": assignment["worker_token"],
                      "event": "question", "evidence": "need input"}, self.board, self.config)
        with self.assertRaisesRegex(WorkParallelError, "unknown P0 assignment"):
            self.call("p0_launch_assignment", {"internal": True}, **args)

    def test_concurrent_assignment_returns_one_capability(self):
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        results, errors = [], []
        def issue():
            try:
                results.append(self.call("p0_launch_assignment", {"internal": True}, **args))
            except Exception as error:
                errors.append(error)
        workers = [threading.Thread(target=issue) for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual([], errors)
        self.assertEqual(8, len(results))
        self.assertEqual(1, len({row["worker_token"] for row in results}))
        lease = self.store.read()["leases"][reserved["lease_id"]]
        self.assertEqual(parallel.hash_worker_token(results[0]["worker_token"]), lease["report_hash"])

    def test_assignment_capability_rechecks_exact_intent_inside_ledger_lock(self):
        reserved = self.reserve()
        side = p0_store(self.store)
        pinned = side.read()["intents"][reserved["intent_id"]]
        side.transition_intent(reserved["intent_id"], "cancelled",
                               expected_version=side.read()["version"])
        with self.assertRaisesRegex(WorkParallelError, "exact launch intent|launch intent state"):
            self.store.set_p0_reserved_capability(
                reserved["lease_id"], "a" * 64, reserved["dispatch_name"],
                p0_intent=pinned, expected_version=self.store.read()["version"])
        self.assertIsNone(self.store.read()["leases"][reserved["lease_id"]]["report_hash"])

    def test_assignment_capability_replay_refuses_different_hash(self):
        reserved = self.reserve()
        side = p0_store(self.store)
        pinned = side.read()["intents"][reserved["intent_id"]]
        version = self.store.read()["version"]
        self.store.set_p0_reserved_capability(
            reserved["lease_id"], "a" * 64, reserved["dispatch_name"],
            p0_intent=pinned, expected_version=version)
        with self.assertRaisesRegex(WorkParallelError, "pinned differently"):
            self.store.set_p0_reserved_capability(
                reserved["lease_id"], "b" * 64, reserved["dispatch_name"],
                p0_intent=pinned, expected_version=self.store.read()["version"])
        self.assertEqual("a" * 64,
                         self.store.read()["leases"][reserved["lease_id"]]["report_hash"])

    def test_assignment_hmac_changes_for_every_identity_field(self):
        key = bytes(range(32))
        intent = {"scope": SCOPE, "root_id": "root", "root_created_at": 1.0,
                  "item_id": "item", "intent_id": "a" * 32}
        lease = {"id": "lease"}
        run = {"generation": 1}
        base = m._p0_assignment_token(key, intent, lease, run)
        mutations = [
            ({**intent, "scope": SCOPE + "x"}, lease, run),
            ({**intent, "root_id": "other"}, lease, run),
            ({**intent, "root_created_at": 2.0}, lease, run),
            ({**intent, "item_id": "other"}, lease, run),
            ({**intent, "intent_id": "b" * 32}, lease, run),
            (intent, {"id": "other"}, run),
            (intent, lease, {"generation": 2}),
        ]
        for changed_intent, changed_lease, changed_run in mutations:
            self.assertNotEqual(base, m._p0_assignment_token(
                key, changed_intent, changed_lease, changed_run))

    def test_parked_wait_projection_is_bounded_exact_and_token_free(self):
        reserved = self.reserve()
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        self.call("p0_launch_spawning", {"internal": True}, **args)
        self.call("bind", {"host_id": "agent", "agent_name": reserved["dispatch_name"]},
                  lease_id=reserved["lease_id"])
        def waiting(doc):
            doc["leases"][reserved["lease_id"]].update(
                state="waiting_input", slot=False, usage=0, idle_proof="wait")
        self.store._change(waiting, self.store.read()["version"])
        projection = m.parked_wait_projection(
            self.store, self.board, SCOPE, [{"item_id": "item", "root_id": "root"}])
        self.assertEqual([{"item_id": "item", "root_id": "root",
                           "lease_id": reserved["lease_id"],
                           "state": "waiting_input", "slot": False}], projection["rows"])
        self.assertNotIn("host_id", json.dumps(projection))
        self.assertIsNone(m.parked_wait_projection(
            self.store, self.board, SCOPE, [{"item_id": "item", "root_id": "root"}],
            ledger_version=projection["ledger_version"] - 1))
        wrong = m.parked_wait_projection(
            self.store, self.board, SCOPE, [{"item_id": "item", "root_id": "wrong"}])
        self.assertEqual([], wrong["rows"])

    def test_wrong_actor_scope_and_stale_version_refuse(self):
        with self.assertRaisesRegex(WorkParallelError, "verified coordinator"):
            self.call("p0_reserve_next", {"fork": True, "agent_id": "fork"}, role="worker")
        reserved = self.reserve()
        replay = self.call("p0_reserve_next", {"fork": True}, role="worker", expected_version=0)
        self.assertEqual(reserved["intent_id"], replay["intent_id"])
        self.assertEqual(reserved["lease_id"], replay["lease_id"])
        self.assertEqual(1, len(self.store.read()["leases"]))
        with self.assertRaisesRegex(WorkParallelError, "P0 launch identity"):
            self.call("p0_launch_spawning", {"internal": True},
                      intent_id=reserved["intent_id"], lease_id="wrong",
                      dispatch_name=reserved["dispatch_name"])

    def test_full_p0_clear_journal_evicts_oldest_done_before_prepare_set_and_clear(self):
        # Celly 2026-11-22 07:33:58Z: 32 active/cleared roots + 32 superseded done
        # clears reached MAX_CLEARS=64, so prune_done(keep=32) removed nothing and
        # every new P0 prepare_set/prepare_clear failed with "P0 clear bound".
        self.assertGreater(p0.MAX_CLEARS, p0.MAX_ROOTS)
        side = p0_store(self.store)
        with patch.object(p0, "MAX_CLEARS", 4):
            # Seed root already has 1 done clear; add a 2nd done clear on root and
            # 2 done clears on r1 -> 4 clears total (2 protected + 2 superseded).
            for rid, created, stamps in (("root", 1.0, (10.0,)), ("r1", 11.0, (11.0, 12.0))):
                for st in stamps:
                    _, row = side.prepare_set(SCOPE, rid, created, 1, set_by="user",
                                              expected_version=side.read()["version"], now=st)
                    side.mark_clear(row["clear_id"], "board_committed", expected_version=side.read()["version"])
                    side.mark_clear(row["clear_id"], "done", expected_version=side.read()["version"])
            self.assertEqual(4, len(side.read()["clears"]))
            # prune_done(keep=32) must still leave headroom below MAX_CLEARS
            # (with keep clamped to MAX_CLEARS - 1 - len(protected)).
            _, removed = side.prune_done(expected_version=side.read()["version"], keep=4)
            self.assertGreaterEqual(removed, 1)
            self.assertLess(len(side.read()["clears"]), 4)
            # Refill to cap=4 with a superseded done clear, then prepare_set for new r2
            _, dup = side.prepare_set(SCOPE, "r1", 11.0, 2, set_by="user",
                                      expected_version=side.read()["version"], now=13.0)
            side.mark_clear(dup["clear_id"], "board_committed", expected_version=side.read()["version"])
            side.mark_clear(dup["clear_id"], "done", expected_version=side.read()["version"])
            self.assertEqual(4, len(side.read()["clears"]))
            _, r2 = side.prepare_set(SCOPE, "r2", 20.0, 3, set_by="user",
                                     expected_version=side.read()["version"], now=20.0)
            self.assertEqual("r2", r2["root_id"])
            self.assertEqual(4, len(side.read()["clears"]))
            side.mark_clear(r2["clear_id"], "board_committed", expected_version=side.read()["version"])
            side.mark_clear(r2["clear_id"], "done", expected_version=side.read()["version"])
            # prepare_clear when full must evict the remaining superseded done clear and keep validate() intact
            _, cleared = side.prepare_clear(SCOPE, "r2", 20.0, "P2", 4, set_by="user",
                                            expected_version=side.read()["version"], now=21.0)
            self.assertEqual("P2", cleared["target_priority"])
            self.assertEqual(4, len(side.read()["clears"]))
            side.mark_clear(cleared["clear_id"], "board_committed", expected_version=side.read()["version"])
            side.mark_clear(cleared["clear_id"], "done", expected_version=side.read()["version"])
            # Add a 4th active root (evicting r2's superseded prepare_set row) -> now all 4 clears are protected newest rows
            _, r3 = side.prepare_set(SCOPE, "r3", 30.0, 5, set_by="user",
                                     expected_version=side.read()["version"], now=30.0)
            side.mark_clear(r3["clear_id"], "board_committed", expected_version=side.read()["version"])
            side.mark_clear(r3["clear_id"], "done", expected_version=side.read()["version"])
            self.assertEqual(4, len(side.read()["clears"]))
            # With all 4 slots holding required active/cleared newest operations, a 5th root cleanly hits P0 clear bound
            with self.assertRaisesRegex(WorkParallelError, "P0 clear bound"):
                side.prepare_set(SCOPE, "r4", 40.0, 6, set_by="user",
                                 expected_version=side.read()["version"], now=40.0)
            # Once r3 ends (sweep_roots), its done clear becomes evictable and r4 succeeds
            side.sweep_roots(SCOPE, {"nodes": [{"id": "root", "parent_id": None, "created_at": 1.0, "status": "pending"},
                                               {"id": "r1", "parent_id": None, "created_at": 11.0, "status": "pending"}]},
                             expected_version=side.read()["version"], now=35.0)
            _, r4 = side.prepare_set(SCOPE, "r4", 40.0, 6, set_by="user",
                                     expected_version=side.read()["version"], now=40.0)
            self.assertEqual("r4", r4["root_id"])


if __name__ == "__main__":
    unittest.main()
