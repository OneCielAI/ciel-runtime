"""Work worktrees in a repository another account owns, and reservation cleanup.

Celly (2026-11-17): the router runs as a sandbox account in a repository the
machine owner owns. Git refused every Work git call ("detected dubious
ownership"), and each failed preparation left a coordination run (ordinary
lane) or the whole reservation (P0 lane) reserved. ``GIT_TEST_ASSUME_DIFFERENT_OWNER``
makes git treat every repository as owned by another account.
"""
import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ciel_runtime_support import runtime_paths
from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support import work_parallel_workspace as ws
from ciel_runtime_support.work_parallel import WorkParallelError, WorkParallelStore
from ciel_runtime_support.work_parallel_failures import (MESSAGES, WORKSPACE_CODES, WorkParallelFailureLog,
                                                         public_failures)
from ciel_runtime_support.work_parallel_p0 import p0_store

SECRET = "trust-host-secret-" * 3
SCOPE = "claude:trust"
# GitHub's Ubuntu runner images write "[safe] directory = *" to /etc/gitconfig
# (actions/runner-images images/ubuntu/scripts/build/install-git.sh), so on CI
# git never refused (run 44189662051). Only command-scope trust may count here:
# the system file is skipped and the global file is an empty one.
_EMPTY_GLOBAL = TemporaryDirectory(prefix="ciel-trust-gitconfig-")
(Path(_EMPTY_GLOBAL.name) / "gitconfig").write_text("", encoding="utf-8")
FOREIGN = {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": str(Path(_EMPTY_GLOBAL.name) / "gitconfig")}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def make_repo(path):
    path.mkdir(parents=True)
    git(path, "init", "-q")
    git(path, "config", "user.name", "fixture")
    git(path, "config", "user.email", "fixture@example.invalid")
    (path / "base.txt").write_text("base", encoding="utf-8")
    git(path, "add", "base.txt")
    git(path, "commit", "-q", "-m", "base")
    return path.resolve()


class Board:
    def __init__(self, phase):
        self.doc = {"version": 1, "phase": phase, "active_root": "root", "focus_ids": [], "nodes": [
            {"id": "root", "parent_id": None, "title": "goal", "status": "pending", "note": "",
             "progress": "", "evidence": "", "waiting_on": "", "ref": "", "urgent": False,
             "updated_at": 1.0, "created_at": 1.0, "phase": phase, "priority": "P1"},
            {"id": "item", "parent_id": "root", "title": "item", "status": "pending", "note": "",
             "progress": "", "evidence": "", "waiting_on": "", "ref": "", "urgent": False,
             "updated_at": 1.0, "created_at": 1.0}]}

    def read(self):
        return self.doc


class Base(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name).resolve()
        self.repo = make_repo(self.tmp / "repo")
        self.config_dir = self.tmp / "cfg"
        self._patch(patch.object(runtime_paths, "CONFIG_DIR", self.config_dir))
        self.store = WorkParallelStore(self.tmp / "work-parallel.json")
        self.config = {"work_parallel_host": {"token": SECRET, "capability_ready": True}}
        self.lines = []

    def _patch(self, patcher):
        # TestCase.enterContext is Python 3.11+; CI also runs a unit shard on 3.10.
        patcher.start()
        self.addCleanup(patcher.stop)

    def launch_folder(self, path):
        self._patch(patch.object(runtime_paths, "ROUTER_WORKSPACE", str(path)))

    def call(self, board, event, host=None, **args):
        args.setdefault("expected_version", self.store.read()["version"])
        args["event"] = event
        args["_host"] = {"token": SECRET, **(host or {})}
        with patch.object(m, "ledger", return_value=self.store):
            return json.loads(m.run("work_parallel_event", args, board, SCOPE, self.config, None,
                                    log=lambda level, text: self.lines.append((level, text))))

    def runs(self):
        return m.coordination(self.store).read()["runs"]

    def failures(self):
        return [row["code"] for row in public_failures(self.store.path, SCOPE)["failures"]]


class TrustedRootTests(Base):
    def test_only_the_launch_folder_and_its_work_worktrees_are_trusted(self):
        self.launch_folder(self.repo)
        owned = self.repo / ".claude" / "worktrees" / ("work-parallel-" + "a" * 24)
        self.assertEqual((self.repo,), ws.trusted_roots(self.repo))
        self.assertEqual((owned,), ws.trusted_roots(owned))
        for other in (self.repo.parent, self.repo / "sub", self.repo / ".claude" / "worktrees" / "other",
                      self.repo / ".claude" / "worktrees" / ("work-parallel-" + "a" * 23),
                      self.tmp / "elsewhere" / ".claude" / "worktrees" / ("work-parallel-" + "a" * 24)):
            self.assertEqual((), ws.trusted_roots(other), other)

    def test_trust_arguments_name_exact_roots_and_refuse_wildcards(self):
        self.assertEqual(["-c", "safe.directory=" + str(self.repo)], ws.trust_arguments((self.repo,)))
        for bad in ("*", str(self.repo) + "/*", "relative/path", ""):
            with self.assertRaises(ws.WorkspaceError):
                ws.trust_arguments((bad,))

    def test_every_git_call_of_a_prepare_carries_the_exact_root(self):
        self.launch_folder(self.repo)
        seen = []
        real = subprocess.run

        def record(argv, *args, **kwargs):
            if argv and argv[0] == "git":
                seen.append(list(argv))
            return real(argv, *args, **kwargs)
        with patch.object(ws.subprocess, "run", side_effect=record):
            made = ws.prepare(self.repo, "lease-a", config_dir=self.config_dir)
        self.assertTrue(seen)
        for argv in seen:
            values = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "-c"
                      and argv[i + 1].startswith("safe.directory=")]
            self.assertEqual(1, len(values), argv)
            self.assertNotIn("*", values[0])
            cwd = argv[argv.index("-C") + 1]
            self.assertEqual("safe.directory=" + cwd, values[0])
            self.assertIn(Path(cwd), (self.repo, Path(made["path"])))


