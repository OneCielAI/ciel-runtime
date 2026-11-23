"""P0 survives a native parked continuation of the same Work board (Celly 2026-11-23).

The board continues across conversation identities through the authenticated
native-parked lineage, but P0 root markers were keyed by the identity alone:
every restart dropped P0 (39 of 43 Celly markers named roots of the current
board under five identities; n1179/n1209 lost P0). Temporary state only.
"""
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support import work_parallel_p0 as p0
from ciel_runtime_support import work_session_lineage as lineage
from ciel_runtime_support.work_parallel import WorkParallelStore
from ciel_runtime_support.work_parallel_p0 import p0_store

SECRET = "p0-lineage-secret-" * 3
OLD = "claude:old-session"
MID = "claude:mid-session"
NEW = "claude:new-session"
OTHER = "claude:other-board"


def write_alias(root, session, canonical):
    folder = root / "work-sessions" / hashlib.sha256(session.encode()).hexdigest()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "work-lineage.json").write_text(json.dumps(
        {"session": session, "canonical": canonical, "authority": "native-parked-handoff"}),
        encoding="utf-8")
    return folder


def node(node_id, parent=None, created=1.0, status="pending", priority="P1"):
    row = {"id": node_id, "parent_id": parent, "title": node_id, "status": status, "note": "",
           "progress": "", "evidence": "", "waiting_on": "", "ref": "", "urgent": False,
           "updated_at": created, "created_at": created}
    if parent is None:
        row.update(phase="executing", priority=priority)
    return row


class Board:
    def __init__(self, nodes, scopes):
        self.doc = {"version": 3, "phase": "executing", "active_root": nodes[0]["id"],
                    "focus_ids": [], "nodes": nodes}
        self.scopes = frozenset(scopes)

    def read(self):
        return self.doc

    def lineage_scopes(self):
        return self.scopes


