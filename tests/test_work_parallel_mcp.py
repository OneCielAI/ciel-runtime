"""Focused temporary-ledger/router/host tests; never start runtime sessions."""

import json
import subprocess
from unittest.mock import patch
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.work_parallel import WorkParallelStore, WorkParallelError

SECRET = "test-host-secret-" * 3
SCOPE = "claude:test"


def _make_env(tmp_path):
    store = WorkParallelStore(tmp_path / "ledger.json")
    board = {
        "version": 7,
        "phase": "executing",
        "active_root": "n1",
        "focus_ids": [],
        "nodes": [
            {
                "id": "n1",
                "parent_id": None,
                "title": "Product goal",
                "ref": "PRD",
                "phase": "executing",
                "status": "pending",
                "updated_at": 1,
                "priority": "P1",
                "note": "Ship the exact approved checkout behavior.",
            },
            {
                "id": "n2",
                "parent_id": "n1",
                "title": "Checkout phase",
                "status": "pending",
                "updated_at": 1,
            },
            {
                "id": "n3",
                "parent_id": "n2",
                "title": "Fix payment retry",
                "ref": "PAY-227",
                "status": "pending",
                "updated_at": 1,
            },
        ],
    }

    class Board:
        def read(self):
            return board

    config = {"work_parallel_host": {"token": SECRET, "capability_ready": True}}

    def run(name, host=None, **args):
        args.setdefault("expected_version", store.read()["version"])
        if host is not None:
            args["_host"] = {"token": SECRET, **host}
        with patch.object(m, "ledger", return_value=store):
            return json.loads(
                m.run("work_parallel_" + name, args, Board(), SCOPE, config, None)
            )

    return store, board, config, run


def launch(env):
    s, b, c, run = env
    run("configure", {}, changes={"enabled": True})
    r = run("event", {"fork": True}, event="reserve", item_id="n1", priority="P1")
    run("event", {"host_id": "agent-1"}, event="bind", lease_id=r["lease_id"])
    return r["lease_id"]












def _git(cwd, *args):
    import subprocess

    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _legacy_reviewer(store, item_id, host_id, *, plan_version=7, ident="legacy-reviewer"):
    """A submitted reviewer row exactly as 4eebfdc/5c91ea8 builds persisted it.

    New reviewer reservations are retired; legacy rows must still be readable
    and consumable by acceptance.
    """
    doc = store.read()
    doc["leases"][ident] = {
        "id": ident, "scope": SCOPE, "item_id": item_id, "priority": "P1", "created_at": 1,
        "role": "reviewer", "mode": "fork", "plan_version": plan_version, "state": "submitted",
        "host_id": host_id, "slot": False, "usage": 0, "artifact": "b" * 64, "rework": 0,
        "evidence": "independent findings", "idle_proof": "independent findings",
        "cleanup_proof": None, "report_hash": None,
    }
    store.path.write_text(json.dumps(doc), encoding="utf-8")
    return ident


def _accepted_worker(env, tmp_path):
    s, b, c, run = env
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "init", str(repo))
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@example.invalid")
    (repo / "f").write_text("x")
    _git(repo, "add", "f")
    _git(repo, "commit", "-m", "fixture")
    _git(repo, "remote", "add", "origin", str(remote))
    target = repo / ".claude" / "worktrees" / "w1"
    _git(repo, "worktree", "add", "-b", "w1", str(target))
    ident = launch(env)
    run(
        "event",
        {"agent_id": "agent-1"},
        event="result",
        lease_id=ident,
        plan_version=7,
        artifact="a" * 64,
        evidence="r",
    )
    for event in ("verify", "review"):
        run(
            "event",
            {},
            event=event,
            lease_id=ident,
            plan_version=7,
            artifact="a" * 64,
            evidence="checked",
        )
    reviewer = _legacy_reviewer(s, "n1", "reviewer-1")
    run(
        "event",
        {},
        event="accept",
        lease_id=ident,
        reviewer_lease_id=reviewer,
        plan_version=7,
        artifact="a" * 64,
        evidence="ok",
    )
    run(
        "event",
        {"state": "idle", "proof": "terminal"},
        event="reconcile",
        lease_id=ident,
    )
    return ident, repo, target