class ForeignOwnerTests(Base):
    def test_launch_folder_owned_by_another_account_gets_a_worktree(self):
        self.launch_folder(self.repo)
        with patch.dict(os.environ, FOREIGN):
            with self.assertRaisesRegex(subprocess.CalledProcessError, ""):
                git(self.repo, "status")  # the fixture really is refused without trust
            made = ws.prepare(self.repo, "lease-a", config_dir=self.config_dir)
            self.assertTrue(Path(made["path"]).is_dir())
            observed = ws.observe(self.repo, "lease-a", config_dir=self.config_dir)
            self.assertEqual(made["head"], observed["head"])
            ws.bind(self.repo, "lease-a", "agent-1", config_dir=self.config_dir)
            ws.set_activity(self.repo, "lease-a", "agent-1", idle=True, proof="ended",
                            config_dir=self.config_dir)
            removed = ws.remove(self.repo, "lease-a", host_id="agent-1", active=False, idle_proof="ended",
                                expected_head=observed["head"], approved_target=made["path"],
                                config_dir=self.config_dir)
        self.assertEqual("removed", removed["status"])
        self.assertFalse(Path(made["path"]).exists())

    def test_other_folder_owned_by_another_account_stays_refused_and_clean(self):
        self.launch_folder(self.tmp / "launched-elsewhere")
        with patch.dict(os.environ, FOREIGN):
            with self.assertRaises(ws.WorkspaceError) as caught:
                ws.prepare(self.repo, "lease-a", config_dir=self.config_dir)
        self.assertIn(ws.DUBIOUS_OWNERSHIP, str(caught.exception))
        self.assertTrue(caught.exception.clean)
        self.assertFalse((self.config_dir / "work-parallel-workspaces").exists()
                         and any((self.config_dir / "work-parallel-workspaces").glob("*.json")))
        self.assertFalse((self.repo / ".claude").exists())


