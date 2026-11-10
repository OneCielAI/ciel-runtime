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
    reviewer = run(
        "event",
        {"fork": True},
        event="reserve",
        item_id="n1",
        priority="P1",
        role="reviewer",
    )["lease_id"]
    run("event", {"host_id": "reviewer-1"}, event="bind", lease_id=reviewer)
    run(
        "event",
        {"agent_id": "reviewer-1"},
        event="result",
        lease_id=reviewer,
        plan_version=7,
        artifact="b" * 64,
        evidence="ok",
    )
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
            run("event", {"host_id": "native-worker", "agent_name": "owned-name"}, event="bind", lease_id=ident)
            self.assertEqual(store.read()["leases"][ident]["agent_name"], "owned-name")
            run("event", {"state": "idle", "proof": "native terminal"}, event="reconcile", lease_id=ident)
            record = observe(repo, ident)
            self.assertTrue(record["idle"])
            self.assertEqual(record["host_id"], "native-worker")

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

    def test_reserve_next_skips_waiting_leaf_and_returns_next_runnable(self):
        store, board, config, run = self.env
        board["nodes"][2]["waiting_on"] = "review"
        board["nodes"].append({"id": "n4", "parent_id": "n2", "title": "Second item",
                               "note": "Do second", "status": "pending", "updated_at": 2})
        reserved = run("event", {"fork": True, "dispatch_name": "auto-one"}, event="reserve_next")
        self.assertEqual(reserved["item_id"], "n4")
        self.assertEqual(reserved["priority"], "P1")
        self.assertEqual(reserved["item_note"], "Do second")

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
        reserved = run("event", {"fork": True}, event="reserve", item_id="n1", priority="P2", role="reviewer")
        assert s.read()["leases"][reserved["lease_id"]]["priority"] == "P1"
        with self.assertRaises(WorkParallelError):
            run("event", {"fork": True}, event="reserve", item_id="n1", priority="P2", role="reviewer")


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
        reviewer = run(
            "event",
            {"fork": True},
            event="reserve",
            item_id="n1",
            priority="P1",
            role="reviewer",
        )["lease_id"]
        run("event", {"host_id": "reviewer-1"}, event="bind", lease_id=reviewer)
        run(
            "event",
            {"agent_id": "reviewer-1"},
            event="result",
            lease_id=reviewer,
            plan_version=7,
            artifact="b" * 64,
            evidence="independent findings",
        )
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
        assert s.read()["leases"][ident]["state"] == "done"


    def test_stale_board_and_cross_scope(self):
        env = self.env
        s, b, c, run = env
        ident = launch(env)
        b["version"] += 1
        with self.assertRaises(WorkParallelError):
            run(
                "event",
                {"agent_id": "agent-1"},
                event="result",
                lease_id=ident,
                plan_version=7,
                artifact="a" * 64,
                evidence="stale",
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
        assert s.read()["leases"][ident]["state"] == "running"


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


if __name__ == "__main__":
    unittest.main()