def _workspace_host(ident, target):
    return {
        "workspace_binding": {
            "host_id": "agent-1",
            "lease_id": ident,
            "idle": True,
            "path": str(target),
        }
    }






class WorkParallelMCPTests(unittest.TestCase):
    def test_worker_report_schema_requires_identity_capability_and_event(self):
        schema = next(row for row in m.schemas() if row["name"] == "work_parallel_report")["inputSchema"]
        self.assertEqual(["lease_id", "worker_token", "event"], schema["required"])
        self.assertIn("detail", schema["properties"])

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp_path = Path(temporary.name)
        self.env = _make_env(self.tmp_path)

    def test_managed_workspace_launch_binding_and_idle_evidence(self):
        store, board, config, run = self.env
        repo = self.tmp_path / "owned"
        _git(self.tmp_path, "init", str(repo))
        _git(repo, "config", "user.name", "fixture")
        _git(repo, "config", "user.email", "fixture@example.invalid")
        (repo / "f").write_text("fixture")
        _git(repo, "add", "f")
        _git(repo, "commit", "-m", "fixture")
        from ciel_runtime_support import runtime_paths
        from ciel_runtime_support.work_parallel_workspace import observe
        with patch.object(runtime_paths, "CONFIG_DIR", self.tmp_path / "cfg"):
            run("configure", {}, changes={"enabled": True})
            reserved = run("event", {"fork": True, "workspace_repo": str(repo)},
                           event="reserve", item_id="n1", priority="P1")
            ident = reserved["lease_id"]
            self.assertTrue(Path(reserved["workspace"]["path"]).is_dir())
            coordinated = m.coordination(store).read()["runs"][ident]["workspace"]
            self.assertEqual({key: reserved["workspace"][key] for key in ("path", "branch", "head")}, coordinated)
            run("event", {"host_id": "native-worker", "agent_name": "owned-name"}, event="bind", lease_id=ident)
            self.assertEqual(store.read()["leases"][ident]["agent_name"], "owned-name")
            run("event", {"state": "idle", "proof": "native terminal"}, event="reconcile", lease_id=ident)
            record = observe(repo, ident)
            self.assertTrue(record["idle"])
            self.assertEqual(record["host_id"], "native-worker")

    def test_host_salvage_persists_structured_receipt_before_unreported(self):
        store, board, config, run = self.env
        repo = self.tmp_path / "salvage-repo"
        _git(self.tmp_path, "init", str(repo))
        _git(repo, "config", "user.name", "fixture")
        _git(repo, "config", "user.email", "fixture@example.invalid")
        (repo / "f").write_text("fixture")
        _git(repo, "add", "f")
        _git(repo, "commit", "-m", "fixture")
        from ciel_runtime_support import runtime_paths
        with patch.object(runtime_paths, "CONFIG_DIR", self.tmp_path / "cfg"):
            reserved = run("event", {"fork": True, "workspace_repo": str(repo)},
                           event="reserve", item_id="n1", priority="P1")
            run("event", {"host_id": "native-worker"}, event="bind", lease_id=reserved["lease_id"])
            Path(reserved["workspace"]["path"], "partial.txt").write_text("partial")
            salvaged = run("event", {"proof": "native terminal"}, event="host_salvage",
                           lease_id=reserved["lease_id"], reason="missing exit report")
            self.assertTrue(salvaged["salvaged"])
            detail = m.coordination(store).detail(salvaged["receipt_digest"])
            self.assertEqual(["n1"], detail["remaining"])
            self.assertIn("partial.txt", detail["workspace"]["changed_files"])
            run("event", {"proof": "salvage " + salvaged["receipt_digest"]},
                event="unreported", lease_id=reserved["lease_id"])
            self.assertEqual("unreported", store.read()["leases"][reserved["lease_id"]]["state"])

    def test_failed_workspace_preparation_releases_its_reservation(self):
        store, board, config, run = self.env
        not_root = self.tmp_path / "plain"
        not_root.mkdir()
        from ciel_runtime_support import runtime_paths
        from ciel_runtime_support import work_parallel_workspace
        with patch.object(runtime_paths, "CONFIG_DIR", self.tmp_path / "cfg"):
            run("configure", {}, changes={"enabled": True, "priority_limits": {"P2": 1}})
            # Owned runs 2026-11-09: "repo must be exact worktree root" and a git
            # timeout each left a reserved lease that held the only slot.
            with self.assertRaisesRegex(Exception, "reservation released"):
                run("event", {"fork": True, "workspace_repo": str(not_root)}, event="reserve", item_id="n1", priority="P2")
            self.assertEqual({}, store.read()["leases"])
            def hang(*args, **kwargs):
                raise subprocess.TimeoutExpired(args[0], 30)
            with patch.object(work_parallel_workspace.subprocess, "run", hang):
                with self.assertRaisesRegex(Exception, "timed out after"):
                    run("event", {"fork": True, "workspace_repo": str(not_root)}, event="reserve", item_id="n1", priority="P2")
            self.assertEqual({}, store.read()["leases"])
            # The slot is free again: a plain reservation is admitted.
            self.assertIn("lease_id", run("event", {"fork": True}, event="reserve", item_id="n1", priority="P2"))

    def test_status_enriches_leases_with_item_assignment_metadata(self):
        env = self.env
        s, b, c, run = env
        run("configure", {}, changes={"enabled": True})
        reserved = run("event", {"fork": True}, event="reserve", item_id="n3", priority="P1")
        self.assertEqual(reserved["item_title"], "Fix payment retry")
        self.assertEqual(reserved["item_ref"], "PAY-227")
        self.assertEqual(reserved["goal_title"], "Product goal")
        self.assertEqual(reserved["item_path"], ["Product goal", "Checkout phase", "Fix payment retry"])
        run("event", {"host_id": "agent-1", "agent_name": "work-alpha"}, event="bind", lease_id=reserved["lease_id"])
        lease = run("status")["leases"][reserved["lease_id"]]
        self.assertEqual(lease["item_id"], "n3")
        self.assertEqual(lease["item_title"], "Fix payment retry")
        self.assertEqual(lease["agent_name"], "work-alpha")
        self.assertEqual(lease["host_id"], "agent-1")
        self.assertNotIn("item_note", lease)

    def test_reserve_uses_root_priority_and_returns_worker_token_and_note(self):
        store, board, config, run = self.env
        reserved = run("event", {"fork": True, "dispatch_name": "work-n3"},
                       event="reserve", item_id="n3", priority="P5")
        self.assertEqual(reserved["priority"], "P1")
        self.assertEqual(reserved["item_note"], "")
        self.assertEqual(len(reserved["worker_token"]), 64)
        row = store.read()["leases"][reserved["lease_id"]]
        self.assertNotIn(reserved["worker_token"], json.dumps(row))
        self.assertEqual(row["dispatch_name"], "work-n3")

    def test_worker_report_token_needs_no_host_identity_and_revokes_on_result(self):
        store, board, config, run = self.env
        reserved = run("event", {"fork": True, "dispatch_name": "work-n3"},
                       event="reserve", item_id="n3", priority="P1")
        run("event", {"host_id": "agent-1", "agent_name": "work-n3"},
            event="bind", lease_id=reserved["lease_id"])
        with patch.object(m, "ledger", return_value=store):
            class Board:
                def read(_self):
                    return board
            result = json.loads(m.report({"lease_id": reserved["lease_id"],
                "worker_token": reserved["worker_token"], "event": "result",
                "plan_version": 7, "artifact": "a" * 64, "evidence": "tested"},
                Board(), config))
            self.assertEqual(result["state"], "submitted")
            with self.assertRaises(WorkParallelError):
                m.report({"lease_id": reserved["lease_id"],
                    "worker_token": reserved["worker_token"], "event": "result",
                    "plan_version": 7, "artifact": "a" * 64, "evidence": "replay"},
                    Board(), config)

    def test_unrelated_board_change_does_not_invalidate_scoped_assignment(self):
        store, board, config, run = self.env
        reserved = run("event", {"fork": True, "dispatch_name": "work-n3"},
                       event="reserve", item_id="n3", priority="P1")
        run("event", {"host_id": "agent-1", "agent_name": "work-n3"},
            event="bind", lease_id=reserved["lease_id"])
        board["version"] += 1
        board["nodes"].append({"id": "other", "parent_id": None, "title": "Other root",
                               "note": "unrelated", "status": "pending", "updated_at": 2,
                               "priority": "P5", "phase": "planning"})
        with patch.object(m, "ledger", return_value=store):
            class Board:
                def read(_self): return board
            result = json.loads(m.report({"lease_id": reserved["lease_id"],
                "worker_token": reserved["worker_token"], "event": "result",
                "plan_version": 7, "artifact": "a" * 64, "evidence": "tested"}, Board(), config))
        self.assertEqual("submitted", result["state"])
        self.assertTrue(result["slot_released"])

    def test_assigned_scope_change_invalidates_scoped_assignment(self):
        store, board, config, run = self.env
        reserved = run("event", {"fork": True}, event="reserve", item_id="n3", priority="P1")
        run("event", {"host_id": "agent-1"}, event="bind", lease_id=reserved["lease_id"])
        board["version"] += 1
        board["nodes"][2]["note"] = "changed approved requirement"
        with patch.object(m, "ledger", return_value=store):
            class Board:
                def read(_self): return board
            with self.assertRaisesRegex(WorkParallelError, "approval scope changed"):
                m.report({"lease_id": reserved["lease_id"], "worker_token": reserved["worker_token"],
                          "event": "result", "artifact": "a" * 64, "evidence": "tested"}, Board(), config)

    def test_reserve_next_skips_waiting_leaf_and_returns_next_runnable(self):
        store, board, config, run = self.env
        board["nodes"][2]["waiting_on"] = "review"
        board["nodes"].append({"id": "n4", "parent_id": "n2", "title": "Second item",
                               "note": "Do second", "status": "pending", "updated_at": 2})
        reserved = run("event", {"fork": True, "dispatch_name": "auto-one"}, event="reserve_next")
        self.assertEqual(reserved["item_id"], "n4")
        self.assertEqual(reserved["priority"], "P1")
        self.assertEqual(reserved["item_note"], "Do second")

    def test_owner_without_birth_identity_is_never_classified_dead(self):
        store, board, config, run = self.env
        reserved = run("event", {"fork": True, "session_pid": 4242,
                                  "session_process_identity": None},
                       event="reserve", item_id="n3", priority="P1")
        with patch.object(m, "process_identity", return_value="different-birth"):
            preview = m._orphan_preview(store)
        row = next(item for item in preview["leases"] if item["lease_id"] == reserved["lease_id"])
        self.assertEqual("unknown", row["owner_state"])

    def test_owner_comes_only_from_this_conversations_bound_scope(self):
        from ciel_runtime_support import runtime_paths
        instance = self.tmp_path / "instance"
        instance.mkdir()
        scope = instance / "compaction-scope.json"
        scope.write_text(json.dumps({"runtime": "claude", "session_id": "other", "launcher_pid": 4242}), encoding="utf-8")
        with patch.object(runtime_paths, "ROUTER_INSTANCE_DIR", instance), \
             patch.object(m, "process_identity", return_value="birth-4242"):
            self.assertIsNone(m._owner_from_host({}, SCOPE))
            scope.write_text(json.dumps({"runtime": "claude", "session_id": "test", "launcher_pid": 4242}), encoding="utf-8")
            self.assertEqual({"session_pid": 4242, "identity": "birth-4242"}, m._owner_from_host({}, SCOPE))

    def test_orphan_preview_is_read_only_and_apply_requires_observation(self):
        store, board, config, run = self.env
        # No provable owner: this models a lease written by an older build.
        with patch.object(m, "_owner_from_host", return_value=None):
            reserved = run("event", {"fork": True, "dispatch_name": "old"}, event="reserve", item_id="n3")
        run("event", {"host_id": "old-agent", "agent_name": "old"}, event="bind", lease_id=reserved["lease_id"])
        before = store.read()
        with patch.object(m, "process_identity", return_value=""):
            preview = run("event", {}, event="reconcile_orphans", operation="preview")
        self.assertEqual(store.read(), before)
        row = next(item for item in preview["leases"] if item["lease_id"] == reserved["lease_id"])
        self.assertEqual(row["owner_state"], "legacy_no_owner")
        with self.assertRaises(WorkParallelError):
            run("event", {}, event="reconcile_orphans", operation="apply",
                lease_id=reserved["lease_id"], preview_digest=preview["digest"],
                observation={"owner_state": "legacy_no_owner"})

    def test_legacy_unbound_orphan_apply_releases_invalid_reservation_shape(self):
        store, board, config, run = self.env
        with patch.object(m, "_owner_from_host", return_value=None):
            reserved = run("event", {"fork": True}, event="reserve", item_id="n3")
        with patch.object(m, "process_identity", return_value=""):
            preview = run("event", {}, event="reconcile_orphans", operation="preview")
        result = run("event", {"proof": "operator verified legacy owner absent"},
                     event="reconcile_orphans", operation="apply", lease_id=reserved["lease_id"],
                     preview_digest=preview["digest"], observation={"owner_state": "legacy_no_owner",
                         "host_dead": True, "scope_board_missing": True, "pid_absent": True})
        self.assertTrue(result["released"])
        self.assertEqual("released", result["state"])
        self.assertNotIn(reserved["lease_id"], store.read()["leases"])

    def test_unbound_reserve_release_keeps_item_dispatchable(self):
        env = self.env
        s, b, c, run = env
        run("configure", {}, changes={"enabled": True})
        reserved = run("event", {"fork": True}, event="reserve", item_id="n3", priority="P1")
        ident = reserved["lease_id"]
        run("event", {"proof": "native spawn denied before launch"}, event="release", lease_id=ident)
        assert ident not in s.read()["leases"]
        assert b["nodes"][2]["status"] == "pending"
        retry = run("event", {"fork": True}, event="reserve", item_id="n3", priority="P1")
        assert retry["lease_id"] != ident

    def test_wait_event_releases_only_execution_slot_and_reacquire_restores_it(self):
        env = self.env
        s, b, c, run = env
        run("configure", {}, changes={"enabled": True, "launches_per_minute": 20})
        ident = launch(env)
        run(
            "event",
            {"agent_id": "agent-1"},
            event="question",
            lease_id=ident,
            plan_version=7,
            evidence="need input",
        )
        waited = run(
            "event",
            {"proof": "native host status: waiting"},
            event="wait",
            lease_id=ident,
        )
        self.assertTrue(waited["slot_released"])
        parked = s.read()["leases"][ident]
        self.assertEqual(parked["state"], "waiting_input")
        self.assertEqual(parked["host_id"], "agent-1")
        self.assertFalse(parked["slot"])
        # The freed slot admits a different queued item; the waiting item itself
        # stays recorded rather than being deleted or marked complete.
        other = run("event", {"fork": True}, event="reserve", item_id="n3", priority="P1")
        self.assertTrue(s.read()["leases"][other["lease_id"]]["slot"])
        reacquired = run("event", {}, event="reacquire", lease_id=ident)
        self.assertTrue(reacquired["slot_reacquired"])
        run(
            "event",
            {},
            event="resume",
            lease_id=ident,
            plan_version=7,
            evidence="input received",
        )
        self.assertEqual(s.read()["leases"][ident]["state"], "running")


    def test_auth_and_default_capability(self):
        env = self.env
        s, b, c, run = env
        assert run("status")["policy"]["enabled"]
        with self.assertRaises(WorkParallelError):
            run("configure", changes={"enabled": True})
        with self.assertRaises(WorkParallelError):
            run("configure", {"token": "forged"}, changes={"enabled": True})
        c["work_parallel_host"]["capability_ready"] = False
        with self.assertRaises(WorkParallelError):
            run("configure", {}, changes={"enabled": True})
        assert s.read()["version"] == 0


    def test_server_gates_1(self):
        env = self.env
        gate = 'approval'
        s, b, c, run = env
        run("configure", {}, changes={"enabled": True})
        if gate == "approval":
            b["phase"] = "planning"
            b["nodes"][0]["phase"] = "planning"
        if gate == "focus":
            b["focus_ids"] = ["other"]
        if gate == "blocked":
            b["nodes"][0]["status"] = "blocked"
        with self.assertRaises(WorkParallelError):
            run(
                "event",
                {"fork": gate != "fork"},
                event="reserve",
                item_id="n1",
                priority="P1",
            )
        assert not s.read()["leases"]


    def test_focus_does_not_block_dispatch(self):
        env = self.env
        s, b, c, run = env
        run("configure", {}, changes={"enabled": True})
        b["focus_ids"] = ["other"]
        reserved = run(
            "event",
            {"fork": True},
            event="reserve",
            item_id="n1",
            priority="P1",
        )
        assert s.read()["leases"][reserved["lease_id"]]["slot"]


    def test_ancestor_wait_denial_names_exact_gate_without_bypassing_it(self):
        store, board, config, run = self.env
        board["nodes"][1]["waiting_on"] = "analysis allowed; implementation approval pending"
        with self.assertRaisesRegex(WorkParallelError, "node n2 has waiting_on"):
            run("event", {"fork": True}, event="reserve", item_id="n3", priority="P1")
        self.assertFalse(store.read()["leases"])
        # The scheduler must not infer an exception from free-form prose.
        board["nodes"][1]["waiting_on"] = ""
        result = run("event", {"fork": True}, event="reserve", item_id="n3", priority="P1")
        self.assertTrue(result["lease_id"])

    def test_server_gates_3(self):
        env = self.env
        gate = 'blocked'
        s, b, c, run = env
        run("configure", {}, changes={"enabled": True})
        if gate == "approval":
            b["phase"] = "planning"
            b["nodes"][0]["phase"] = "planning"
        if gate == "focus":
            b["focus_ids"] = ["other"]
        if gate == "blocked":
            b["nodes"][0]["status"] = "blocked"
        with self.assertRaises(WorkParallelError):
            run(
                "event",
                {"fork": gate != "fork"},
                event="reserve",
                item_id="n1",
                priority="P1",
            )
        assert not s.read()["leases"]


    def test_server_gates_4(self):
        env = self.env
        gate = 'fork'
        s, b, c, run = env
        run("configure", {}, changes={"enabled": True})
        if gate == "approval":
            b["phase"] = "planning"
            b["nodes"][0]["phase"] = "planning"
        if gate == "focus":
            b["focus_ids"] = ["other"]
        if gate == "blocked":
            b["nodes"][0]["status"] = "blocked"
        with self.assertRaises(WorkParallelError):
            run(
                "event",
                {"fork": gate != "fork"},
                event="reserve",
                item_id="n1",
                priority="P1",
            )
        assert not s.read()["leases"]


    def test_worker_authority_and_slot_admission(self):
        env = self.env
        s, b, c, run = env
        ident = launch(env)
        with self.assertRaises(WorkParallelError):
            run("configure", {"agent_id": "agent-1"}, changes={"paused": True})
        with self.assertRaises(WorkParallelError):
            run(
                "event",
                {"agent_id": "other"},
                event="result",
                lease_id=ident,
                plan_version=7,
                artifact="a" * 64,
                evidence="result",
            )
        with self.assertRaises(WorkParallelError):
            run(
                "event",
                {"agent_id": "agent-1"},
                event="accept",
                lease_id=ident,
                plan_version=7,
                evidence="self approval",
            )
        run("event", {}, event="reconcile", lease_id=ident)
        assert s.read()["leases"][ident]["slot"]
        # Caller input cannot demote the root; the root's P1 priority is authoritative.
        reserved = run("event", {"fork": True}, event="reserve", item_id="n3", priority="P2")
        assert s.read()["leases"][reserved["lease_id"]]["priority"] == "P1"
        # Worker and resume share one execution family per item.
        for role in ("worker", "resume"):
            with self.assertRaises(WorkParallelError):
                run("event", {"fork": True}, event="reserve", item_id="n1", priority="P2", role=role)
        # Reviewer forks are retired: main reviews completed results itself.
        with self.assertRaisesRegex(WorkParallelError, "reviewer reservations are retired"):
            run("event", {"fork": True}, event="reserve", item_id="n3", priority="P1", role="reviewer")


    def test_review_cleanup_lifecycle(self):
        env = self.env
        s, b, c, run = env
        ident = launch(env)
        run(
            "event",
            {"agent_id": "agent-1"},
            event="result",
            lease_id=ident,
            plan_version=7,
            artifact="a" * 64,
            evidence="result",
        )
        with self.assertRaises(WorkParallelError):
            run(
                "event",
                {},
                event="done",
                lease_id=ident,
                plan_version=7,
                artifact="a" * 64,
                evidence="premature",
            )
        for event in ("verify", "review"):
            run(
                "event",
                {},
                event=event,
                lease_id=ident,
                plan_version=7,
                artifact="a" * 64,
                evidence="checked",
            )
        with self.assertRaises(WorkParallelError):
            run(
                "event",
                {},
                event="accept",
                lease_id=ident,
                plan_version=7,
                artifact="a" * 64,
                evidence="unchecked",
            )
        with self.assertRaisesRegex(WorkParallelError, "reviewer reservations are retired"):
            run("event", {"fork": True}, event="reserve", item_id="n1", priority="P1", role="reviewer")
        # Legacy reviewer rows written by 4eebfdc/5c91ea8 remain consumable.
        reviewer = _legacy_reviewer(s, "n1", "reviewer-1")
        run(
            "event",
            {},
            event="accept",
            lease_id=ident,
            reviewer_lease_id=reviewer,
            plan_version=7,
            artifact="a" * 64,
            evidence="checked",
        )
        run(
            "event",
            {},
            event="cleanup",
            lease_id=ident,
            plan_version=7,
            artifact="a" * 64,
            evidence="checked",
        )
        run(
            "event",
            {"state": "idle", "proof": "observed terminal host"},
            event="reconcile",
            lease_id=ident,
        )
        run(
            "event",
            {},
            event="done",
            lease_id=ident,
            plan_version=7,
            artifact="a" * 64,
            evidence="checked",
            cleanup_proof="checked cleanup",
        )
        leases = s.read()["leases"]
        assert leases[ident]["state"] == "done"
        assert leases[reviewer]["state"] == "done"
        assert leases[reviewer]["cleanup_proof"]


    def test_stale_board_and_cross_scope(self):
        env = self.env
        s, b, c, run = env
        ident = launch(env)
        b["version"] += 1
        # The global CAS version is not approval identity: unrelated bookkeeping
        # must not strand an in-flight assignment.
        run(
            "event",
            {"agent_id": "agent-1"},
            event="result",
            lease_id=ident,
            plan_version=7,
            artifact="a" * 64,
            evidence="still approved",
        )
        with patch.object(m, "ledger", return_value=s):
            with self.assertRaises(WorkParallelError):
                m.run(
                    "work_parallel_event",
                    {
                        "event": "bind",
                        "lease_id": ident,
                        "_host": {"token": SECRET},
                        "expected_version": s.read()["version"],
                    },
                    None,
                    "claude:other",
                    c,
                    None,
                )
        assert s.read()["leases"][ident]["state"] == "submitted"


    def test_cleanup_keeps_unpushed_worktree_without_user_approval(self):
        env = self.env
        tmp_path = self.tmp_path
        s, b, c, run = env
        ident, repo, target = _accepted_worker(env, tmp_path)
        with self.assertRaisesRegex(WorkParallelError, "push or explicit user approval"):
            run(
                "event",
                _workspace_host(ident, target),
                event="cleanup",
                lease_id=ident,
                workspace=str(target),
                plan_version=7,
                artifact="a" * 64,
                evidence="c",
            )
        # A model-supplied approval flag is not host approval.
        with self.assertRaises(WorkParallelError):
            run(
                "event",
                _workspace_host(ident, target),
                event="cleanup",
                lease_id=ident,
                workspace=str(target),
                approved_target=str(target),
                plan_version=7,
                artifact="a" * 64,
                evidence="c",
            )
        assert target.exists() and s.read()["leases"][ident]["state"] == "accepted"


    def test_cleanup_removes_pushed_worktree_and_proves_absence(self):
        env = self.env
        tmp_path = self.tmp_path
        s, b, c, run = env
        ident, repo, target = _accepted_worker(env, tmp_path)
        _git(repo, "push", "origin", "w1")
        run(
            "event",
            _workspace_host(ident, target),
            event="cleanup",
            lease_id=ident,
            workspace=str(target),
            plan_version=7,
            artifact="a" * 64,
            evidence="c",
        )
        assert not target.exists()
        run(
            "event",
            {},
            event="done",
            lease_id=ident,
            plan_version=7,
            artifact="a" * 64,
            evidence="done",
        )
        lease = s.read()["leases"][ident]
        assert lease["state"] == "done" and '"pushed": true' in lease["cleanup_proof"]


    def test_cleanup_with_host_approval_removes_unpushed(self):
        env = self.env
        tmp_path = self.tmp_path
        s, b, c, run = env
        ident, repo, target = _accepted_worker(env, tmp_path)
        run(
            "event",
            {**_workspace_host(ident, target), "approved_target": str(target)},
            event="cleanup",
            lease_id=ident,
            workspace=str(target),
            plan_version=7,
            artifact="a" * 64,
            evidence="c",
        )
        assert not target.exists()
        assert '"explicit_user_approval": true' in s.read()["leases"][ident]["evidence"]


    def test_invalid_cleanup_never_removes_worktree_1(self):
        env = self.env
        tmp_path = self.tmp_path
        bad = {'expected_version': 0}
        s, b, c, run = env
        ident, repo, target = _accepted_worker(env, tmp_path)
        _git(repo, "push", "origin", "w1")
        args = dict(
            event="cleanup",
            lease_id=ident,
            workspace=str(target),
            plan_version=7,
            artifact="a" * 64,
            evidence="c",
        )
        args.update(bad)
        with self.assertRaises(WorkParallelError):
            run("event", _workspace_host(ident, target), **args)
        assert target.exists()
        assert s.read()["leases"][ident]["state"] == "accepted"


    def test_invalid_cleanup_never_removes_worktree_2(self):
        env = self.env
        tmp_path = self.tmp_path
        bad = {'plan_version': 6}
        s, b, c, run = env
        ident, repo, target = _accepted_worker(env, tmp_path)
        _git(repo, "push", "origin", "w1")
        args = dict(
            event="cleanup",
            lease_id=ident,
            workspace=str(target),
            plan_version=7,
            artifact="a" * 64,
            evidence="c",
        )
        args.update(bad)
        with self.assertRaises(WorkParallelError):
            run("event", _workspace_host(ident, target), **args)
        assert target.exists()
        assert s.read()["leases"][ident]["state"] == "accepted"


    def test_invalid_cleanup_never_removes_worktree_3(self):
        env = self.env
        tmp_path = self.tmp_path
        bad = {'artifact': 'b' * 64}
        s, b, c, run = env
        ident, repo, target = _accepted_worker(env, tmp_path)
        _git(repo, "push", "origin", "w1")
        args = dict(
            event="cleanup",
            lease_id=ident,
            workspace=str(target),
            plan_version=7,
            artifact="a" * 64,
            evidence="c",
        )
        args.update(bad)
        with self.assertRaises(WorkParallelError):
            run("event", _workspace_host(ident, target), **args)
        assert target.exists()
        assert s.read()["leases"][ident]["state"] == "accepted"


    def test_cleanup_without_native_ownership_keeps_pushed_worktree(self):
        env = self.env
        tmp_path = self.tmp_path
        s, b, c, run = env
        ident, repo, target = _accepted_worker(env, tmp_path)
        _git(repo, "push", "origin", "w1")
        with self.assertRaisesRegex(WorkParallelError, "ownership"):
            run(
                "event",
                {},
                event="cleanup",
                lease_id=ident,
                workspace=str(target),
                plan_version=7,
                artifact="a" * 64,
                evidence="c",
            )
        assert target.exists()


    def test_status_is_bounded_and_omits_private_terminal_history(self):
        store, board, config, run = self.env
        doc = store.read()
        template = {
            "id": "", "scope": SCOPE, "item_id": "n3", "priority": "P1", "created_at": 1,
            "role": "worker", "mode": "fork", "plan_version": 7, "state": "unreported",
            "host_id": "agent", "slot": False, "usage": 0, "artifact": None, "rework": 0,
            "evidence": "PRIVATE" * 200, "idle_proof": "terminal", "cleanup_proof": None,
            "workspace": {"path": "C:/PRIVATE/" + "x" * 500, "repo": "C:/PRIVATE"},
            "report_hash": None, "terminal": {"reason": "unreported", "observed_at": 1, "proof": "terminal"},
        }
        for index in range(200):
            ident = f"old-{index}"
            doc["leases"][ident] = {**template, "id": ident, "host_id": f"agent-{index}"}
        store.path.write_text(json.dumps(doc), encoding="utf-8")
        value = run("status")
        encoded = json.dumps(value, ensure_ascii=False)
        self.assertLess(len(encoded), 12_000)
        self.assertNotIn("PRIVATE", encoded)
        self.assertEqual(200, value["lease_counts"]["unreported"])
        self.assertFalse(value["leases"])


if __name__ == "__main__":
    unittest.main()