class OrdinaryLaneTests(Base):
    def setUp(self):
        super().setUp()
        self.board = Board("executing")
        with patch.object(m, "ledger", return_value=self.store):
            m.run("work_parallel_configure", {"changes": {"enabled": True},
                                              "expected_version": self.store.read()["version"],
                                              "_host": {"token": SECRET}}, self.board, SCOPE, self.config, None)

    def reserve(self):
        return self.call(self.board, "reserve", {"fork": True, "workspace_repo": str(self.repo)},
                         item_id="item", priority="P1")

    def test_untrusted_failure_releases_lease_and_run_and_is_recorded(self):
        self.launch_folder(self.tmp / "launched-elsewhere")
        with patch.dict(os.environ, FOREIGN):
            for _ in range(3):
                with self.assertRaisesRegex(WorkParallelError, "workspace_git_untrusted.*reservation released"):
                    self.reserve()
        self.assertEqual({}, self.store.read()["leases"])
        self.assertEqual({}, self.runs())
        self.assertEqual(["workspace_git_untrusted"], self.failures())
        self.assertEqual(3, public_failures(self.store.path, SCOPE)["failures"][0]["count"])
        self.assertIn("message", public_failures(self.store.path, SCOPE)["failures"][0])
        self.assertTrue(any(level == "WARN" and "work_workspace_failed code=workspace_git_untrusted" in text
                            and ws.DUBIOUS_OWNERSHIP in text for level, text in self.lines))

    def test_launch_folder_owned_by_another_account_reserves_with_a_workspace(self):
        self.launch_folder(self.repo)
        with patch.dict(os.environ, FOREIGN):
            reserved = self.reserve()
        self.assertTrue(Path(reserved["workspace"]["path"]).is_dir())
        self.assertEqual("reserved", self.runs()[reserved["lease_id"]]["state"])
        self.assertIsNotNone(self.runs()[reserved["lease_id"]]["workspace"])


class P0LaneTests(Base):
    def setUp(self):
        super().setUp()
        self.board = Board("planning")
        p0_store(self.store).activate_root(SCOPE, "root", 1.0, set_by="user", expected_version=0, now=1.0)

    def reserve(self):
        return self.call(self.board, "p0_reserve_next", {"fork": True, "workspace_repo": str(self.repo)},
                         role="worker")

    def test_clean_failure_returns_the_intent_and_frees_the_slot(self):
        self.launch_folder(self.tmp / "launched-elsewhere")
        with patch.dict(os.environ, FOREIGN):
            for _ in range(3):
                with self.assertRaisesRegex(WorkParallelError, "workspace_git_untrusted"):
                    self.reserve()
        self.assertEqual({}, self.store.read()["leases"])
        self.assertEqual({}, self.runs())
        intents = list(p0_store(self.store).read()["intents"].values())
        # One intent for the item across every attempt: the P0 file never grows.
        self.assertEqual(1, len(intents))
        self.assertEqual(("intent", None), (intents[0]["state"], intents[0]["lease_id"]))
        # Trusted now: the same intent launches with a fresh lease and worktree.
        self.launch_folder(self.repo)
        with patch.dict(os.environ, FOREIGN):
            reserved = self.reserve()
        self.assertEqual(intents[0]["intent_id"], reserved["intent_id"])
        self.assertEqual("reserved", p0_store(self.store).read()["intents"][reserved["intent_id"]]["state"])
        self.assertTrue(Path(reserved["workspace"]["path"]).is_dir())


class FailureLogTests(Base):
    def test_workspace_codes_live_in_their_own_file_and_merge_with_texts(self):
        first = WorkParallelFailureLog.beside(self.store.path)
        first.record(SCOPE, "revival_refused", dedup_key="a", now=1_800_000_000)
        own = WorkParallelFailureLog.workspace_beside(self.store.path)
        for code in WORKSPACE_CODES:
            own.record(SCOPE, code, dedup_key=code, now=1_800_000_100)
        # The shared first file still holds only codes every older build knows.
        with self.assertRaises(WorkParallelError):
            first.record(SCOPE, "workspace_git_untrusted")
        with self.assertRaises(WorkParallelError):
            own.record(SCOPE, "revival_refused")
        view = public_failures(self.store.path, SCOPE)
        self.assertEqual(1, view["failures_version"])
        self.assertEqual(set(WORKSPACE_CODES) | {"revival_refused"}, {row["code"] for row in view["failures"]})
        for row in view["failures"]:
            if row["code"] in WORKSPACE_CODES:
                self.assertEqual(MESSAGES[row["code"]], row["message"])
        self.assertEqual("revival_refused", view["failures"][-1]["code"])

    def test_an_unreadable_workspace_log_hides_only_its_rows(self):
        WorkParallelFailureLog.beside(self.store.path).record(SCOPE, "owner_live", dedup_key="a")
        WorkParallelFailureLog.workspace_beside(self.store.path).path.write_text("{", encoding="utf-8")
        self.assertEqual(["owner_live"], [row["code"] for row in public_failures(self.store.path, SCOPE)["failures"]])


