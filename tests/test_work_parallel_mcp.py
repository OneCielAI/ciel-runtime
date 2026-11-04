"""Focused temporary-ledger/router/host tests; never start runtime sessions."""

import json
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
        # No provider usage observation is needed: a second, non-P1 slot is available.
        reserved = run("event", {"fork": True}, event="reserve", item_id="n1", priority="P2", role="reviewer")
        assert s.read()["leases"][reserved["lease_id"]]["priority"] == "P2"
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
