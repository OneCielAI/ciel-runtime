"""Focused temporary-ledger/router/host tests; never start runtime sessions."""

import json
from unittest.mock import patch
import pytest
from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.work_parallel import WorkParallelStore, WorkParallelError

SECRET = "test-host-secret-" * 3
SCOPE = "claude:test"


@pytest.fixture
def env(tmp_path):
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
                "phase": "executing",
                "status": "pending",
                "updated_at": 1,
            }
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


def test_auth_and_default_capability(env):
    s, b, c, run = env
    assert not run("status")["policy"]["enabled"]
    with pytest.raises(WorkParallelError):
        run("configure", changes={"enabled": True})
    with pytest.raises(WorkParallelError):
        run("configure", {"token": "forged"}, changes={"enabled": True})
    c["work_parallel_host"]["capability_ready"] = False
    with pytest.raises(WorkParallelError):
        run("configure", {}, changes={"enabled": True})
    assert s.read()["version"] == 0


@pytest.mark.parametrize("gate", ["approval", "focus", "blocked", "fork"])
def test_server_gates(env, gate):
    s, b, c, run = env
    run("configure", {}, changes={"enabled": True})
    if gate == "approval":
        b["phase"] = "planning"
        b["nodes"][0]["phase"] = "planning"
    if gate == "focus":
        b["focus_ids"] = ["other"]
    if gate == "blocked":
        b["nodes"][0]["status"] = "blocked"
    with pytest.raises(WorkParallelError):
        run(
            "event",
            {"fork": gate != "fork"},
            event="reserve",
            item_id="n1",
            priority="P1",
        )
    assert not s.read()["leases"]


def test_unknown_usage_and_worker_authority(env):
    s, b, c, run = env
    ident = launch(env)
    with pytest.raises(WorkParallelError):
        run("configure", {"agent_id": "agent-1"}, changes={"paused": True})
    with pytest.raises(WorkParallelError):
        run(
            "event",
            {"agent_id": "other"},
            event="result",
            lease_id=ident,
            plan_version=7,
            artifact="a" * 64,
            evidence="result",
        )
    with pytest.raises(WorkParallelError):
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
    with pytest.raises(WorkParallelError):
        run("event", {"fork": True}, event="reserve", item_id="n1", priority="P2")


def test_review_cleanup_lifecycle(env):
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
    with pytest.raises(WorkParallelError):
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
    with pytest.raises(WorkParallelError):
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


def test_stale_board_and_cross_scope(env):
    s, b, c, run = env
    ident = launch(env)
    b["version"] += 1
    with pytest.raises(WorkParallelError):
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
        with pytest.raises(WorkParallelError):
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


def test_cleanup_keeps_unpushed_worktree_without_user_approval(env, tmp_path):
    s, b, c, run = env
    ident, repo, target = _accepted_worker(env, tmp_path)
    with pytest.raises(WorkParallelError, match="push or explicit user approval"):
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
    with pytest.raises(WorkParallelError):
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


def test_cleanup_removes_pushed_worktree_and_proves_absence(env, tmp_path):
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


def test_cleanup_with_host_approval_removes_unpushed(env, tmp_path):
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


def _workspace_host(ident, target):
    return {
        "workspace_binding": {
            "host_id": "agent-1",
            "lease_id": ident,
            "idle": True,
            "path": str(target),
        }
    }


@pytest.mark.parametrize(
    "bad", [{"expected_version": 0}, {"plan_version": 6}, {"artifact": "b" * 64}]
)
def test_invalid_cleanup_never_removes_worktree(env, tmp_path, bad):
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
    with pytest.raises(WorkParallelError):
        run("event", _workspace_host(ident, target), **args)
    assert target.exists()
    assert s.read()["leases"][ident]["state"] == "accepted"


def test_cleanup_without_native_ownership_keeps_pushed_worktree(env, tmp_path):
    s, b, c, run = env
    ident, repo, target = _accepted_worker(env, tmp_path)
    _git(repo, "push", "origin", "w1")
    with pytest.raises(WorkParallelError, match="ownership"):
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