class ReconcileTests(Base):
    """Leaked halves exactly as Celly's builds left them."""

    def setUp(self):
        super().setUp()
        self.board = Board("planning")
        side = p0_store(self.store)
        side.activate_root(SCOPE, "root", 1.0, set_by="user", expected_version=0, now=1.0)

    def leak_p0(self, state):
        reserved = self.call(self.board, "p0_reserve_next", {"fork": True}, role="worker")
        args = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        if state == "spawning":
            self.call(self.board, "p0_launch_assignment", {"internal": True}, **args)
            self.call(self.board, "p0_launch_spawning", {"internal": True}, **args)
        current = self.store.read()
        self.store.release_unbound(reserved["lease_id"], proof="leak fixture", expected_version=current["version"])
        return reserved

    def test_preview_apply_backup_and_idempotence(self):
        from ciel_runtime_support import work_parallel_reconcile as reconcile
        leaked = self.leak_p0("reserved")
        view = reconcile.preview(self.store)
        self.assertEqual(1, view["orphan_run_count"])
        self.assertEqual(1, view["orphan_p0_intent_count"])
        self.assertEqual([leaked["lease_id"]], view["orphan_runs"])
        with self.assertRaisesRegex(WorkParallelError, "changed since the preview"):
            reconcile.apply(self.store, expected_digest="0" * 64)
        done = reconcile.apply(self.store, expected_digest=view["digest"])
        self.assertEqual((1, 1), (done["cancelled_runs"], done["cancelled_p0_intents"]))
        backup = Path(done["backup"])
        self.assertTrue((backup / "work-parallel.json").is_file())
        self.assertTrue((backup / "work-parallel-p0.json").is_file())
        self.assertEqual({}, self.runs())
        archived = m.coordination(self.store).read()["archive"][-1]
        self.assertEqual(reconcile.RUN_OUTCOME, archived["summary"]["outcome"])
        self.assertEqual("cancelled", p0_store(self.store).read()["intents"][leaked["intent_id"]]["state"])
        again = reconcile.apply(self.store)
        self.assertEqual((0, 0, None), (again["cancelled_runs"], again["cancelled_p0_intents"], again["backup"]))

    def test_spawning_intent_without_lease_is_left_and_listed(self):
        from ciel_runtime_support import work_parallel_reconcile as reconcile
        leaked = self.leak_p0("spawning")
        view = reconcile.preview(self.store)
        self.assertEqual(0, view["orphan_p0_intent_count"])
        self.assertEqual([leaked["intent_id"]], view["ambiguous_p0_intents"])
        reconcile.apply(self.store)
        self.assertEqual("spawning", p0_store(self.store).read()["intents"][leaked["intent_id"]]["state"])

    def test_a_live_reservation_is_never_touched(self):
        from ciel_runtime_support import work_parallel_reconcile as reconcile
        reserved = self.call(self.board, "p0_reserve_next", {"fork": True}, role="worker")
        view = reconcile.preview(self.store)
        self.assertEqual((0, 0, 0), (view["orphan_run_count"], view["orphan_p0_intent_count"],
                                     view["ambiguous_p0_intent_count"]))
        reconcile.apply(self.store)
        self.assertIn(reserved["lease_id"], self.runs())

    def test_launch_sweep_retires_the_halves_its_own_release_leaves(self):
        from ciel_runtime_support import work_parallel_orphan_sweep as sweep
        self.leak_p0("reserved")
        lines = []
        with patch.object(m, "ledger", return_value=self.store):
            sweep.sweep_at_launch(lambda level, text: lines.append((level, text)))
        self.assertEqual({}, self.runs())
        self.assertTrue(any("work_reservation_reconciled runs=1 p0_intents=1" in text for _, text in lines))

    def test_many_leaked_runs_retire_in_one_coordination_write(self):
        from ciel_runtime_support import work_parallel_reconcile as reconcile
        coord = m.coordination(self.store)
        self.leak_p0("reserved")
        # Celly held 1,753 such runs; clone the one real leaked run 1,500 times.
        state = coord.read()
        before = state["version"]
        template = next(iter(state["runs"].values()))
        for index in range(1500):
            state["runs"]["leak%04d" % index] = {**template, "lease_id": "leak%04d" % index}
        Path(coord.path).write_text(json.dumps({**state, "version": before + 1}), encoding="utf-8")
        view = reconcile.preview(self.store)
        self.assertEqual(1501, view["orphan_run_count"])
        self.assertTrue(view["truncated"])
        done = reconcile.apply(self.store)
        self.assertEqual(1501, done["cancelled_runs"])
        self.assertEqual(before + 2, coord.read()["version"])
        self.assertEqual({}, coord.read()["runs"])


if __name__ == "__main__":
    unittest.main()