class LineageScopesTests(unittest.TestCase):
    def test_every_identity_continuing_the_same_board_and_nothing_else(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            write_alias(root, MID, OLD)
            write_alias(root, NEW, OLD)
            write_alias(root, "claude:unrelated", OTHER)
            # A marker whose folder is not its own session's digest is ignored.
            spoof = root / "work-sessions" / ("0" * 64)
            spoof.mkdir(parents=True)
            (spoof / "work-lineage.json").write_text(json.dumps(
                {"session": "claude:spoof", "canonical": OLD, "authority": "native-parked-handoff"}),
                encoding="utf-8")
            # A marker with another authority is not a continuation.
            write_alias(root, "claude:copied", OLD).joinpath("work-lineage.json").write_text(
                json.dumps({"session": "claude:copied", "canonical": OLD, "authority": "copy"}),
                encoding="utf-8")
            self.assertEqual({OLD, MID, NEW}, set(lineage.lineage_scopes(root, NEW)))
            self.assertEqual({OLD, MID, NEW}, set(lineage.lineage_scopes(root, OLD)))
            self.assertEqual({OTHER, "claude:unrelated"}, set(lineage.lineage_scopes(root, OTHER)))
            self.assertEqual({"claude:alone"}, set(lineage.lineage_scopes(root, "claude:alone")))


class AdoptTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.store = WorkParallelStore(self.tmp / "work-parallel.json")
        self.side = p0_store(self.store)

    def activate(self, scope, root_id, created, now):
        self.side.activate_root(scope, root_id, created, set_by="user",
                                expected_version=self.side.read()["version"], now=now)

    def clear(self, scope, root_id, created, now):
        """A priority change ends P0 in ``scope`` (marker cleared, operation done)."""
        row = self.side.prepare_clear(scope, root_id, created, "P2", 3,
                                      expected_version=self.side.read()["version"], now=now)[1]
        for target in ("board_committed", "done"):
            self.side.mark_clear(row["clear_id"], target, expected_version=self.side.read()["version"])

    def test_latest_decision_of_the_lineage_carries_over_and_rows_of_the_session_win(self):
        board = Board([node("g1", created=11.0), node("g2", created=12.0), node("g3", created=13.0),
                       node("g4", created=14.0, status="done"), node("g5", created=15.0),
                       node("g6", created=16.0)], {OLD, MID, NEW})
        self.activate(OLD, "g1", 11.0, now=100.0)
        self.activate(OLD, "g2", 12.0, now=100.0)
        self.activate(MID, "g2", 12.0, now=200.0)
        self.clear(MID, "g2", 12.0, now=250.0)          # a later session ended P0
        self.activate(OLD, "g3", 99.0, now=100.0)       # same id, a different root generation
        self.activate(OLD, "g4", 14.0, now=100.0)       # root finished on the board
        self.activate(OTHER, "g5", 15.0, now=100.0)     # another board's session
        self.activate(OLD, "g6", 16.0, now=100.0)
        self.activate(NEW, "g6", 16.0, now=40.0)        # the session itself decided,
        self.clear(NEW, "g6", 16.0, now=50.0)           # even earlier than the old row
        version = self.side.read()["version"]
        adopted = self.side.adopt_lineage_markers(NEW, board.lineage_scopes(), board.read(),
                                                  expected_version=version, now=300.0)
        self.assertEqual(["g1"], [row["root_id"] for row in adopted])
        state = self.side.read()
        p0.validate(state)
        marker = state["roots"][p0.root_key(NEW, "g1")]
        self.assertEqual(("active", 11.0, 100.0, "user"),
                         (marker["state"], marker["root_created_at"], marker["set_at"], marker["set_by"]))
        root = board.read()["nodes"][0]
        self.assertEqual("P0", self.side.effective_priority(NEW, root))
        for root_id in ("g2", "g3", "g4", "g5"):
            self.assertNotIn(p0.root_key(NEW, root_id), state["roots"], root_id)
        self.assertEqual("cleared", state["roots"][p0.root_key(NEW, "g6")]["state"])
        # Idempotent: nothing is left to adopt.
        again = self.side.adopt_lineage_markers(NEW, board.lineage_scopes(), board.read(),
                                                expected_version=self.side.read()["version"], now=400.0)
        self.assertEqual([], again)
        self.assertEqual(state, self.side.read())

    def test_a_lone_session_adopts_nothing(self):
        board = Board([node("g1", created=11.0)], {NEW})
        self.activate(OLD, "g1", 11.0, now=100.0)
        before = self.side.read()
        self.assertEqual([], self.side.adopt_lineage_markers(
            NEW, board.lineage_scopes(), board.read(), expected_version=before["version"]))
        self.assertEqual(before, self.side.read())


class StatusAdoptsTests(unittest.TestCase):
    def test_the_host_status_read_restores_p0_and_the_p0_lane_launches_it(self):
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = WorkParallelStore(tmp / "work-parallel.json")
            side = p0_store(store)
            side.activate_root(OLD, "g1", 11.0, set_by="user", expected_version=0, now=100.0)
            board = Board([node("g1", created=11.0), node("item", parent="g1", created=12.0)],
                          {OLD, NEW})
            config = {"work_parallel_host": {"token": SECRET, "capability_ready": True}}

            def call(name, **args):
                args.setdefault("expected_version", store.read()["version"])
                args["_host"] = {"token": SECRET, "fork": True}
                with patch.object(m, "ledger", return_value=store):
                    return json.loads(m.run(name, args, board, NEW, config, None))

            # Before: the new session sees canonical P1.
            self.assertEqual("P1", side.effective_priority(NEW, board.doc["nodes"][0]))
            call("work_parallel_status")
            self.assertEqual("P0", side.effective_priority(NEW, board.doc["nodes"][0]))
            reserved = call("work_parallel_event", event="p0_reserve_next", role="worker")
            self.assertEqual("ready", reserved["dispatch"])
            self.assertEqual("item", reserved["item_id"])


if __name__ == "__main__":
    unittest.main()
