"""Main-session review and roll-forward finalization; temp config, no live runtime."""
import json
import subprocess
import time
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_parallel import WorkParallelError, WorkParallelStore
from ciel_runtime_support.work_parallel_finalization import STAGES, WorkParallelFinalizationStore
from ciel_runtime_support.work_parallel_workspace import WorkspaceError
from ciel_runtime_support.work_plan_store import WorkPlanStore

SECRET = "review-host-secret-" * 3
SCOPE = "claude:main-review"
LAUNCH_HOST = {"internal": True, "launch_nonce": "process-1"}
SOURCE_ROOT = Path(__file__).resolve().parents[1]
LEGACY_COMMITS = ("4eebfdc", "5c91ea8")


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def legacy_module(commit):
    """Load the exact work_parallel.py an old live build runs, without installing it."""
    try:
        source = subprocess.run(["git", "-C", str(SOURCE_ROOT), "show",
                                 commit + ":ciel_runtime_support/work_parallel.py"],
                                check=True, capture_output=True, text=True, encoding="utf-8").stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise unittest.SkipTest("legacy source unavailable: " + commit) from error
    name = "ciel_runtime_support._legacy_work_parallel_" + commit
    module = types.ModuleType(name)
    module.__package__ = "ciel_runtime_support"
    exec(compile(source, name, "exec"), module.__dict__)
    return module


class Board:
    def __init__(self, path):
        self.store = WorkPlanStore(path)
        doc = self.store.read()
        doc, root = self.store.transaction(lambda d: tree.set_plan(d, {
            "title": "Goal", "note": "", "priority": "P1", "children": [
                {"title": "Item", "note": "", "ref": "", "waiting_on": "", "children": []},
                {"title": "Item two", "note": "", "ref": "", "waiting_on": "", "children": []},
            ]}, None), expected_version=doc["version"])
        self.root = root
        self.item = next(n["id"] for n in doc["nodes"] if n["parent_id"] == root and n["title"] == "Item")
        self.item2 = next(n["id"] for n in doc["nodes"] if n["parent_id"] == root and n["title"] == "Item two")
        self.store.transaction(lambda d: tree.set_phase(d, "executing", root),
                               expected_version=doc["version"])

    def read(self):
        return self.store.read()

    def transaction(self, *args, **kwargs):
        return self.store.transaction(*args, **kwargs)


class Crash(RuntimeError):
    pass


class MainReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.repo = self.tmp / "repo"
        git(self.tmp, "init", str(self.repo))
        git(self.repo, "config", "user.name", "fixture")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        (self.repo / "f").write_text("base")
        git(self.repo, "add", "f")
        git(self.repo, "commit", "-m", "base")
        self.board = Board(self.tmp / "work-plan.json")
        self.store = WorkParallelStore(self.tmp / "work-parallel.json")
        self.config = {"work_parallel_host": {"token": SECRET, "capability_ready": True}}
        runtime_patch = patch("ciel_runtime_support.runtime_paths.CONFIG_DIR", self.tmp / "config")
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)

    def call(self, name, host=None, **args):
        if name != "item_review":
            args.setdefault("expected_version", self.store.read()["version"])
        args["_host"] = {"token": SECRET, **(host or {})}
        with patch.object(m, "ledger", return_value=self.store):
            return json.loads(m.run("work_parallel_" + name, args, self.board, SCOPE,
                                    self.config, None))

    def completed(self, item_id=None, worker_id="worker-1"):
        item_id = item_id or self.board.item
        reserve = self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                            event="reserve", item_id=item_id, priority="P1")
        self.call("event", {"host_id": worker_id}, event="bind", lease_id=reserve["lease_id"])
        workspace = Path(reserve["workspace"]["path"])
        (workspace / "result.txt").write_text("result")
        artifact = "a" * 64
        with patch.object(m, "ledger", return_value=self.store):
            m.report({"lease_id": reserve["lease_id"], "worker_token": reserve["worker_token"],
                      "event": "completion", "plan_version": self.board.read()["version"],
                      "artifact": artifact, "evidence": "tests pass",
                      "detail": {"summary": "done", "coverage": [{"item_id": item_id,
                                 "verified": True}], "verification": "tests pass"}},
                     self.board, self.config)
        return reserve, workspace, artifact

    def idle(self, lease_id):
        return self.call("event", {"state": "idle", "proof": "native completed"},
                         event="reconcile", lease_id=lease_id)

    def pending_version(self):
        return self.call("status", {})["review"]["version"]

    def review(self, lease_id, decision, decision_id="decision-1", evidence="main verified", **host):
        return self.call("item_review", host, lease_id=lease_id, decision=decision,
                         decision_id=decision_id, evidence=evidence,
                         expected_finalization_version=self.pending_version())

    def review_row(self, lease_id):
        return WorkParallelFinalizationStore(self.tmp / "work-parallel-finalization.json").read()["reviews"].get(lease_id)

    def node(self):
        return next(n for n in self.board.read()["nodes"] if n["id"] == self.board.item)

    # Projection --------------------------------------------------------------

    def test_projection_waits_for_receipt_and_positive_idle_then_is_bounded(self):
        reserve, workspace, artifact = self.completed()
        # Completion alone (slot released, no positive idle) publishes nothing.
        self.assertEqual([], self.call("status", {})["review"]["pending"])
        self.idle(reserve["lease_id"])
        status = self.call("status", {})
        self.assertEqual(1, len(status["review"]["pending"]))
        row = status["review"]["pending"][0]
        self.assertEqual(artifact, row["artifact"])
        self.assertEqual("done", row["summary"])
        self.assertEqual("tests pass", row["verification"])
        self.assertEqual(git(workspace, "rev-parse", "HEAD"), row["workspace"]["head"])
        self.assertIn("result.txt", row["workspace"]["changed_files"])
        self.assertIsNone(row["decision"])
        self.assertEqual(("Item", ["Goal", "Item"]), (row["item_title"], row["item_path"]))
        self.assertEqual(str(workspace), row["workspace"]["path"])
        encoded = json.dumps(status["review"])
        self.assertNotIn(reserve["worker_token"], encoded)
        # The model-facing review projection carries no capability, saga identity,
        # repo root or idle proof.
        for private in ("report_hash", "idle_proof", "receipt_digest", "snapshot_digest", '"repo"',
                        str(self.repo).replace("\\", "\\\\") + '"'):
            self.assertNotIn(private, encoded)

    def test_projection_is_bounded_for_large_diffs(self):
        reserve = self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                            event="reserve", item_id=self.board.item, priority="P1")
        self.call("event", {"host_id": "worker-1"}, event="bind", lease_id=reserve["lease_id"])
        workspace = Path(reserve["workspace"]["path"])
        for index in range(150):
            (workspace / (f"bulk-{index:03}-" + "n" * 150 + ".txt")).write_text("x")
        with patch.object(m, "ledger", return_value=self.store):
            m.report({"lease_id": reserve["lease_id"], "worker_token": reserve["worker_token"],
                      "event": "completion", "plan_version": self.board.read()["version"],
                      "artifact": "a" * 64, "evidence": "ok",
                      "detail": {"summary": "s" * 2000, "coverage": [{"item_id": "i%d" % i, "verified": True}
                                 for i in range(100)], "verification": "v" * 2000}}, self.board, self.config)
        self.idle(reserve["lease_id"])
        row = self.call("status", {})["review"]["pending"][0]
        self.assertEqual(150, row["workspace"]["changed_count"])
        self.assertEqual(20, len(row["workspace"]["changed_files"]))
        self.assertEqual((100, 32), (row["coverage_count"], len(row["coverage"])))
        self.assertLess(len(json.dumps(row)), 16_000)

    # Approve -----------------------------------------------------------------

    def test_approve_rolls_forward_board_legacy_lease_and_archive(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        result = self.review(reserve["lease_id"], "approve")
        self.assertEqual("finalized", result["state"])
        lease = self.store.read()["leases"][reserve["lease_id"]]
        self.assertEqual("done", lease["state"])
        self.assertIn("retained verified workspace", lease["cleanup_proof"])
        self.assertEqual("done", self.node()["status"])
        coord = m.coordination(self.store).read()
        self.assertNotIn(reserve["lease_id"], coord["runs"])
        self.assertEqual("main accepted", coord["archive"][-1]["summary"]["outcome"])
        self.assertTrue(workspace.exists())
        self.assertTrue(all(self.review_row(reserve["lease_id"])["stages"].values()))
        self.assertEqual([], self.call("status", {})["review"]["pending"])
        # Identical replay of a finished decision is a no-op answer.
        replay = self.call("item_review", {}, lease_id=reserve["lease_id"], decision="approve",
                           decision_id="decision-1", evidence="main verified",
                           expected_finalization_version=0)
        self.assertEqual("finalized", replay["state"])

    def test_self_accept_by_worker_or_fork_is_denied(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        before = self.pending_version()
        for actor in ("worker-1", "some-other-fork"):
            with self.assertRaisesRegex(WorkParallelError, "main-session"):
                self.review(reserve["lease_id"], "approve", agent_id=actor)
        self.assertEqual(before, self.pending_version())
        self.assertEqual("submitted", self.store.read()["leases"][reserve["lease_id"]]["state"])
        # A worker capability cannot reach the review path either.
        with patch.object(m, "ledger", return_value=self.store):
            with self.assertRaises(WorkParallelError):
                m.report({"lease_id": reserve["lease_id"], "worker_token": reserve["worker_token"],
                          "event": "review", "evidence": "self"}, self.board, self.config)

    def test_unauthenticated_review_is_denied(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        with patch.object(m, "ledger", return_value=self.store):
            with self.assertRaisesRegex(WorkParallelError, "authenticated parallel host"):
                m.run("work_parallel_item_review", {"lease_id": reserve["lease_id"], "decision": "approve",
                      "decision_id": "x", "evidence": "x", "expected_finalization_version": 1,
                      "_host": {"token": "wrong" * 10}}, self.board, SCOPE, self.config, None)

    def test_stale_artifact_head_and_fingerprint_leave_result_pending(self):
        cases = {}

        def stale_artifact(lease_id, workspace):
            doc = self.store.read()
            doc["leases"][lease_id]["artifact"] = "c" * 64
            self.store.path.write_text(json.dumps(doc), encoding="utf-8")
            return "stale artifact"

        def stale_head(lease_id, workspace):
            (workspace / "f").write_text("changed")
            git(workspace, "add", "f")
            git(workspace, "commit", "-m", "changed")
            return "stale workspace"

        def stale_tree(lease_id, workspace):
            (workspace / "late.txt").write_text("late")
            return "stale workspace"

        def stale_fingerprint(lease_id, workspace):
            self.board.transaction(lambda d: tree.update_node(d, self.board.item, title="Item changed"),
                                   expected_version=self.board.read()["version"])
            self.board.transaction(lambda d: tree.set_phase(d, "executing", self.board.root),
                                   expected_version=self.board.read()["version"])
            return "approval"

        cases.update(artifact=stale_artifact, head=stale_head, tree=stale_tree, fingerprint=stale_fingerprint)
        for label, mutate in cases.items():
            with self.subTest(label):
                self.setUp()
                reserve, workspace, artifact = self.completed()
                self.idle(reserve["lease_id"])
                version = self.pending_version()
                pattern = mutate(reserve["lease_id"], workspace)
                for decision in ("approve", "rework"):
                    with self.assertRaisesRegex(WorkParallelError, pattern):
                        self.call("item_review", {}, lease_id=reserve["lease_id"], decision=decision,
                                  decision_id="d-" + decision, evidence="e",
                                  expected_finalization_version=version)
                row = self.review_row(reserve["lease_id"])
                self.assertIsNone(row["decision"])
                self.assertEqual("pending", row["state"])
                self.assertNotEqual("done", self.node()["status"])
                self.assertEqual(1, sum(1 for r in self.store.read()["leases"].values()
                                        if r["item_id"] == self.board.item))

    def test_stale_finalization_version_and_conflicting_decision_are_refused(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        version = self.pending_version()
        with self.assertRaisesRegex(WorkParallelError, "changed; reread"):
            self.call("item_review", {}, lease_id=reserve["lease_id"], decision="approve",
                      decision_id="d", evidence="e", expected_finalization_version=version + 5)
        self.review(reserve["lease_id"], "approve", decision_id="first")
        with self.assertRaisesRegex(WorkParallelError, "decided differently"):
            self.review(reserve["lease_id"], "rework", decision_id="second")

    # Rework ------------------------------------------------------------------

    def test_rework_rebinds_retained_workspace_and_host_dispatch_mints_capability(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        result = self.review(reserve["lease_id"], "rework", evidence="fix assertion")
        self.assertEqual("rework_ready", result["state"])
        self.assertEqual({"kind", "lease_id", "role", "generation", "workspace"}, set(result["dispatch"]))
        self.assertEqual(("resume", 2), (result["dispatch"]["role"], result["dispatch"]["generation"]))
        self.assertNotIn("worker_token", json.dumps(result))
        replacement = result["dispatch"]["lease_id"]
        ledger = self.store.read()["leases"]
        self.assertEqual(("resume", "reserved", True, 1, None),
                         (ledger[replacement]["role"], ledger[replacement]["state"], ledger[replacement]["slot"],
                          ledger[replacement]["rework"], ledger[replacement]["report_hash"]))
        self.assertEqual(str(workspace), ledger[replacement]["workspace"]["path"])
        self.assertEqual("done", ledger[reserve["lease_id"]]["state"])
        coord = m.coordination(self.store).read()
        self.assertEqual(2, coord["runs"][replacement]["generation"])
        self.assertEqual(replacement, coord["archive"][-1]["run"]["replaced_by"])
        # The board item stays open for the replacement generation.
        self.assertNotEqual("done", self.node()["status"])
        # Only an authenticated coordinator fork host can mint the capability.
        with self.assertRaises(WorkParallelError):
            self.call("event", {"agent_id": "worker-1", "fork": True}, event="rework_dispatch",
                      lease_id=replacement)
        dispatched = self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        self.assertEqual(64, len(dispatched["worker_token"]))
        self.assertNotIn(dispatched["worker_token"], self.store.path.read_text(encoding="utf-8"))
        self.call("event", LAUNCH_HOST, event="rework_launch_intent", lease_id=replacement,
                  dispatch_name="resume-exact", generation=2)
        self.call("event", LAUNCH_HOST, event="rework_spawn_observed", lease_id=replacement,
                  dispatch_name="resume-exact", agent_id="worker-2")
        # The replacement runs on the retained workspace and its new capability works.
        self.call("event", {"host_id": "worker-2"}, event="bind", lease_id=replacement)
        with patch.object(m, "ledger", return_value=self.store):
            reported = json.loads(m.report({"lease_id": replacement, "worker_token": dispatched["worker_token"],
                "event": "completion", "plan_version": dispatched["plan_version"], "artifact": "d" * 64,
                "evidence": "fixed", "detail": {"summary": "fixed", "coverage": [{"item_id": self.board.item,
                "verified": True}], "verification": "fixed"}}, self.board, self.config))
        self.assertEqual("submitted", reported["state"])

    # A rework predecessor stays visible only until its replacement generation
    # publishes its own review row; the replacement then carries the item.

    def _projected_ids(self):
        return [row["lease_id"] for row in self.call("status", {})["review"]["pending"]]

    def _replacement_completed(self, replacement, agent, artifact, decision_id):
        """Dispatch, launch, bind, report and idle one resume replacement."""
        token = self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)["worker_token"]
        name = "resume-" + decision_id
        self.call("event", LAUNCH_HOST, event="rework_launch_intent", lease_id=replacement,
                  dispatch_name=name, generation=self.store.read()["leases"][replacement]["rework"] + 1)
        self.call("event", {"host_id": agent, "agent_name": name}, event="bind", lease_id=replacement)
        dispatched_plan = self.store.read()["leases"][replacement]["plan_version"]
        # The receipt digest is the detail body, so each generation reports its own.
        with patch.object(m, "ledger", return_value=self.store):
            m.report({"lease_id": replacement, "worker_token": token, "event": "completion",
                      "plan_version": dispatched_plan, "artifact": artifact, "evidence": "fixed",
                      "detail": {"summary": "fixed " + decision_id,
                                 "coverage": [{"item_id": self.board.item, "verified": True}],
                                 "verification": "fixed " + decision_id}}, self.board, self.config)
        return token

    def test_rework_predecessor_stays_projected_until_its_replacement_publishes_a_review(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        replacement = self.review(reserve["lease_id"], "rework", decision_id="first")["dispatch"]["lease_id"]
        # Reserved, no launch yet: the predecessor is the pump's rework row.
        self.assertEqual([reserve["lease_id"]], self._projected_ids())
        self.assertEqual(("rework_ready", "none"), (self.review_row(reserve["lease_id"])["state"],
                                                    self.review_row(reserve["lease_id"])["launch"]["state"]))
        token = self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)["worker_token"]
        self.call("event", LAUNCH_HOST, event="rework_launch_intent", lease_id=replacement,
                  dispatch_name="resume-first", generation=2)
        # Launch intent: still shown.
        self.assertEqual("launch_intent", self.review_row(reserve["lease_id"])["launch"]["state"])
        self.assertEqual([reserve["lease_id"]], self._projected_ids())
        self.call("event", LAUNCH_HOST, event="rework_spawn_observed", lease_id=replacement,
                  dispatch_name="resume-first", agent_id="worker-2")
        # Spawn observed, not yet bound: still shown.
        self.assertEqual("spawn_observed", self.review_row(reserve["lease_id"])["launch"]["state"])
        self.assertEqual([reserve["lease_id"]], self._projected_ids())
        self.call("event", {"host_id": "worker-2"}, event="bind", lease_id=replacement)
        # Bound and running, no replacement review: still shown.
        self.assertEqual("bound", self.review_row(reserve["lease_id"])["launch"]["state"])
        self.assertEqual("running", self.store.read()["leases"][replacement]["state"])
        self.assertEqual([reserve["lease_id"]], self._projected_ids())
        with patch.object(m, "ledger", return_value=self.store):
            m.report({"lease_id": replacement, "worker_token": token, "event": "completion",
                      "plan_version": self.store.read()["leases"][replacement]["plan_version"],
                      "artifact": "d" * 64, "evidence": "fixed",
                      "detail": {"summary": "fixed", "coverage": [{"item_id": self.board.item, "verified": True}],
                                 "verification": "fixed"}}, self.board, self.config)
        # Submitted but not yet idle, so no replacement review exists: still shown.
        self.assertIsNone(self.review_row(replacement))
        self.assertEqual([reserve["lease_id"]], self._projected_ids())
        self.idle(replacement)
        # The replacement's own review (pending) replaces the predecessor.
        self.assertEqual("pending", self.review_row(replacement)["state"])
        self.assertEqual([replacement], self._projected_ids())
        self.assertEqual("rework_ready", self.review_row(reserve["lease_id"])["state"])
        # Finalized replacement: nothing remains in the main review queue.
        self.assertEqual("finalized", self.review(replacement, "approve", decision_id="second")["state"])
        self.assertEqual([], self._projected_ids())
        # The stored journal is unchanged in shape: the predecessor row is kept.
        self.assertEqual(("rework_ready", "rework", True, "bound"),
                         (self.review_row(reserve["lease_id"])["state"], self.review_row(reserve["lease_id"])["decision"],
                          all(self.review_row(reserve["lease_id"])["stages"].values()),
                          self.review_row(reserve["lease_id"])["launch"]["state"]))

    def test_twice_reworked_item_projects_only_its_latest_live_generation(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        second = self.review(reserve["lease_id"], "rework", decision_id="g2")["dispatch"]["lease_id"]
        self._replacement_completed(second, "worker-2", "d" * 64, "g2")
        self.idle(second)
        self.assertEqual([second], self._projected_ids())
        third = self.review(second, "rework", decision_id="g3")["dispatch"]["lease_id"]
        # Generation 2 is now the live rework row; generation 1 stays hidden.
        self.assertEqual([second], self._projected_ids())
        self.assertEqual(3, self.review_row(second)["replacement_generation"])
        self._replacement_completed(third, "worker-3", "e" * 64, "g3")
        self.idle(third)
        # A chain of three generations: generation 1 is hidden by generation 2
        # (rework_ready), generation 2 by generation 3 (pending). Only the newest shows.
        self.assertEqual([third], self._projected_ids())
        self.assertEqual(("rework_ready", "rework_ready", "pending"),
                         tuple(self.review_row(lease)["state"] for lease in (reserve["lease_id"], second, third)))
        self.assertEqual("finalized", self.review(third, "approve", decision_id="g3-ok")["state"])
        self.assertEqual([], self._projected_ids())
        # Every stored file is still accepted by the old ledger readers; neither
        # old commit has a finalization, coordination or native-evidence reader.
        raw = json.loads(self.store.path.read_text(encoding="utf-8"))
        for commit in LEGACY_COMMITS:
            module = legacy_module(commit)
            with self.subTest(reader=commit):
                module.validate(json.loads(json.dumps(raw)))
                self.assertEqual(raw["version"], module.WorkParallelStore(self.store.path).read()["version"])
                for name in ("work_parallel_finalization.py", "work_parallel_receipts.py", "work_parallel_native.py"):
                    absent = subprocess.run(["git", "-C", str(SOURCE_ROOT), "cat-file", "-e",
                                             commit + ":ciel_runtime_support/" + name], capture_output=True)
                    self.assertNotEqual(0, absent.returncode, name)

    def test_replacement_review_in_deciding_hides_the_predecessor(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        replacement = self.review(reserve["lease_id"], "rework", decision_id="pre")["dispatch"]["lease_id"]
        self._replacement_completed(replacement, "worker-2", "d" * 64, "pre")
        self.idle(replacement)
        original = m._board_accept

        def failing(*args, **kwargs):
            raise WorkParallelError("board temporarily unavailable")
        with patch.object(m, "_board_accept", failing):
            first = self.review(replacement, "approve", decision_id="approve-later")
        self.assertEqual("deciding", first["state"])
        self.assertIs(original, m._board_accept)
        projected = self.call("status", {})["review"]["pending"]
        self.assertEqual([(replacement, "deciding", "stage_failed")],
                         [(row["lease_id"], row["state"], row["blocked"]["code"]) for row in projected])
        done = self.call("event", {"internal": True}, event="review_roll_forward", lease_id=replacement)
        self.assertEqual("finalized", done["state"])
        self.assertEqual([], self._projected_ids())

    def test_incomplete_or_blocked_rework_ready_row_is_never_hidden(self):
        # The validator already refuses a stored rework_ready row with an
        # incomplete stage or a blocked reason; the projection rule keeps such a
        # row visible anyway. Injected through read(), bypassing validation.
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        replacement = self.review(reserve["lease_id"], "rework", decision_id="inj")["dispatch"]["lease_id"]
        self._replacement_completed(replacement, "worker-2", "d" * 64, "inj")
        self.idle(replacement)
        reviews = WorkParallelFinalizationStore(self.tmp / "work-parallel-finalization.json")
        stored = reviews.read()
        self.assertEqual([replacement], [row["lease_id"] for row in reviews.projection(SCOPE)["pending"]])
        cases = {"incomplete stage": lambda row: row["stages"].update(archive=False),
                 "blocked": lambda row: row.update(blocked={"code": "stage_failed", "message": "x", "at": 1.0})}
        for label, change in cases.items():
            with self.subTest(label):
                state = json.loads(json.dumps(stored))
                change(state["reviews"][reserve["lease_id"]])
                with self.assertRaises(WorkParallelError):
                    from ciel_runtime_support.work_parallel_finalization import validate
                    validate(json.loads(json.dumps(state)))
                with patch.object(WorkParallelFinalizationStore, "read", lambda store, state=state: json.loads(json.dumps(state))):
                    shown = [row["lease_id"] for row in reviews.projection(SCOPE)["pending"]]
                self.assertEqual({reserve["lease_id"], replacement}, set(shown))

    def _web(self):
        from ciel_runtime_support.http_response import HttpResponseAdapter
        from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
        return WorkHttpController(WorkHttpServices(
            self.tmp / "state", lambda: self.config, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None}, HttpResponseAdapter.write_json,
            "fixture", parallel_status=lambda identity, board: m.project(self.store, identity, board),
            parallel_store=lambda: self.store))

    def test_hidden_predecessor_leaves_no_launch_indicator_in_the_web_view(self):
        # Injected state: the predecessor's launch stays unresolved although the
        # replacement binds, completes and is reviewed (launch_bound is skipped).
        # Whether a real bind can reach this is not established; a crash inside
        # the bind event also skips the workspace bind, so idle would fail there.
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        replacement = self.review(reserve["lease_id"], "rework", decision_id="web")["dispatch"]["lease_id"]
        token = self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)["worker_token"]
        self.call("event", LAUNCH_HOST, event="rework_launch_intent", lease_id=replacement,
                  dispatch_name="resume-web", generation=2)
        self.call("event", LAUNCH_HOST, event="rework_spawn_observed", lease_id=replacement,
                  dispatch_name="resume-web", agent_id="worker-2")
        with patch.object(m, "_bind_rework_launch", lambda *args, **kwargs: None):
            self.call("event", {"host_id": "worker-2"}, event="bind", lease_id=replacement)
        self.assertEqual("worker-2", self.store.read()["leases"][replacement]["host_id"])
        self.assertEqual("spawn_observed", self.review_row(reserve["lease_id"])["launch"]["state"])
        web = self._web()
        board = self.board.read()
        before = web._public_parallel(SCOPE, board)["parallel_reviews"]
        self.assertEqual([(reserve["lease_id"], "spawn_observed")],
                         [(row["lease_id"], row["launch"]["state"]) for row in before])
        with patch.object(m, "ledger", return_value=self.store):
            m.report({"lease_id": replacement, "worker_token": token, "event": "completion",
                      "plan_version": self.store.read()["leases"][replacement]["plan_version"],
                      "artifact": "d" * 64, "evidence": "fixed",
                      "detail": {"summary": "fixed web", "coverage": [{"item_id": self.board.item, "verified": True}],
                                 "verification": "fixed web"}}, self.board, self.config)
        self.idle(replacement)
        # The launch is still unresolved in the journal and in the host's raw
        # launch list, but no shown row carries it and the tab is not "launching".
        self.assertEqual("spawn_observed", self.review_row(reserve["lease_id"])["launch"]["state"])
        self.assertIn(reserve["lease_id"], web._public_launches(SCOPE))
        board = self.board.read()
        rows = web._public_parallel(SCOPE, board)["parallel_reviews"]
        self.assertEqual([(replacement, "pending")], [(row["lease_id"], row["state"]) for row in rows])
        self.assertNotIn("launch", rows[0])
        fingerprint, live = web._parallel_fingerprint(SCOPE, board)
        self.assertFalse(live)
        # The predecessor lease still appears among ended forks; its review and
        # launch do not appear among the review rows the tab watches.
        reviews = json.loads(fingerprint)[3]
        self.assertEqual([[replacement, "pending", None, [None, None, None, None, None]]], reviews)

    def test_rework_waiting_for_capacity_or_blocked_stays_visible_to_the_pump(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        self._block_p1()
        waiting = self.review(reserve["lease_id"], "rework", decision_id="slot")
        self.assertEqual("awaiting_capacity", waiting["state"])
        projected = self.call("status", {})["review"]["pending"]
        self.assertEqual([(reserve["lease_id"], "awaiting_capacity", "capacity")],
                         [(row["lease_id"], row["state"], row["blocked"]["code"]) for row in projected])
        self._unblock_p1()
        # A stage failure after the decision: deciding + blocked stays projected.
        original = m.coordination(self.store).__class__.record_assignment
        def failing(store, *args, **kwargs):
            raise WorkParallelError("resume assignment unavailable")
        with patch.object(m.coordination(self.store).__class__, "record_assignment", failing):
            blocked = self.call("event", {"internal": True}, event="review_roll_forward", lease_id=reserve["lease_id"])
        self.assertEqual("deciding", blocked["state"])
        row = self.review_row(reserve["lease_id"])
        self.assertEqual(("deciding", "stage_failed", True, False),
                         (row["state"], row["blocked"]["code"], row["stages"]["workspace"], row["stages"]["board"]))
        projected = self.call("status", {})["review"]["pending"]
        self.assertEqual([(reserve["lease_id"], "deciding", "stage_failed")],
                         [(r["lease_id"], r["state"], r["blocked"]["code"]) for r in projected])
        self.assertIs(original, m.coordination(self.store).__class__.record_assignment)
        ready = self.call("event", {"internal": True}, event="review_roll_forward", lease_id=reserve["lease_id"])
        self.assertEqual("rework_ready", ready["state"])
        self.assertEqual([reserve["lease_id"]], self._projected_ids())

    def _predecessor_and_other_review(self, decision_id):
        """A rework predecessor (no replacement review yet) and another item's pending review."""
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        replacement = self.review(reserve["lease_id"], "rework", decision_id=decision_id)["dispatch"]["lease_id"]
        other, _, _ = self.completed(self.board.item2, worker_id="worker-9")
        self.idle(other["lease_id"])
        self.assertEqual({reserve["lease_id"], other["lease_id"]}, set(self._projected_ids()))
        path = self.tmp / "work-parallel-finalization.json"
        base = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn(replacement, base["reviews"])
        self.assertEqual((2, self.board.item), (base["reviews"][reserve["lease_id"]]["replacement_generation"],
                                                base["reviews"][reserve["lease_id"]]["item_id"]))
        return reserve, replacement, other, path, base

    def _write_forged_replacement(self, path, base, other, replacement, **fields):
        """Store a valid review row under the replacement lease id, copied from another review."""
        doc = json.loads(json.dumps(base))
        forged = json.loads(json.dumps(doc["reviews"][other["lease_id"]]))
        forged.update(lease_id=replacement, **fields)
        doc["reviews"][replacement] = forged
        path.write_text(json.dumps(doc), encoding="utf-8")

    def test_unrelated_review_row_never_hides_a_rework_predecessor(self):
        reserve, replacement, other, path, base = self._predecessor_and_other_review("keep")
        # A row keyed by the replacement lease that differs in generation or scope
        # is not that replacement's review, so the predecessor stays projected.
        mismatches = {"generation": {"item_id": self.board.item, "generation": 1},
                      "scope": {"item_id": self.board.item, "generation": 2, "scope": "claude:other-session"}}
        for label, fields in mismatches.items():
            with self.subTest(label):
                self._write_forged_replacement(path, base, other, replacement, **fields)
                self.assertIn(reserve["lease_id"], self._projected_ids())
        # Control: the exact replacement identity does hide it.
        self._write_forged_replacement(path, base, other, replacement, item_id=self.board.item, generation=2)
        self.assertNotIn(reserve["lease_id"], self._projected_ids())

    def test_replacement_row_for_another_item_never_hides_the_predecessor(self):
        reserve, replacement, other, path, base = self._predecessor_and_other_review("item")
        # Same replacement lease id, scope and generation, but another item: not
        # this predecessor's replacement review, so the predecessor stays projected.
        self._write_forged_replacement(path, base, other, replacement, item_id=self.board.item2, generation=2)
        stored = WorkParallelFinalizationStore(path).read()["reviews"][replacement]
        self.assertEqual((replacement, SCOPE, 2, self.board.item2),
                         (stored["lease_id"], stored["scope"], stored["generation"], stored["item_id"]))
        self.assertIn(reserve["lease_id"], self._projected_ids())
        # Control: the same row for this item hides it.
        self._write_forged_replacement(path, base, other, replacement, item_id=self.board.item, generation=2)
        self.assertNotIn(reserve["lease_id"], self._projected_ids())

    def test_decision_replay_binds_body_and_uses_journaled_evidence(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        crash, fired = self._crash_once("board")
        with crash, self.assertRaises(Crash):
            self.review(reserve["lease_id"], "approve", decision_id="same", evidence="ORIGINAL")
        with self.assertRaisesRegex(WorkParallelError, "decided differently"):
            self.call("item_review", {}, lease_id=reserve["lease_id"], decision="approve",
                      decision_id="same", evidence="CHANGED", expected_finalization_version=0)
        result = self.call("item_review", {}, lease_id=reserve["lease_id"], decision="approve",
                           decision_id="same", evidence="ORIGINAL", expected_finalization_version=0)
        self.assertEqual("finalized", result["state"])
        self.assertIn("ORIGINAL", self.node()["evidence"])
        self.assertNotIn("CHANGED", json.dumps(self.store.read()))

    def test_resume_reservation_blocks_a_second_worker_for_the_same_item(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        # The predecessor submitted result already fences a duplicate worker.
        with self.assertRaisesRegex(WorkParallelError, "no eligible candidate"):
            self.call("event", {"fork": True}, event="reserve", item_id=self.board.item, priority="P1")
        result = self.review(reserve["lease_id"], "rework", decision_id="family")
        for role in ("worker", "resume"):
            with self.assertRaisesRegex(WorkParallelError, "no eligible candidate"):
                self.call("event", {"fork": True}, event="reserve", item_id=self.board.item,
                          priority="P1", role=role)
        rows = [row for row in self.store.read()["leases"].values()
                if row["item_id"] == self.board.item and row["state"] in ("reserved", "running")]
        self.assertEqual([result["dispatch"]["lease_id"]], [row["id"] for row in rows])

    def test_review_wake_is_durable_fenced_and_restart_safe(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        first = self.call("event", {"internal": True}, event="review_wake", operation="prepare", lease_id=reserve["lease_id"])
        self.assertEqual("submitting", first["state"])
        with self.assertRaisesRegex(WorkParallelError, "stale review wake"):
            self.call("event", {"internal": True}, event="review_wake", operation="record", lease_id=reserve["lease_id"],
                      attempt_id="stale", state="queued")
        self.call("event", {"internal": True}, event="review_wake", operation="record", lease_id=reserve["lease_id"],
                  attempt_id=first["attempt_id"], state="queued")
        # A fresh store/process sees queued and cannot resubmit.
        with self.assertRaisesRegex(WorkParallelError, "already submitted"):
            self.call("event", {"internal": True}, event="review_wake", operation="prepare", lease_id=reserve["lease_id"])
        projected = self.call("status", {})["review"]["pending"][0]
        self.assertEqual({"state": "queued", "attempts": 1}, projected["wake"])
        self.assertNotIn(first["attempt_id"], json.dumps(projected))
        self.review(reserve["lease_id"], "approve", decision_id="wake")
        self.assertEqual("acknowledged", self.review_row(reserve["lease_id"])["wake"]["state"])

    def test_failed_review_wake_retries_and_worker_cannot_wake(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        with self.assertRaisesRegex(WorkParallelError, "mod-internal review host"):
            self.call("event", {"agent_id": "worker-1"}, event="review_wake", operation="prepare",
                      lease_id=reserve["lease_id"])
        first = self.call("event", {"internal": True}, event="review_wake", operation="prepare", lease_id=reserve["lease_id"])
        self.call("event", {"internal": True}, event="review_wake", operation="record", lease_id=reserve["lease_id"],
                  attempt_id=first["attempt_id"], state="failed")
        second = self.call("event", {"internal": True}, event="review_wake", operation="prepare", lease_id=reserve["lease_id"])
        self.assertNotEqual(first["attempt_id"], second["attempt_id"])
        self.assertEqual(2, second["attempts"])

    def test_rework_launch_intent_blocks_rotation_until_positive_denial(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        replacement = self.review(reserve["lease_id"], "rework", decision_id="launch")["dispatch"]["lease_id"]
        first = self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        intent = self.call("event", LAUNCH_HOST, event="rework_launch_intent", lease_id=replacement,
                           dispatch_name="resume-a", generation=2)
        self.assertEqual("launch_intent", intent["state"])
        self.assertEqual(intent, {**self.call("event", LAUNCH_HOST, event="rework_launch_intent", lease_id=replacement,
                                              dispatch_name="resume-a", generation=2),
                                  "finalization_version": intent["finalization_version"]})
        with self.assertRaisesRegex(WorkParallelError, "unresolved"):
            self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        with self.assertRaisesRegex(WorkParallelError, "unresolved"):
            self.call("event", LAUNCH_HOST, event="rework_launch_intent", lease_id=replacement,
                      dispatch_name="resume-b", generation=2)
        self.call("event", {**LAUNCH_HOST, "proof": "native spawn denied"}, event="rework_launch_denied",
                  lease_id=replacement, dispatch_name="resume-a")
        second = self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        self.assertNotEqual(first["worker_token"], second["worker_token"])
        self.call("event", LAUNCH_HOST, event="rework_launch_intent", lease_id=replacement,
                  dispatch_name="resume-b", generation=2)
        self.call("event", LAUNCH_HOST, event="rework_spawn_observed", lease_id=replacement,
                  dispatch_name="resume-b", agent_id="agent-b")
        with self.assertRaisesRegex(WorkParallelError, "unresolved"):
            self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        self.call("event", {"host_id": "agent-b"}, event="bind", lease_id=replacement)
        self.call("event", {"host_id": "agent-b"}, event="bind", lease_id=replacement)
        self.assertEqual("bound", self.review_row(reserve["lease_id"])["launch"]["state"])
        with self.assertRaisesRegex(WorkParallelError, "unknown rework dispatch"):
            self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)

    def test_schema_one_journal_migrates_on_next_write(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        path = self.tmp / "work-parallel-finalization.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["schema"] = 1
        for row in doc["reviews"].values():
            for key in ("decision_digest", "wake", "launch"):
                row.pop(key)
        path.write_text(json.dumps(doc), encoding="utf-8")
        migrated = WorkParallelFinalizationStore(path).read()
        self.assertEqual(5, migrated["schema"])
        self.assertEqual("pending", migrated["reviews"][reserve["lease_id"]]["wake"]["state"])
        self.call("event", {"internal": True}, event="review_wake", operation="prepare", lease_id=reserve["lease_id"])
        self.assertEqual(5, json.loads(path.read_text(encoding="utf-8"))["schema"])

    def test_lifecycle_invariant_matrix_fails_closed(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        path = self.tmp / "work-parallel-finalization.json"
        valid = json.loads(path.read_text(encoding="utf-8"))
        row = valid["reviews"][reserve["lease_id"]]
        def all_stages(value):
            return {key: value for key in STAGES}
        cases = {
            "pending decision": {"decision": "approve"},
            "finalized empty": {"decision": "approve", "decision_id": "d", "decision_digest": "f" * 64,
                                "evidence": "e", "state": "finalized", "stages": all_stages(False),
                                "wake": {**row["wake"], "state": "acknowledged"}},
            "board before workspace": {"decision": "approve", "decision_id": "d", "decision_digest": "f" * 64,
                                       "evidence": "e", "state": "deciding",
                                       "stages": {**all_stages(False), "accepted": True, "board": True},
                                       "wake": {**row["wake"], "state": "acknowledged"}},
            "rework finalized": {"decision": "rework", "decision_id": "d", "decision_digest": "f" * 64,
                                 "evidence": "e", "state": "finalized", "stages": all_stages(True),
                                 "replacement_lease_id": "r", "replacement_generation": 2,
                                 "wake": {**row["wake"], "state": "acknowledged"}},
            "rework ready incomplete": {"decision": "rework", "decision_id": "d", "decision_digest": "f" * 64,
                                        "evidence": "e", "state": "rework_ready", "stages": all_stages(False),
                                        "replacement_lease_id": "r", "replacement_generation": 2,
                                        "wake": {**row["wake"], "state": "acknowledged"}},
            "approve replacement": {"decision": "approve", "decision_id": "d", "decision_digest": "f" * 64,
                                    "evidence": "e", "state": "deciding", "replacement_lease_id": "r",
                                    "replacement_generation": 2, "wake": {**row["wake"], "state": "acknowledged"}},
            "pending acknowledged": {"wake": {**row["wake"], "state": "acknowledged"}},
            "decided wake pending": {"decision": "approve", "decision_id": "d", "decision_digest": "f" * 64,
                                     "evidence": "e", "state": "deciding"},
            "awaiting capacity without reason": {
                "decision": "rework", "decision_id": "d", "decision_digest": "f" * 64, "evidence": "e",
                "state": "awaiting_capacity", "replacement_lease_id": "r", "replacement_generation": 2,
                "wake": {**row["wake"], "state": "acknowledged"}},
            "awaiting capacity after reservation": {
                "decision": "rework", "decision_id": "d", "decision_digest": "f" * 64, "evidence": "e",
                "state": "awaiting_capacity", "replacement_lease_id": "r", "replacement_generation": 2,
                "stages": {**all_stages(False), "accepted": True},
                "blocked": {"code": "capacity", "message": "full", "at": 1},
                "wake": {**row["wake"], "state": "acknowledged"}},
            "approve awaiting capacity": {
                "decision": "approve", "decision_id": "d", "decision_digest": "f" * 64, "evidence": "e",
                "state": "awaiting_capacity", "blocked": {"code": "capacity", "message": "full", "at": 1},
                "wake": {**row["wake"], "state": "acknowledged"}},
            "pending blocked": {"blocked": {"code": "stage_failed", "message": "x", "at": 1}},
            "launch audit on approve": {
                "decision": "approve", "decision_id": "d", "decision_digest": "f" * 64, "evidence": "e",
                "state": "deciding", "wake": {**row["wake"], "state": "acknowledged"},
                "launch": {**row["launch"], "audit": [{"kind": "absent", "dispatch_name": "n", "attempt": 1,
                                                       "observer_nonce": None, "at": 1, "detail": "d"}]}},
            "pending launch nonce": {"launch": {**row["launch"], "host_nonce": "process-1"}},
            "launch on approve": {
                "decision": "approve", "decision_id": "d", "decision_digest": "f" * 64, "evidence": "e",
                "state": "deciding", "wake": {**row["wake"], "state": "acknowledged"},
                "launch": {**row["launch"], "state": "launch_intent", "dispatch_name": "n", "attempt": 1}},
        }
        for label, changes in cases.items():
            with self.subTest(label):
                broken = json.loads(json.dumps(valid))
                broken["reviews"][reserve["lease_id"]].update(changes)
                path.write_text(json.dumps(broken), encoding="utf-8")
                with self.assertRaisesRegex(WorkParallelError, "corrupt"):
                    WorkParallelFinalizationStore(path).read()
        path.write_text(json.dumps(valid), encoding="utf-8")

    def _block_p1(self):
        state = self.store.read()
        state["policy"]["priority_limits"]["P1"] = 1
        template = next(iter(state["leases"].values()))
        state["leases"]["blocker"] = {**template, "id": "blocker", "item_id": "other", "state": "running",
            "slot": True, "usage": .05, "artifact": None, "host_id": "other-host", "role": "worker",
            "idle_proof": None, "cleanup_proof": None, "report_hash": "b" * 64}
        for key in ("workspace", "terminal"):
            state["leases"]["blocker"].pop(key, None)
        self.store.path.write_text(json.dumps(state), encoding="utf-8")

    def _unblock_p1(self):
        state = self.store.read()
        del state["leases"]["blocker"]
        self.store.path.write_text(json.dumps(state), encoding="utf-8")

    def test_max_rework_is_refused_before_decision_and_keeps_review_pending(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        state = self.store.read()
        state["leases"][reserve["lease_id"]]["rework"] = state["policy"]["max_rework"]
        self.store.path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(WorkParallelError, "rework limit reached"):
            self.review(reserve["lease_id"], "rework", decision_id="limit")
        self.assertIsNone(self.review_row(reserve["lease_id"])["decision"])
        self.assertEqual([reserve["lease_id"]], [k for k, r in self.store.read()["leases"].items()
                                                  if r["item_id"] == self.board.item])
        # Approval is still possible for the same pending result.
        self.assertEqual("finalized", self.review(reserve["lease_id"], "approve", decision_id="ok")["state"])

    def test_capacity_after_decision_is_explicit_awaiting_capacity_and_host_pump_retries(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        self._block_p1()
        result = self.review(reserve["lease_id"], "rework", decision_id="capacity", evidence="redo it")
        self.assertEqual("awaiting_capacity", result["state"])
        self.assertIn("capacity reached for P1 (1/1)", result["reason"])
        self.assertNotIn("dispatch", result)
        row = self.review_row(reserve["lease_id"])
        self.assertEqual(("rework", "awaiting_capacity", "capacity"),
                         (row["decision"], row["state"], row["blocked"]["code"]))
        projected = self.call("status", {})["review"]["pending"][0]
        self.assertEqual("awaiting_capacity", projected["state"])
        self.assertEqual("capacity", projected["blocked"]["code"])
        # The submitted predecessor keeps fencing the item: no worker/resume duplicate.
        self.assertEqual("submitted", self.store.read()["leases"][reserve["lease_id"]]["state"])
        for role in ("worker", "resume"):
            with self.assertRaises(WorkParallelError):
                self.call("event", {"fork": True}, event="reserve", item_id=self.board.item,
                          priority="P1", role=role)
        # The decision is durable: a different verdict cannot replace it.
        with self.assertRaisesRegex(WorkParallelError, "decided differently"):
            self.review(reserve["lease_id"], "approve", decision_id="other")
        # Host pump retry while still full stays explicit, never a silent deciding.
        again = self.call("event", {"internal": True}, event="review_roll_forward", lease_id=reserve["lease_id"])
        self.assertEqual("awaiting_capacity", again["state"])
        # Workers cannot drive the roll-forward.
        with self.assertRaisesRegex(WorkParallelError, "mod-internal review host"):
            self.call("event", {"agent_id": "worker-1"}, event="review_roll_forward",
                      lease_id=reserve["lease_id"])
        self._unblock_p1()
        ready = self.call("event", {"internal": True}, event="review_roll_forward", lease_id=reserve["lease_id"])
        self.assertEqual("rework_ready", ready["state"])
        row = self.review_row(reserve["lease_id"])
        self.assertIsNone(row["blocked"])
        self.assertEqual("redo it", row["evidence"])
        self.assertEqual("done", self.store.read()["leases"][reserve["lease_id"]]["state"])
        resumes = [k for k, r in self.store.read()["leases"].items()
                   if r["item_id"] == self.board.item and r["role"] == "resume"]
        self.assertEqual([ready["dispatch"]["lease_id"]], resumes)

    def test_stage_failure_after_decision_is_recorded_as_blocked(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        original = m._board_accept
        calls = []

        def failing(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise WorkParallelError("board temporarily unavailable")
            return original(*args, **kwargs)
        with patch.object(m, "_board_accept", failing):
            first = self.review(reserve["lease_id"], "approve", decision_id="blocked")
            self.assertEqual("deciding", first["state"])
            self.assertEqual("host_pump", first["retry"])
            row = self.review_row(reserve["lease_id"])
            self.assertEqual(("stage_failed", "board temporarily unavailable"),
                             (row["blocked"]["code"], row["blocked"]["message"]))
            done = self.call("event", {"internal": True}, event="review_roll_forward", lease_id=reserve["lease_id"])
        self.assertEqual("finalized", done["state"])
        self.assertIsNone(self.review_row(reserve["lease_id"])["blocked"])

    # Crash replay ------------------------------------------------------------

    def _crash_once(self, stage):
        original = WorkParallelFinalizationStore.mark
        fired = []

        def mark(self_, lease_id, stage_name, **kwargs):
            if stage_name == stage and not fired:
                fired.append(stage)
                raise Crash("crash after " + stage + " effect, before journal mark")
            return original(self_, lease_id, stage_name, **kwargs)
        return patch.object(WorkParallelFinalizationStore, "mark", mark), fired

    def test_every_approve_stage_replays_idempotently_after_crash(self):
        for stage in STAGES:
            with self.subTest(stage):
                self.setUp()
                reserve, workspace, artifact = self.completed()
                self.idle(reserve["lease_id"])
                crash, fired = self._crash_once(stage)
                with crash, self.assertRaises(Crash):
                    self.review(reserve["lease_id"], "approve", decision_id="crash-" + stage)
                self.assertEqual([stage], fired)
                result = self.call("item_review", {}, lease_id=reserve["lease_id"], decision="approve",
                                   decision_id="crash-" + stage, evidence="main verified",
                                   expected_finalization_version=0)
                self.assertEqual("finalized", result["state"])
                self.assertEqual("done", self.store.read()["leases"][reserve["lease_id"]]["state"])
                self.assertEqual("done", self.node()["status"])
                archived = [r for r in m.coordination(self.store).read()["archive"]
                            if r["run"]["lease_id"] == reserve["lease_id"]]
                self.assertEqual(1, len(archived))

    def test_every_rework_stage_replays_without_a_duplicate_generation(self):
        for stage in STAGES:
            with self.subTest(stage):
                self.setUp()
                reserve, workspace, artifact = self.completed()
                self.idle(reserve["lease_id"])
                crash, fired = self._crash_once(stage)
                with crash, self.assertRaises(Crash):
                    self.review(reserve["lease_id"], "rework", decision_id="crash-" + stage)
                result = self.call("item_review", {}, lease_id=reserve["lease_id"], decision="rework",
                                   decision_id="crash-" + stage, evidence="main verified",
                                   expected_finalization_version=0)
                self.assertEqual("rework_ready", result["state"])
                resumes = [k for k, r in self.store.read()["leases"].items()
                           if r["item_id"] == self.board.item and r["role"] == "resume"]
                self.assertEqual([result["dispatch"]["lease_id"]], resumes)
                coord = m.coordination(self.store).read()
                self.assertEqual(1, sum(1 for r in coord["runs"].values() if r["role"] == "resume"))
                self.assertEqual(1, sum(1 for r in coord["archive"] if r["run"]["lease_id"] == reserve["lease_id"]))

    def test_rework_recovers_lost_workspace_attach(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        original = WorkParallelStore.attach_workspace
        calls = []

        def attach(self_, *args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise Crash("lost after rebind")
            return original(self_, *args, **kwargs)
        with patch.object(WorkParallelStore, "attach_workspace", attach), self.assertRaises(Crash):
            self.review(reserve["lease_id"], "rework", decision_id="attach")
        with patch.object(WorkParallelStore, "attach_workspace", attach):
            result = self.call("item_review", {}, lease_id=reserve["lease_id"], decision="rework",
                               decision_id="attach", evidence="main verified", expected_finalization_version=0)
        self.assertEqual(str(workspace), self.store.read()["leases"][result["dispatch"]["lease_id"]]["workspace"]["path"])

    def test_launch_name_is_durable_on_lease_and_bind_is_fenced(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        replacement = self.review(reserve["lease_id"], "rework", decision_id="named")["dispatch"]["lease_id"]
        self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        # A bind without an observed or exactly named launch is refused before commit.
        with self.assertRaisesRegex(WorkParallelError, "not observed"):
            self.call("event", {"host_id": "stray"}, event="bind", lease_id=replacement)
        self.assertIsNone(self.store.read()["leases"][replacement]["host_id"])
        self.call("event", LAUNCH_HOST, event="rework_launch_intent", lease_id=replacement,
                  dispatch_name="resume-durable", generation=2)
        lease = self.store.read()["leases"][replacement]
        self.assertEqual("resume-durable", lease["dispatch_name"])
        self.assertNotIn("dispatch_name", self.model_status()["leases"][replacement])
        self.assertEqual("resume-durable", self.call("status", {})["leases"][replacement]["dispatch_name"])
        host_view = self.call("event", LAUNCH_HOST, event="rework_launch_status")["launches"]
        self.assertEqual(["resume-durable"], [row["dispatch_name"] for row in host_view])
        # A new process reconciles by exact name: bind with that agent name is the observation.
        with self.assertRaisesRegex(WorkParallelError, "not observed"):
            self.call("event", {"host_id": "agent-x", "agent_name": "other-name"}, event="bind",
                      lease_id=replacement)
        self.call("event", {"host_id": "agent-x", "agent_name": "resume-durable"}, event="bind",
                  lease_id=replacement)
        launch = self.review_row(reserve["lease_id"])["launch"]
        self.assertEqual(("bound", "agent-x"), (launch["state"], launch["agent_id"]))
        # Lost bind response: identical replay is idempotent.
        self.call("event", {"host_id": "agent-x", "agent_name": "resume-durable"}, event="bind",
                  lease_id=replacement)
        projected = json.dumps(self.call("status", {})["review"])
        self.assertNotIn("agent-x", projected)

    def test_lost_dispatch_response_rotates_only_before_launch_and_revokes_prior_token(self):
        from ciel_runtime_support.work_parallel import worker_token_matches
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        replacement = self.review(reserve["lease_id"], "rework", decision_id="rotate")["dispatch"]["lease_id"]
        # Response lost before any launch intent: a replay mints a fresh token and
        # the lost one can never authenticate.
        lost = self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        fresh = self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        lease = self.store.read()["leases"][replacement]
        self.assertFalse(worker_token_matches(lease, lost["worker_token"]))
        self.assertTrue(worker_token_matches(lease, fresh["worker_token"]))
        before = lease["report_hash"]
        self.call("event", LAUNCH_HOST, event="rework_launch_intent", lease_id=replacement,
                  dispatch_name="resume-rotate", generation=2)
        # Unknown spawn outcome: no rotation and the issued hash is unchanged.
        with self.assertRaisesRegex(WorkParallelError, "unresolved"):
            self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        self.assertEqual(before, self.store.read()["leases"][replacement]["report_hash"])

    # Launch absence ------------------------------------------------------------

    def _rework_with_intent(self, decision_id="absent", name="resume-a", nonce="process-1", item_id=None):
        reserve, workspace, artifact = self.completed(item_id)
        self.idle(reserve["lease_id"])
        replacement = self.review(reserve["lease_id"], "rework", decision_id=decision_id)["dispatch"]["lease_id"]
        token = self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)["worker_token"]
        self.call("event", {"internal": True, "launch_nonce": nonce}, event="rework_launch_intent",
                  lease_id=replacement, dispatch_name=name, generation=2)
        return reserve, replacement, token

    def _absent(self, replacement, name="resume-a", nonce="process-2", asserted=None, **extra):
        host = {"internal": True, "launch_nonce": nonce,
                "absent_dispatch_name": name if asserted is None else asserted, **extra}
        return self.call("event", host, event="rework_launch_absent", lease_id=replacement, dispatch_name=name)

    LAUNCH_EVENTS = (("rework_launch_denied", {"dispatch_name": "resume-a", "proof": "forged"}),
                     ("rework_launch_absent", {"dispatch_name": "resume-a"}),
                     ("rework_launch_manual_resolve", {"dispatch_name": "resume-a"}),
                     ("rework_launch_present", {"dispatch_name": "resume-a"}),
                     ("rework_spawn_observed", {"dispatch_name": "resume-a", "agent_id": "x"}),
                     ("rework_launch_intent", {"dispatch_name": "resume-z", "generation": 2}))

    def _assert_launch_untouched(self, reserve, replacement, before):
        self.assertEqual(before, self._snapshot_bytes()
                         | {"work-parallel-finalization.json":
                            (self.tmp / "work-parallel-finalization.json").read_bytes()})
        launch = self.review_row(reserve["lease_id"])["launch"]
        self.assertEqual(("launch_intent", "resume-a", 1),
                         (launch["state"], launch["dispatch_name"], launch["attempt"]))
        with self.assertRaisesRegex(WorkParallelError, "unresolved"):
            self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)

    def _all_files(self):
        return self._snapshot_bytes() | {"work-parallel-finalization.json":
                                         (self.tmp / "work-parallel-finalization.json").read_bytes()}

    def test_main_forwarded_launch_events_with_forged_host_fields_are_refused(self):
        reserve, replacement, token = self._rework_with_intent()
        # The mod forwards a main-model work_parallel_event with _host.agent_id=null,
        # and it replaces _host wholesale. Even if forged fields arrived, a call
        # without the mod-internal marker is refused and writes nothing.
        forged = {"agent_id": None, "fork": True, "launch_nonce": "process-9", "proof": "forged",
                  "absent_dispatch_name": "resume-a", "present_dispatch_name": "resume-a",
                  "user_confirmed": True}
        for event, args in self.LAUNCH_EVENTS:
            with self.subTest(event):
                before = self._all_files()
                with self.assertRaisesRegex(WorkParallelError, "mod-internal launch host required"):
                    self.call("event", forged, event=event, lease_id=replacement, **args)
                self.assertEqual(before, self._all_files())
        self._assert_launch_untouched(reserve, replacement, self._all_files())

    def test_fork_originated_launch_events_are_refused_even_with_internal_marker(self):
        reserve, replacement, token = self._rework_with_intent()
        for event, args in self.LAUNCH_EVENTS:
            with self.subTest(event):
                before = self._all_files()
                with self.assertRaisesRegex(WorkParallelError, "mod-internal launch host required"):
                    self.call("event", {"agent_id": "fork-1", "internal": True, "launch_nonce": "x",
                                        "absent_dispatch_name": "resume-a"},
                              event=event, lease_id=replacement, **args)
                self.assertEqual(before, self._all_files())
        self._assert_launch_untouched(reserve, replacement, self._all_files())

    def test_model_supplied_host_argument_is_replaced_by_the_router_boundary(self):
        # A model tool call can carry an extra "_host" key: the MCP input schema
        # does not forbid additional properties. host_context then requires the
        # out-of-band host token, so a forged _host without it is refused.
        reserve, replacement, token = self._rework_with_intent()
        schema = next(row for row in m.schemas() if row["name"] == "work_parallel_event")["inputSchema"]
        self.assertNotIn("_host", schema["properties"])
        before = self._all_files()
        with patch.object(m, "ledger", return_value=self.store):
            with self.assertRaisesRegex(WorkParallelError, "authenticated parallel host required"):
                m.run("work_parallel_event", {"event": "rework_launch_denied", "lease_id": replacement,
                      "dispatch_name": "resume-a", "_host": {"internal": True, "proof": "forged",
                                                              "token": "model-guess-" * 4}},
                      self.board, SCOPE, self.config, None)
        self.assertEqual(before, self._all_files())

    def test_launch_absent_refusal_paths(self):
        reserve, replacement, token = self._rework_with_intent()
        lease_before = self.store.read()["leases"][replacement]["report_hash"]
        cases = (
            ("same process nonce", dict(nonce="process-1"), "belongs to this host process"),
            ("stale name", dict(name="resume-old"), "stale rework launch name"),
            ("no absence assertion", dict(asserted=""), "assert this exact launch name absent"),
            ("asserted other name", dict(asserted="resume-b"), "assert this exact launch name absent"),
            ("worker actor", dict(agent_id="worker-1"), "mod-internal launch host required"),
        )
        for label, kwargs, pattern in cases:
            with self.subTest(label):
                with self.assertRaisesRegex(WorkParallelError, pattern):
                    self._absent(replacement, **kwargs)
                self.assertEqual("launch_intent", self.review_row(reserve["lease_id"])["launch"]["state"])
        with self.assertRaisesRegex(WorkParallelError, "mod-internal launch host required"):
            self.call("event", {"launch_nonce": "process-2", "absent_dispatch_name": "resume-a"},
                      event="rework_launch_absent", lease_id=replacement, dispatch_name="resume-a")
        with self.assertRaisesRegex(WorkParallelError, "unknown rework replacement"):
            self._absent("not-a-lease")
        self.assertEqual(lease_before, self.store.read()["leases"][replacement]["report_hash"])
        # After a bind the launch is bound: absence can no longer resolve it.
        self.call("event", LAUNCH_HOST, event="rework_spawn_observed", lease_id=replacement,
                  dispatch_name="resume-a", agent_id="agent-a")
        self.call("event", {"host_id": "agent-a", "agent_name": "resume-a"}, event="bind", lease_id=replacement)
        with self.assertRaisesRegex(WorkParallelError, "not unresolved or observed-unbound"):
            self._absent(replacement, absent_agent_id="agent-a")

    def test_absent_after_restart_rotates_once_and_binds_once(self):
        from ciel_runtime_support.work_parallel import worker_token_matches
        reserve, replacement, old_token = self._rework_with_intent()
        resolved = self._absent(replacement)
        self.assertEqual("spawn_denied", resolved["state"])
        self.assertEqual("absent", resolved["audit"][-1]["kind"])
        self.assertNotIn("launch_nonce", json.dumps(resolved))
        self.assertNotIn("process-2", json.dumps(self.call("status", {})["review"]))
        # Idempotent replay after a lost response.
        self.assertEqual(resolved["finalization_version"], self._absent(replacement)["finalization_version"])
        # A different process nonce replaying after resolution is refused (not unresolved).
        with self.assertRaisesRegex(WorkParallelError, "not unresolved or observed-unbound"):
            self._absent(replacement, nonce="process-3")
        fresh = self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        lease = self.store.read()["leases"][replacement]
        self.assertFalse(worker_token_matches(lease, old_token))
        self.assertTrue(worker_token_matches(lease, fresh["worker_token"]))
        self.assertEqual((2, "resume"), (fresh["generation"], fresh["role"]))
        self.call("event", {"internal": True, "launch_nonce": "process-2"}, event="rework_launch_intent",
                  lease_id=replacement, dispatch_name="resume-b", generation=2)
        self.call("event", LAUNCH_HOST, event="rework_spawn_observed", lease_id=replacement,
                  dispatch_name="resume-b", agent_id="agent-b")
        self.call("event", {"host_id": "agent-b", "agent_name": "resume-b"}, event="bind", lease_id=replacement)
        lease = self.store.read()["leases"][replacement]
        run = m.coordination(self.store).read()["runs"][replacement]
        self.assertEqual(("running", "agent-b"), (lease["state"], lease["host_id"]))
        self.assertEqual(("bound", "agent-b"), (run["state"], run["agent_id"]))
        self.assertEqual("bound", self.review_row(reserve["lease_id"])["launch"]["state"])

    def test_late_agent_after_absent_is_refused_and_never_double_binds(self):
        reserve, replacement, old_token = self._rework_with_intent()
        self._absent(replacement)
        ledger_before = self.store.read()
        # The old fork appears late under its exact name: no observation, no bind.
        with self.assertRaisesRegex(WorkParallelError, "stale rework spawn observation"):
            self.call("event", LAUNCH_HOST, event="rework_spawn_observed", lease_id=replacement,
                      dispatch_name="resume-a", agent_id="agent-late")
        with self.assertRaisesRegex(WorkParallelError, "not observed"):
            self.call("event", {"host_id": "agent-late", "agent_name": "resume-a"}, event="bind",
                      lease_id=replacement)
        self.assertEqual(ledger_before["version"], self.store.read()["version"])
        # The fresh launch binds; afterwards the late agent still cannot take it.
        fresh = self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        self.call("event", {"internal": True, "launch_nonce": "process-2"}, event="rework_launch_intent",
                  lease_id=replacement, dispatch_name="resume-b", generation=2)
        self.call("event", {"host_id": "agent-b", "agent_name": "resume-b"}, event="bind", lease_id=replacement)
        with self.assertRaises(WorkParallelError):
            self.call("event", {"host_id": "agent-late", "agent_name": "resume-a"}, event="bind",
                      lease_id=replacement)
        lease = self.store.read()["leases"][replacement]
        self.assertEqual("agent-b", lease["host_id"])
        bound = [row for row in self.store.read()["leases"].values()
                 if row["item_id"] == self.board.item and row["role"] == "resume" and row["host_id"]]
        self.assertEqual(1, len(bound))
        # The late fork's revoked capability cannot report.
        with patch.object(m, "ledger", return_value=self.store):
            with self.assertRaises(WorkParallelError):
                m.report({"lease_id": replacement, "worker_token": old_token, "event": "completion",
                          "plan_version": fresh["plan_version"], "artifact": "f" * 64, "evidence": "late",
                          "detail": {"summary": "s", "coverage": [], "verification": "v"}},
                         self.board, self.config)

    def test_pre_nonce_intent_counts_as_unknown_process(self):
        reserve, replacement, token = self._rework_with_intent()
        path = self.tmp / "work-parallel-finalization.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["reviews"][reserve["lease_id"]]["launch"]["host_nonce"] = None
        path.write_text(json.dumps(doc), encoding="utf-8")
        self.assertIsNone(self.review_row(reserve["lease_id"])["launch"]["host_nonce"])
        status = self.call("event", LAUNCH_HOST, event="rework_launch_status")["launches"][0]
        self.assertEqual((False, None), (status["intent_nonce_present"], status["same_process"]))
        self.assertEqual("spawn_denied", self._absent(replacement, nonce="process-1")["state"])

    def test_new_launch_intent_requires_host_process_nonce(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        replacement = self.review(reserve["lease_id"], "rework", decision_id="nonce")["dispatch"]["lease_id"]
        self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        before = (self.tmp / "work-parallel-finalization.json").read_bytes()
        for host in ({"internal": True}, {"internal": True, "launch_nonce": ""}):
            with self.subTest(host=host):
                with self.assertRaisesRegex(WorkParallelError, "requires the host process nonce"):
                    self.call("event", host, event="rework_launch_intent", lease_id=replacement,
                              dispatch_name="resume-a", generation=2)
        self.assertEqual(before, (self.tmp / "work-parallel-finalization.json").read_bytes())
        self.assertIsNone(self.store.read()["leases"][replacement]["dispatch_name"])

    def _manual_request(self, review_lease, launch_ref=None, confirmed=True):
        launch = self.review_row(review_lease)["launch"]
        launch_ref = launch["launch_ref"] if launch_ref is None else launch_ref
        with patch.object(m, "ledger", return_value=self.store):
            return m.request_rework_launch_manual(self.store, SCOPE, review_lease, launch_ref,
                                                  confirmed=confirmed,
                                                  expected_version=m.finalization(self.store).read()["version"])

    def test_manual_request_records_only_and_host_resolves_absent(self):
        reserve, replacement, token = self._rework_with_intent()
        launch_ref = self.review_row(reserve["lease_id"])["launch"]["launch_ref"]
        # Without a recorded request the host cannot manually resolve, even in-process.
        with self.assertRaisesRegex(WorkParallelError, "was not requested"):
            self.call("event", {**LAUNCH_HOST, "absent_dispatch_name": "resume-a"},
                      event="rework_launch_manual_resolve", lease_id=replacement,
                      dispatch_name="resume-a", launch_ref=launch_ref)
        with self.assertRaisesRegex(WorkParallelError, "stale rework launch reference"):
            self._manual_request(reserve["lease_id"], "0" * 32)
        requested = self._manual_request(reserve["lease_id"])
        self.assertEqual({"lease_id", "state", "attempt", "launch_ref", "finalization_version"}, set(requested))
        self.assertEqual((reserve["lease_id"], launch_ref, "manual_requested"),
                         (requested["lease_id"], requested["launch_ref"], requested["state"]))
        self.assertEqual("manual_requested", self.review_row(reserve["lease_id"])["launch"]["audit"][-1]["kind"])
        # A request is not an absence assertion: no fresh token until the host checks.
        with self.assertRaisesRegex(WorkParallelError, "unresolved"):
            self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        with self.assertRaisesRegex(WorkParallelError, "not observed"):
            self.call("event", {"host_id": "agent-x", "agent_name": "resume-a"}, event="bind",
                      lease_id=replacement)
        self.assertEqual(requested["finalization_version"],
                         self._manual_request(reserve["lease_id"])["finalization_version"])
        for host, pattern in (
                ({**LAUNCH_HOST}, "assert this exact launch name absent"),
                ({**LAUNCH_HOST, "absent_dispatch_name": "resume-other"}, "assert this exact launch name absent"),
                ({"launch_nonce": "process-1", "absent_dispatch_name": "resume-a"}, "mod-internal")):
            with self.subTest(pattern):
                with self.assertRaisesRegex(WorkParallelError, pattern):
                    self.call("event", host, event="rework_launch_manual_resolve", lease_id=replacement,
                              dispatch_name="resume-a", launch_ref=launch_ref)
        # Same process is allowed once the person asked and the host checked.
        resolved = self.call("event", {**LAUNCH_HOST, "absent_dispatch_name": "resume-a"},
                             event="rework_launch_manual_resolve", lease_id=replacement,
                             dispatch_name="resume-a", launch_ref=launch_ref)
        self.assertEqual(("spawn_denied", "manual"), (resolved["state"], resolved["audit"][-1]["kind"]))
        replay = self.call("event", {**LAUNCH_HOST, "absent_dispatch_name": "resume-a"},
                           event="rework_launch_manual_resolve", lease_id=replacement,
                           dispatch_name="resume-a", launch_ref=launch_ref)
        self.assertEqual(resolved["finalization_version"], replay["finalization_version"])
        self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)

    def test_manual_request_for_present_agent_is_rejected_and_restored(self):
        reserve, replacement, token = self._rework_with_intent()
        self.call("event", LAUNCH_HOST, event="rework_spawn_observed", lease_id=replacement,
                  dispatch_name="resume-a", agent_id="agent-a")
        observed_ref = self.review_row(reserve["lease_id"])["launch"]["launch_ref"]
        self._manual_request(reserve["lease_id"])
        with self.assertRaisesRegex(WorkParallelError, "exact launch name present"):
            self.call("event", {**LAUNCH_HOST, "agent_id": None}, event="rework_launch_present",
                      lease_id=replacement, dispatch_name="resume-a", launch_ref=observed_ref)
        with self.assertRaisesRegex(WorkParallelError, "present launch agent changed"):
            self.call("event", {**LAUNCH_HOST, "present_dispatch_name": "resume-a", "present_agent_id": "agent-z"},
                      event="rework_launch_present", lease_id=replacement, dispatch_name="resume-a",
                      launch_ref=observed_ref)
        present = self.call("event", {**LAUNCH_HOST, "present_dispatch_name": "resume-a",
                                      "present_agent_id": "agent-a"},
                            event="rework_launch_present", lease_id=replacement, dispatch_name="resume-a",
                            launch_ref=observed_ref)
        self.assertEqual(("spawn_observed", "agent present", "present"),
                         (present["state"], present["proof"], present["audit"][-1]["kind"]))
        self.assertNotEqual(observed_ref, present["launch_ref"])
        projected = self.call("status", {})["review"]["pending"][0]
        self.assertEqual("spawn_observed", projected["launch"]["state"])
        # The present agent then binds normally.
        self.call("event", {"host_id": "agent-a", "agent_name": "resume-a"}, event="bind", lease_id=replacement)
        self.assertEqual("bound", self.review_row(reserve["lease_id"])["launch"]["state"])

    # Ledger bind committed, every launch_bound attempt lost to a concurrent
    # finalization write, then a person asked to resolve the "stuck" launch.

    @staticmethod
    def _exhaust_launch_bound():
        """One real concurrent finalization write before every launch_bound CAS."""
        original = WorkParallelFinalizationStore.launch_bound

        def racing(store, replacement_lease_id, agent_id, agent_name=None, *, expected_version):
            current = store.read()
            source = next(row["lease_id"] for row in current["reviews"].values()
                          if row.get("replacement_lease_id") == replacement_lease_id)

            def touch(state):
                state["reviews"][source]["updated_at"] = time.time()
            store._change(touch, expected_version=current["version"])
            return original(store, replacement_lease_id, agent_id, agent_name, expected_version=expected_version)
        return patch.object(WorkParallelFinalizationStore, "launch_bound", racing)

    def _final_bytes(self):
        return (self.tmp / "work-parallel-finalization.json").read_bytes()

    def _ledger_bound_launch_requested(self, manual_from):
        reserve, replacement, token = self._rework_with_intent()
        if manual_from == "spawn_observed":
            self.call("event", LAUNCH_HOST, event="rework_spawn_observed", lease_id=replacement,
                      dispatch_name="resume-a", agent_id="agent-a")
        with self._exhaust_launch_bound():
            with self.assertRaisesRegex(WorkParallelError, "^Work finalization state changed; reread$"):
                self.call("event", {"host_id": "agent-a", "agent_name": "resume-a"}, event="bind",
                          lease_id=replacement)
        lease = self.store.read()["leases"][replacement]
        self.assertEqual(("running", "agent-a", "resume-a"), (lease["state"], lease["host_id"], lease["agent_name"]))
        self.assertEqual(manual_from, self.review_row(reserve["lease_id"])["launch"]["state"])
        # The web console's confirmed request for this exact launch.
        self._manual_request(reserve["lease_id"])
        launch = self.review_row(reserve["lease_id"])["launch"]
        self.assertEqual(("manual_requested", manual_from, "manual_requested"),
                         (launch["state"], launch["manual_from"], launch["audit"][-1]["kind"]))
        return reserve, replacement, token, launch

    def _assert_request_settled_by_exact_bind(self, manual_from):
        reserve, replacement, token, requested = self._ledger_bound_launch_requested(manual_from)
        with patch.object(m, "ledger", return_value=self.store):
            m.report({"lease_id": replacement, "worker_token": token, "event": "completion",
                      "plan_version": self.store.read()["leases"][replacement]["plan_version"],
                      "artifact": "d" * 64, "evidence": "fixed",
                      "detail": {"summary": "fixed", "coverage": [{"item_id": self.board.item, "verified": True}],
                                 "verification": "fixed"}}, self.board, self.config)
        # Stuck before review: the workspace was never bound to the fork.
        with self.assertRaisesRegex(WorkspaceError, "workspace host binding mismatch"):
            self.idle(replacement)
        self.assertIsNone(self.review_row(replacement))
        # The host's idempotent bind replay for the ledger's own host id settles it.
        self.call("event", {"host_id": "agent-a", "agent_name": "resume-a"}, event="bind", lease_id=replacement)
        launch = self.review_row(reserve["lease_id"])["launch"]
        self.assertEqual(("bound", "agent-a", None, None),
                         (launch["state"], launch["agent_id"], launch["launch_ref"], launch["manual_from"]))
        # Only existing fields change; the request's audit row stays as history.
        self.assertEqual(requested["audit"], launch["audit"])
        self.assertEqual({k: v for k, v in requested.items() if k not in ("state", "agent_id", "launch_ref",
                                                                         "manual_from", "updated_at")},
                         {k: v for k, v in launch.items() if k not in ("state", "agent_id", "launch_ref",
                                                                      "manual_from", "updated_at")})
        WorkParallelFinalizationStore(self.tmp / "work-parallel-finalization.json").read()  # validates
        settled = self._final_bytes()
        self.call("event", {"host_id": "agent-a", "agent_name": "resume-a"}, event="bind", lease_id=replacement)
        self.assertEqual(settled, self._final_bytes())
        # bound is terminal for every launch transition.
        for label, attempt, pattern in (
                ("manual request", lambda: self._manual_request(reserve["lease_id"], requested["launch_ref"]),
                 "not unresolved or observed-unbound"),
                ("manual resolve", lambda: self.call(
                    "event", {**LAUNCH_HOST, "absent_dispatch_name": "resume-a", "absent_agent_id": "agent-a"},
                    event="rework_launch_manual_resolve", lease_id=replacement, dispatch_name="resume-a",
                    launch_ref=requested["launch_ref"]), "was not requested"),
                ("present", lambda: self.call(
                    "event", {**LAUNCH_HOST, "present_dispatch_name": "resume-a", "present_agent_id": "agent-a"},
                    event="rework_launch_present", lease_id=replacement, dispatch_name="resume-a",
                    launch_ref=requested["launch_ref"]), "was not requested"),
                ("absent", lambda: self._absent(replacement, absent_agent_id="agent-a"), "not unresolved")):
            with self.subTest(label):
                with self.assertRaisesRegex(WorkParallelError, pattern):
                    attempt()
                self.assertEqual(settled, self._final_bytes())
        # The replacement now publishes its own review and is finalized.
        self.idle(replacement)
        self.assertEqual("pending", self.review_row(replacement)["state"])
        self.assertEqual([replacement], self._projected_ids())
        self.assertEqual("finalized", self.review(replacement, "approve", decision_id="second")["state"])
        self.assertEqual([], self._projected_ids())
        raw = json.loads(self.store.path.read_text(encoding="utf-8"))
        for module in (legacy_module(commit) for commit in LEGACY_COMMITS):
            with self.subTest(module=module.__name__):
                module.validate(json.loads(json.dumps(raw)))
                self.assertEqual(raw["version"], module.WorkParallelStore(self.store.path).read()["version"])

    def test_request_on_ledger_bound_observed_launch_is_settled_by_the_exact_bind_replay(self):
        self._assert_request_settled_by_exact_bind("spawn_observed")

    def test_request_on_ledger_bound_named_intent_is_settled_by_its_dispatch_name(self):
        self._assert_request_settled_by_exact_bind("launch_intent")

    def _assert_requested_launch_refuses_others(self, manual_from):
        reserve, replacement, token, requested = self._ledger_bound_launch_requested(manual_from)
        reviews = WorkParallelFinalizationStore(self.tmp / "work-parallel-finalization.json")
        before = self._final_bytes()
        # The store matches what the request was made from: the observed agent id,
        # or (like an unrequested intent) the exact dispatch name. For a named
        # intent the agent id is the ledger's to check: only the idempotent branch,
        # whose host id equals the ledger's, reaches launch_bound for this state.
        if manual_from == "spawn_observed":
            wrong = [("agent-z", "resume-a"), ("agent-z", None)]
        else:
            wrong = [("agent-a", "resume-other"), ("agent-a", None), ("agent-z", "resume-other"), ("agent-z", None)]
        for agent, name in wrong:
            with self.subTest(agent=agent, name=name):
                with self.assertRaisesRegex(WorkParallelError, "^rework launch was not observed for this agent$"):
                    reviews.launch_bound(replacement, agent, name, expected_version=reviews.read()["version"])
        # Through the bind event: another host id is a fresh bind, refused before
        # any ledger write; the ledger's host id with another name is refused too.
        hosts = [{"host_id": "agent-z", "agent_name": "resume-a"}]
        if manual_from == "launch_intent":
            hosts.append({"host_id": "agent-a", "agent_name": "resume-other"})
        for host in hosts:
            with self.subTest(host=host):
                with self.assertRaisesRegex(WorkParallelError, "not observed"):
                    self.call("event", host, event="bind", lease_id=replacement)
        self.assertEqual(before, self._final_bytes())
        self.assertEqual("agent-a", self.store.read()["leases"][replacement]["host_id"])
        self.assertEqual("manual_requested", self.review_row(reserve["lease_id"])["launch"]["state"])
        # Control: the exact replay binds.
        self.call("event", {"host_id": "agent-a", "agent_name": "resume-a"}, event="bind", lease_id=replacement)
        self.assertEqual("bound", self.review_row(reserve["lease_id"])["launch"]["state"])

    def test_requested_observed_launch_refuses_any_other_agent(self):
        self._assert_requested_launch_refuses_others("spawn_observed")

    def test_requested_named_intent_refuses_any_other_agent_or_name(self):
        self._assert_requested_launch_refuses_others("launch_intent")

    def test_observed_launch_that_never_binds_is_resolved_by_later_process(self):
        reserve, replacement, old_token = self._rework_with_intent()
        self.call("event", LAUNCH_HOST, event="rework_spawn_observed", lease_id=replacement,
                  dispatch_name="resume-a", agent_id="agent-a")
        # Same process, name absent, or the exact observed agent not named: refused.
        with self.assertRaisesRegex(WorkParallelError, "belongs to this host process"):
            self._absent(replacement, nonce="process-1", absent_agent_id="agent-a")
        with self.assertRaisesRegex(WorkParallelError, "exact observed agent absent"):
            self._absent(replacement)
        with self.assertRaisesRegex(WorkParallelError, "exact observed agent absent"):
            self._absent(replacement, absent_agent_id="agent-other")
        self.assertEqual("spawn_observed", self.review_row(reserve["lease_id"])["launch"]["state"])
        resolved = self._absent(replacement, absent_agent_id="agent-a")
        self.assertEqual(("spawn_denied", None), (resolved["state"], resolved["agent_id"]))
        # The vanished agent reappearing is refused at bind; a fresh dispatch binds once.
        with self.assertRaisesRegex(WorkParallelError, "not observed"):
            self.call("event", {"host_id": "agent-a", "agent_name": "resume-a"}, event="bind",
                      lease_id=replacement)
        fresh = self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        self.assertNotEqual(old_token, fresh["worker_token"])
        self.call("event", {"internal": True, "launch_nonce": "process-2"}, event="rework_launch_intent",
                  lease_id=replacement, dispatch_name="resume-b", generation=2)
        self.call("event", LAUNCH_HOST, event="rework_spawn_observed", lease_id=replacement,
                  dispatch_name="resume-b", agent_id="agent-b")
        self.call("event", {"host_id": "agent-b", "agent_name": "resume-b"}, event="bind", lease_id=replacement)
        with self.assertRaises(WorkParallelError):
            self.call("event", {"host_id": "agent-a", "agent_name": "resume-a"}, event="bind",
                      lease_id=replacement)
        self.assertEqual("agent-b", self.store.read()["leases"][replacement]["host_id"])

    def test_manual_request_for_observed_agent_requires_exact_agent_absent(self):
        reserve, replacement, token = self._rework_with_intent()
        self.call("event", LAUNCH_HOST, event="rework_spawn_observed", lease_id=replacement,
                  dispatch_name="resume-a", agent_id="agent-a")
        launch_ref = self.review_row(reserve["lease_id"])["launch"]["launch_ref"]
        self._manual_request(reserve["lease_id"])
        with self.assertRaisesRegex(WorkParallelError, "exact observed agent absent"):
            self.call("event", {**LAUNCH_HOST, "absent_dispatch_name": "resume-a"},
                      event="rework_launch_manual_resolve", lease_id=replacement,
                      dispatch_name="resume-a", launch_ref=launch_ref)
        resolved = self.call("event", {**LAUNCH_HOST, "absent_dispatch_name": "resume-a",
                                       "absent_agent_id": "agent-a"},
                             event="rework_launch_manual_resolve", lease_id=replacement,
                             dispatch_name="resume-a", launch_ref=launch_ref)
        self.assertEqual("spawn_denied", resolved["state"])

    def test_manual_request_ref_is_idempotent_stale_after_redispatch_and_wrong_for_other_review(self):
        reserve, replacement, token = self._rework_with_intent(decision_id="ref-one")
        first_ref = self.review_row(reserve["lease_id"])["launch"]["launch_ref"]
        first = self._manual_request(reserve["lease_id"])
        self.assertEqual(first["finalization_version"],
                         self._manual_request(reserve["lease_id"], first_ref)["finalization_version"])
        self.call("event", {**LAUNCH_HOST, "absent_dispatch_name": "resume-a"},
                  event="rework_launch_manual_resolve", lease_id=replacement,
                  dispatch_name="resume-a", launch_ref=first_ref)
        self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        intent = self.call("event", LAUNCH_HOST, event="rework_launch_intent", lease_id=replacement,
                           dispatch_name="resume-b", generation=2)
        self.assertNotEqual(first_ref, intent["launch_ref"])
        with self.assertRaisesRegex(WorkParallelError, "stale rework launch reference"):
            self._manual_request(reserve["lease_id"], first_ref)

        other, other_replacement, _ = self._rework_with_intent(
            decision_id="ref-two", name="resume-other", nonce="process-other", item_id=self.board.item2)
        other_ref = self.review_row(other["lease_id"])["launch"]["launch_ref"]
        # Each ref belongs to exactly one review row: a swapped pair is refused, nothing written.
        before = self._all_files()
        with self.assertRaisesRegex(WorkParallelError, "stale rework launch reference"):
            self._manual_request(reserve["lease_id"], other_ref)
        with self.assertRaisesRegex(WorkParallelError, "stale rework launch reference"):
            self._manual_request(other["lease_id"], intent["launch_ref"])
        self.assertEqual(before, self._all_files())
        self.assertEqual("manual_requested", self._manual_request(other["lease_id"], other_ref)["state"])

    def test_manual_request_is_strict_bounded_and_refused_after_resolution(self):
        reserve, replacement, token = self._rework_with_intent()
        launch_ref = self.review_row(reserve["lease_id"])["launch"]["launch_ref"]
        before = (self.tmp / "work-parallel-finalization.json").read_bytes()
        for ref, confirmed, pattern in (
                (launch_ref, False, "explicit confirmation"),
                ("r" * 33, True, "exact rework launch reference"),
                ("", True, "exact rework launch reference"),
                ("0" * 32, True, "stale rework launch reference")):
            with self.subTest(ref=ref[:8], confirmed=confirmed):
                with self.assertRaisesRegex(WorkParallelError, pattern):
                    self._manual_request(reserve["lease_id"], ref, confirmed)
                self.assertEqual(before, (self.tmp / "work-parallel-finalization.json").read_bytes())
        with patch.object(m, "ledger", return_value=self.store):
            for lease_id in (None, "x" * 201, "not-a-lease"):
                with self.subTest(lease_id=lease_id):
                    with self.assertRaises(WorkParallelError):
                        m.request_rework_launch_manual(self.store, SCOPE, lease_id, launch_ref, confirmed=True,
                                                       expected_version=m.finalization(self.store).read()["version"])
            with self.assertRaisesRegex(WorkParallelError, "unknown rework launch"):
                m.request_rework_launch_manual(self.store, "claude:other", reserve["lease_id"], launch_ref,
                                               confirmed=True,
                                               expected_version=m.finalization(self.store).read()["version"])
            with self.assertRaisesRegex(WorkParallelError, "changed; reread"):
                m.request_rework_launch_manual(self.store, SCOPE, reserve["lease_id"], launch_ref, confirmed=True,
                                               expected_version=m.finalization(self.store).read()["version"] + 3)
        first = self._manual_request(reserve["lease_id"])
        self.assertEqual(first["finalization_version"],
                         self._manual_request(reserve["lease_id"])["finalization_version"])
        audit = self.review_row(reserve["lease_id"])["launch"]["audit"]
        self.assertEqual(1, sum(row["kind"] == "manual_requested" for row in audit))
        self.call("event", {**LAUNCH_HOST, "absent_dispatch_name": "resume-a"},
                  event="rework_launch_manual_resolve", lease_id=replacement,
                  dispatch_name="resume-a", launch_ref=launch_ref)
        with self.assertRaisesRegex(WorkParallelError, "not unresolved or observed-unbound"):
            self._manual_request(reserve["lease_id"], launch_ref)
        self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        second = self.call("event", LAUNCH_HOST, event="rework_launch_intent", lease_id=replacement,
                           dispatch_name="resume-b", generation=2)
        self.call("event", {"host_id": "agent-b", "agent_name": "resume-b"}, event="bind", lease_id=replacement)
        with self.assertRaisesRegex(WorkParallelError, "not unresolved or observed-unbound"):
            self._manual_request(reserve["lease_id"], second["launch_ref"])

    def test_menu_manual_request_event_needs_internal_host_and_confirmation(self):
        reserve, replacement, token = self._rework_with_intent()
        launch_ref = self.review_row(reserve["lease_id"])["launch"]["launch_ref"]
        for host, pattern in (({"internal": True}, "explicit confirmation"),
                              ({"user_confirmed": True}, "mod-internal launch host required"),
                              ({"agent_id": None, "user_confirmed": True}, "mod-internal launch host required"),
                              ({"agent_id": "fork-1", "internal": True, "user_confirmed": True},
                               "mod-internal launch host required")):
            with self.subTest(host=host):
                with self.assertRaisesRegex(WorkParallelError, pattern):
                    self.call("event", host, event="rework_launch_manual_request", lease_id=reserve["lease_id"],
                              launch_ref=launch_ref)
        result = self.call("event", {"internal": True, "user_confirmed": True},
                           event="rework_launch_manual_request", lease_id=reserve["lease_id"],
                           launch_ref=launch_ref)
        self.assertEqual(("manual_requested", launch_ref), (result["state"], result["launch_ref"]))

    def test_host_launch_status_reports_recovery_facts_only_to_internal_host(self):
        reserve, replacement, token = self._rework_with_intent(name="resume-secret", nonce="nonce-secret")
        for host in ({}, {"agent_id": None}, {"agent_id": "fork-1", "internal": True}, {"fork": True}):
            with self.subTest(host=host):
                with self.assertRaisesRegex(WorkParallelError, "mod-internal launch host required"):
                    self.call("event", host, event="rework_launch_status")
        same = self.call("event", {"internal": True, "launch_nonce": "nonce-secret"},
                         event="rework_launch_status")
        row = same["launches"][0]
        self.assertEqual({"review_lease_id", "lease_id", "item_id", "generation", "dispatch_name", "state",
                          "attempt", "launch_ref", "manual_requested", "manual_from", "intent_nonce_present",
                          "same_process", "observed_agent_id", "updated_at"}, set(row))
        self.assertRegex(row["launch_ref"], r"^[0-9a-f]{32}$")
        self.assertEqual((reserve["lease_id"], replacement, "resume-secret", "launch_intent", 1, True, True, None, False),
                         (row["review_lease_id"], row["lease_id"], row["dispatch_name"], row["state"], row["attempt"],
                          row["intent_nonce_present"], row["same_process"], row["observed_agent_id"],
                          row["manual_requested"]))
        self.assertNotIn("nonce-secret", json.dumps(same))
        other = self.call("event", {"internal": True, "launch_nonce": "nonce-later"}, event="rework_launch_status")
        self.assertFalse(other["launches"][0]["same_process"])
        self.assertIsNone(self.call("event", {"internal": True}, event="rework_launch_status")
                          ["launches"][0]["same_process"])
        self.call("event", {"internal": True}, event="rework_spawn_observed", lease_id=replacement,
                  dispatch_name="resume-secret", agent_id="agent-secret")
        observed_ref = self.review_row(reserve["lease_id"])["launch"]["launch_ref"]
        self._manual_request(reserve["lease_id"])
        row = self.call("event", {"internal": True, "launch_nonce": "nonce-later"},
                        event="rework_launch_status")["launches"][0]
        self.assertEqual(("manual_requested", observed_ref, True, "spawn_observed", "agent-secret"),
                         (row["state"], row["launch_ref"], row["manual_requested"], row["manual_from"],
                          row["observed_agent_id"]))
        self.call("event", {"internal": True, "present_dispatch_name": "resume-secret",
                            "present_agent_id": "agent-secret"},
                  event="rework_launch_present", lease_id=replacement, dispatch_name="resume-secret",
                  launch_ref=observed_ref)
        self.call("event", {"host_id": "agent-secret", "agent_name": "resume-secret"}, event="bind",
                  lease_id=replacement)
        self.assertEqual([], self.call("event", {"internal": True}, event="rework_launch_status")["launches"])

    def test_main_and_public_projections_redact_unbound_launch_details(self):
        from ciel_runtime_support import work_mcp_tools
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        review_result = self.review(reserve["lease_id"], "rework", decision_id="redact")
        replacement = review_result["dispatch"]["lease_id"]
        dispatched = self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        self.call("event", {"internal": True, "launch_nonce": "nonce-secret"}, event="rework_launch_intent",
                  lease_id=replacement, dispatch_name="resume-secret", generation=2)
        self.call("event", {"internal": True}, event="rework_spawn_observed", lease_id=replacement,
                  dispatch_name="resume-secret", agent_id="agent-secret")
        launch_ref = self.review_row(reserve["lease_id"])["launch"]["launch_ref"]
        secrets = ("resume-secret", "nonce-secret", "agent-secret", dispatched["worker_token"])
        with patch.object(m, "ledger", return_value=self.store):
            model_status = m.run("work_parallel_status", {}, self.board, SCOPE, self.config, None)
            host_status = self.call("status", {})
            public = json.dumps(m.public_projection(json.loads(model_status), SCOPE))
            board_status = work_mcp_tools._run("work_status", {"format": "json"}, self.board.store, False)
        replay = self.call("item_review", {}, lease_id=reserve["lease_id"], decision="rework",
                           decision_id="redact", evidence="main verified", expected_finalization_version=0)
        for label, payload in (("work_parallel_status", model_status), ("browser /parallel", public),
                               ("work_status", board_status), ("item_review", json.dumps(review_result)),
                               ("item_review replay", json.dumps(replay))):
            for secret in secrets:
                with self.subTest(view=label, secret=secret[:12]):
                    self.assertNotIn(secret, payload)
        model_row = json.loads(model_status)["review"]["pending"][0]
        self.assertEqual({"state": "spawn_observed", "attempt": 1, "launch_ref": launch_ref},
                         model_row["launch"])
        self.assertNotIn("dispatch_name", json.dumps(model_row))
        # Authenticated host work_parallel_status keeps fields used by mod reconciliation.
        self.assertEqual("resume-secret", host_status["leases"][replacement]["dispatch_name"])
        self.assertIn("resume-secret", json.dumps(host_status["forks"]))
        self.assertNotIn("resume-secret", json.dumps(json.loads(model_status)["forks"]))
        host = self.call("event", {"internal": True, "launch_nonce": "nonce-later"},
                         event="rework_launch_status")
        self.assertEqual(("resume-secret", "agent-secret", launch_ref),
                         (host["launches"][0]["dispatch_name"], host["launches"][0]["observed_agent_id"],
                          host["launches"][0]["launch_ref"]))

    def model_status(self, **args):
        with patch.object(m, "ledger", return_value=self.store):
            return json.loads(m.run("work_parallel_status", args, self.board, SCOPE, self.config, None))

    def test_only_authenticated_host_status_keeps_unbound_launch_lease_names(self):
        reserve, replacement, token = self._rework_with_intent(name="resume-secret")
        host = self.call("status", {})["leases"][replacement]
        self.assertEqual("resume-secret", host["dispatch_name"])
        forged = ({}, {"_host": {}}, {"_host": {"token": "wrong-token-" * 4}},
                  {"_host": {"token": SECRET, "agent_id": "fork-1"}}, {"_host": "not-a-host"})
        for args in forged:
            with self.subTest(args=str(args)[:40]):
                status = self.model_status(**args)
                self.assertNotIn("resume-secret", json.dumps(status))
                self.assertNotIn("dispatch_name", status["leases"][replacement])
        # Event results are host-facing only on the mod-internal path.
        main_event = self.call("configure", {"agent_id": None}, changes={})
        self.assertNotIn("resume-secret", json.dumps(main_event))
        internal_event = self.call("configure", {"internal": True}, changes={})
        self.assertEqual("resume-secret", internal_event["leases"][replacement]["dispatch_name"])
        # A corrupt finalization journal cannot reveal the name either.
        (self.tmp / "work-parallel-finalization.json").write_text("{", encoding="utf-8")
        self.assertNotIn("resume-secret", json.dumps(self.model_status()))
        self.assertEqual("resume-secret", self.call("status", {})["leases"][replacement]["dispatch_name"])

    def test_review_wake_and_roll_forward_need_mod_internal_host(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        self._block_p1()
        self.review(reserve["lease_id"], "rework", decision_id="pump", evidence="redo it")
        forged_main = {"agent_id": None, "fork": True, "user_confirmed": True, "launch_nonce": "process-9"}
        forked = {"agent_id": "fork-1", "internal": True}
        calls = (("review_wake", {"operation": "prepare"}),
                 ("review_wake", {"operation": "record", "attempt_id": "x", "state": "queued"}),
                 ("review_roll_forward", {}))
        self._unblock_p1()
        for host in (forged_main, forked, {}):
            for event, args in calls:
                with self.subTest(host=str(host)[:30], event=event, operation=args.get("operation")):
                    before = self._all_files()
                    with self.assertRaisesRegex(WorkParallelError, "mod-internal review host required"):
                        self.call("event", host, event=event, lease_id=reserve["lease_id"], **args)
                    self.assertEqual(before, self._all_files())
        self.assertEqual("awaiting_capacity", self.review_row(reserve["lease_id"])["state"])
        ready = self.call("event", {"internal": True}, event="review_roll_forward", lease_id=reserve["lease_id"])
        self.assertEqual("rework_ready", ready["state"])

    def test_restart_absent_uses_host_status_names_for_intent_and_observed_launches(self):
        reserve, replacement, token = self._rework_with_intent(name="resume-first", nonce="process-1")
        intent_ref = self.review_row(reserve["lease_id"])["launch"]["launch_ref"]
        # A restarted host knows nothing but its new nonce; the host read supplies the exact name.
        row = self.call("event", {"internal": True, "launch_nonce": "process-2"},
                        event="rework_launch_status")["launches"][0]
        self.assertEqual(("launch_intent", False, None, intent_ref),
                         (row["state"], row["same_process"], row["observed_agent_id"], row["launch_ref"]))
        self._absent(row["lease_id"], name=row["dispatch_name"], nonce="process-2")
        row = self.call("event", {"internal": True, "launch_nonce": "process-2"},
                        event="rework_launch_status")["launches"][0]
        self.assertEqual(("spawn_denied", None), (row["state"], row["launch_ref"]))
        self.assertIsNone(self.review_row(reserve["lease_id"])["launch"]["launch_ref"])
        self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        intent = self.call("event", {"internal": True, "launch_nonce": "process-2"}, event="rework_launch_intent",
                           lease_id=replacement, dispatch_name="resume-second", generation=2)
        observed = self.call("event", {"internal": True, "launch_nonce": "process-2"},
                             event="rework_spawn_observed", lease_id=replacement,
                             dispatch_name="resume-second", agent_id="agent-second")
        self.assertNotEqual(intent["launch_ref"], observed["launch_ref"])
        self.assertNotEqual(intent_ref, intent["launch_ref"])
        row = self.call("event", {"internal": True, "launch_nonce": "process-3"},
                        event="rework_launch_status")["launches"][0]
        self.assertEqual(("spawn_observed", "resume-second", "agent-second", False, observed["launch_ref"]),
                         (row["state"], row["dispatch_name"], row["observed_agent_id"], row["same_process"],
                          row["launch_ref"]))
        resolved = self._absent(row["lease_id"], name=row["dispatch_name"], nonce="process-3",
                                absent_agent_id=row["observed_agent_id"])
        self.assertEqual(("spawn_denied", None), (resolved["state"], resolved["launch_ref"]))

    def test_schema_four_unresolved_launch_gets_a_stable_reference_until_written(self):
        reserve, replacement, token = self._rework_with_intent()
        path = self.tmp / "work-parallel-finalization.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["schema"] = 4
        for row in doc["reviews"].values():
            row["launch"].pop("launch_ref")
        path.write_text(json.dumps(doc), encoding="utf-8")
        first = self.review_row(reserve["lease_id"])["launch"]["launch_ref"]
        self.assertRegex(first, r"^[0-9a-f]{32}$")
        self.assertEqual(first, self.review_row(reserve["lease_id"])["launch"]["launch_ref"])
        self.assertEqual(first, self.model_status()["review"]["pending"][0]["launch"]["launch_ref"])
        requested = self._manual_request(reserve["lease_id"], first)
        self.assertEqual(("manual_requested", first), (requested["state"], requested["launch_ref"]))
        self.assertEqual(5, json.loads(path.read_text(encoding="utf-8"))["schema"])

    def test_launch_audit_is_bounded(self):
        reserve, replacement, token = self._rework_with_intent()
        for attempt in range(25):
            name = "resume-%02d" % attempt
            if attempt:
                self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
                self.call("event", LAUNCH_HOST, event="rework_launch_intent", lease_id=replacement,
                          dispatch_name=name, generation=2)
            else:
                name = "resume-a"
            self.call("event", {**LAUNCH_HOST, "proof": "denied " + name}, event="rework_launch_denied",
                      lease_id=replacement, dispatch_name=name)
        launch = self.review_row(reserve["lease_id"])["launch"]
        self.assertEqual(20, len(launch["audit"]))
        self.assertEqual(("resume-24", 25), (launch["audit"][-1]["dispatch_name"], launch["audit"][-1]["attempt"]))

    def test_schema_two_launch_migrates_with_implied_denial_audit(self):
        reserve, replacement, token = self._rework_with_intent()
        self.call("event", {**LAUNCH_HOST, "proof": "native spawn denied"}, event="rework_launch_denied",
                  lease_id=replacement, dispatch_name="resume-a")
        path = self.tmp / "work-parallel-finalization.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["schema"] = 2
        for row in doc["reviews"].values():
            row["launch"].pop("host_nonce")
            row["launch"].pop("manual_from")
            row["launch"].pop("launch_ref")
            row["launch"].pop("audit")
        path.write_text(json.dumps(doc), encoding="utf-8")
        migrated = WorkParallelFinalizationStore(path).read()
        launch = migrated["reviews"][reserve["lease_id"]]["launch"]
        self.assertEqual(5, migrated["schema"])
        self.assertEqual(("spawn_denied", None, "denied"),
                         (launch["state"], launch["host_nonce"], launch["audit"][-1]["kind"]))
        self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)

    def test_legacy_resume_lease_binds_without_a_review_row(self):
        doc = self.store.read()
        doc["leases"]["legacy-resume"] = {
            "id": "legacy-resume", "scope": SCOPE, "item_id": self.board.item2, "priority": "P1",
            "created_at": 1, "role": "resume", "mode": "fork", "plan_version": 1, "state": "reserved",
            "host_id": None, "slot": True, "usage": .05, "artifact": None, "rework": 0, "evidence": None,
            "idle_proof": None, "cleanup_proof": None, "report_hash": "c" * 64}
        self.store.path.write_text(json.dumps(doc), encoding="utf-8")
        # Neither a main-review fence nor a coordination run exists for an old
        # build's lease: the bind succeeds ledger-only, never half-committed.
        self.assertNotIn("legacy-resume", m.coordination(self.store).read()["runs"])
        self.call("event", {"host_id": "legacy-agent"}, event="bind", lease_id="legacy-resume")
        lease = self.store.read()["leases"]["legacy-resume"]
        self.assertEqual(("running", "legacy-agent"), (lease["state"], lease["host_id"]))
        self.assertNotIn("legacy-resume", m.coordination(self.store).read()["runs"])
        # A lost response replays idempotently.
        self.call("event", {"host_id": "legacy-agent"}, event="bind", lease_id="legacy-resume")

    def test_legacy_worker_lease_without_coordination_run_binds(self):
        doc = self.store.read()
        doc["leases"]["legacy-worker"] = {
            "id": "legacy-worker", "scope": SCOPE, "item_id": self.board.item2, "priority": "P1",
            "created_at": 1, "role": "worker", "mode": "fork", "plan_version": 1, "state": "reserved",
            "host_id": None, "slot": True, "usage": .05, "artifact": None, "rework": 0, "evidence": None,
            "idle_proof": None, "cleanup_proof": None, "report_hash": "d" * 64}
        self.store.path.write_text(json.dumps(doc), encoding="utf-8")
        self.call("event", {"host_id": "old-build-agent", "agent_name": "old"}, event="bind",
                  lease_id="legacy-worker")
        lease = self.store.read()["leases"]["legacy-worker"]
        self.assertEqual(("running", "old-build-agent", "old"),
                         (lease["state"], lease["host_id"], lease["agent_name"]))

    def _ledger_bound_coordination_reserved(self, agent="agent-A"):
        """The defect state: ledger bind committed, coordination half lost."""
        reserve = self.call("event", {"fork": True}, event="reserve", item_id=self.board.item, priority="P1")
        self.store.bind(reserve["lease_id"], agent, expected_version=self.store.read()["version"],
                        agent_name="name-A")
        run = m.coordination(self.store).read()["runs"][reserve["lease_id"]]
        self.assertEqual(("reserved", None), (run["state"], run["agent_id"]))
        return reserve["lease_id"]

    def test_bind_retries_coordination_cas_after_unrelated_write(self):
        from ciel_runtime_support.work_parallel_receipts import WorkCoordinationStore
        reserve = self.call("event", {"fork": True}, event="reserve", item_id=self.board.item, priority="P1")
        original = WorkCoordinationStore.bind_run
        injected = []

        def racing(self_, *args, **kwargs):
            if len(injected) < 3:
                # Another session's unrelated device-wide write lands between
                # our read and our CAS.
                injected.append(1)
                self_.diagnostic("claude:other", "P2", "capacity", expected_version=self_.read()["version"])
            return original(self_, *args, **kwargs)
        with patch.object(WorkCoordinationStore, "bind_run", racing):
            self.call("event", {"host_id": "agent-A", "agent_name": "name-A"}, event="bind",
                      lease_id=reserve["lease_id"])
        self.assertEqual(3, len(injected))
        lease = self.store.read()["leases"][reserve["lease_id"]]
        run = m.coordination(self.store).read()["runs"][reserve["lease_id"]]
        self.assertEqual(("running", "agent-A"), (lease["state"], lease["host_id"]))
        self.assertEqual(("bound", "agent-A"), (run["state"], run["agent_id"]))

    def _other_bound_run(self):
        """A second bound fork whose coordination run other writers touch."""
        other = self.call("event", {"fork": True}, event="reserve", item_id=self.board.item2, priority="P1")
        self.call("event", {"host_id": "agent-other"}, event="bind", lease_id=other["lease_id"])
        return other["lease_id"]

    def _bind_with_window_write(self, write):
        from ciel_runtime_support.work_parallel_receipts import WorkCoordinationStore
        reserve = self.call("event", {"fork": True}, event="reserve", item_id=self.board.item, priority="P1")
        original = WorkCoordinationStore.bind_run
        fired = []

        def interleaved(self_, *args, **kwargs):
            if not fired:
                # The window: the ledger bind has committed, our coordination
                # version is already read, and another writer commits first.
                fired.append(self.store.read()["leases"][reserve["lease_id"]]["host_id"])
                write(self_)
            return original(self_, *args, **kwargs)
        with patch.object(WorkCoordinationStore, "bind_run", interleaved):
            self.call("event", {"host_id": "agent-A"}, event="bind", lease_id=reserve["lease_id"])
        self.assertEqual(["agent-A"], fired)
        lease = self.store.read()["leases"][reserve["lease_id"]]
        run = m.coordination(self.store).read()["runs"][reserve["lease_id"]]
        self.assertEqual(("running", "agent-A"), (lease["state"], lease["host_id"]))
        self.assertEqual(("bound", "agent-A"), (run["state"], run["agent_id"]))

    def test_unrelated_update_native_in_window_leaves_both_stores_bound(self):
        other = self._other_bound_run()
        self._bind_with_window_write(
            lambda coord: coord.update_native(other, "running", expected_version=coord.read()["version"]))
        self.assertEqual("running", m.coordination(self.store).read()["runs"][other]["native_status"])

    def test_unrelated_persist_receipt_in_window_leaves_both_stores_bound(self):
        other = self._other_bound_run()
        detail = {"question": "which schema?", "blocking_todos": [self.board.item2],
                  "decision_needed": "pick one"}
        self._bind_with_window_write(
            lambda coord: coord.persist_receipt(other, "question", detail,
                                                expected_version=coord.read()["version"]))
        self.assertEqual("question", m.coordination(self.store).read()["runs"][other]["exit"]["kind"])

    def _snapshot_bytes(self):
        return {name: (self.tmp / name).read_bytes()
                for name in ("work-parallel.json", "work-parallel-coordination.json")}

    def test_real_conflict_on_fresh_bind_leaves_both_files_byte_identical(self):
        reserve = self.call("event", {"fork": True}, event="reserve", item_id=self.board.item, priority="P1")
        path = self.tmp / "work-parallel-coordination.json"
        coord = json.loads(path.read_text(encoding="utf-8"))
        coord["runs"][reserve["lease_id"]].update(agent_id="agent-B", agent_name="agent-B", state="bound",
                                                  bound_at=1, native_status="pending", native_seen_at=1)
        path.write_text(json.dumps(coord), encoding="utf-8")
        before = self._snapshot_bytes()
        with self.assertRaisesRegex(WorkParallelError, "run is not bindable"):
            self.call("event", {"host_id": "agent-A"}, event="bind", lease_id=reserve["lease_id"])
        self.assertEqual(before, self._snapshot_bytes())

    def test_real_conflict_on_replayed_bind_leaves_both_files_byte_identical(self):
        ident = self._ledger_bound_coordination_reserved()
        path = self.tmp / "work-parallel-coordination.json"
        coord = json.loads(path.read_text(encoding="utf-8"))
        coord["runs"][ident].update(agent_id="agent-B", agent_name="agent-B", state="bound",
                                    bound_at=1, native_status="pending", native_seen_at=1)
        path.write_text(json.dumps(coord), encoding="utf-8")
        before = self._snapshot_bytes()
        with self.assertRaisesRegex(WorkParallelError, "run is not bindable"):
            self.call("event", {"host_id": "agent-A"}, event="bind", lease_id=ident)
        self.assertEqual(before, self._snapshot_bytes())

    def _split_lease_with_token(self, item=None, workspace=False):
        """Ledger bound to agent-A, coordination run still reserved/None."""
        host = {"fork": True, **({"workspace_repo": str(self.repo)} if workspace else {})}
        reserve = self.call("event", host, event="reserve", item_id=item or self.board.item, priority="P1")
        self.store.bind(reserve["lease_id"], "agent-A", expected_version=self.store.read()["version"],
                        agent_name="name-A")
        run = m.coordination(self.store).read()["runs"][reserve["lease_id"]]
        self.assertEqual(("reserved", None), (run["state"], run["agent_id"]))
        return reserve

    def _observe(self, rows):
        version = self.call("status", {})["forks"]["evidence_version"] or 0
        return self.call("event", {}, event="native_observation", complete=True,
                         expected_evidence_version=version, rows=rows)

    def _report_completion(self, reserve, item):
        with patch.object(m, "ledger", return_value=self.store):
            m.report({"lease_id": reserve["lease_id"], "worker_token": reserve["worker_token"],
                      "event": "completion", "plan_version": self.board.read()["version"],
                      "artifact": "a" * 64, "evidence": "tests pass",
                      "detail": {"summary": "done", "coverage": [{"item_id": item, "verified": True}],
                                 "verification": "tests pass"}}, self.board, self.config)

    def test_completion_report_repairs_split_before_receipt_terminalizes_run(self):
        reserve = self._split_lease_with_token()
        self._report_completion(reserve, self.board.item)
        run = m.coordination(self.store).read()["runs"][reserve["lease_id"]]
        # Never a terminal run with agent None while the ledger has an agent.
        self.assertEqual(("terminal", "agent-A", "completion"),
                         (run["state"], run["agent_id"], run["exit"]["kind"]))

    def test_exact_residual_repro_keeps_healthy_rows_flowing(self):
        # review-backend's ordering: split, worker completes, then the host
        # observes the completed agent next to a healthy agent-B fork.
        split = self._split_lease_with_token()
        self._report_completion(split, self.board.item)
        healthy = self.call("event", {"fork": True}, event="reserve", item_id=self.board.item2, priority="P1")
        self.call("event", {"host_id": "agent-B"}, event="bind", lease_id=healthy["lease_id"])
        result = self._observe([
            {"lease_id": split["lease_id"], "agent_id": "agent-A", "status": "completed"},
            {"lease_id": healthy["lease_id"], "agent_id": "agent-B", "status": "running"}])
        self.assertEqual([], result["skipped"])
        runs = m.coordination(self.store).read()["runs"]
        self.assertEqual("running", runs[healthy["lease_id"]]["native_status"])
        self.assertEqual(("agent-A", "completed"),
                         (runs[split["lease_id"]]["agent_id"], runs[split["lease_id"]]["native_status"]))

    def test_existing_exited_unbound_run_is_observed_terminal_without_raising(self):
        # A run already terminal with agent None (written by an older build)
        # can no longer be bound; it is reported as observed-terminal and the
        # healthy row in the same batch is still applied.
        split = self._split_lease_with_token()
        coord = m.coordination(self.store)
        coord.persist_receipt(split["lease_id"], "completion",
                              {"summary": "s", "coverage": [], "verification": "v"},
                              expected_version=coord.read()["version"])
        self.assertEqual((None, "terminal"), (coord.read()["runs"][split["lease_id"]]["agent_id"],
                                              coord.read()["runs"][split["lease_id"]]["state"]))
        healthy = self.call("event", {"fork": True}, event="reserve", item_id=self.board.item2, priority="P1")
        self.call("event", {"host_id": "agent-B"}, event="bind", lease_id=healthy["lease_id"])
        result = self._observe([
            {"lease_id": split["lease_id"], "agent_id": "agent-A", "status": "completed"},
            {"lease_id": healthy["lease_id"], "agent_id": "agent-B", "status": "running"}])
        self.assertEqual([split["lease_id"]], result["observed_terminal"])
        self.assertEqual([], result["skipped"])
        self.assertEqual("running", coord.read()["runs"][healthy["lease_id"]]["native_status"])
        # Repeated observations stay quiet: no diagnostic churn for this state.
        before = coord.read()["scheduler"]["diagnostics"]
        self._observe([{"lease_id": split["lease_id"], "agent_id": "agent-A", "status": "completed"}])
        self.assertEqual(before, coord.read()["scheduler"]["diagnostics"])

    def test_unrepairable_row_is_skipped_with_diagnostic_and_batch_commits(self):
        split = self._split_lease_with_token()
        path = self.tmp / "work-parallel-coordination.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["runs"][split["lease_id"]].update(agent_id="agent-Q", agent_name="agent-Q", state="bound",
                                              bound_at=1, native_status="pending", native_seen_at=1)
        path.write_text(json.dumps(doc), encoding="utf-8")
        healthy = self.call("event", {"fork": True}, event="reserve", item_id=self.board.item2, priority="P1")
        self.call("event", {"host_id": "agent-B"}, event="bind", lease_id=healthy["lease_id"])
        result = self._observe([
            {"lease_id": split["lease_id"], "agent_id": "agent-A", "status": "running"},
            {"lease_id": healthy["lease_id"], "agent_id": "agent-B", "status": "running"}])
        self.assertEqual([{"lease_id": split["lease_id"], "reason": "run is not bindable"}], result["skipped"])
        coord = m.coordination(self.store).read()
        self.assertEqual("running", coord["runs"][healthy["lease_id"]]["native_status"])
        self.assertEqual(("agent-Q", "pending"), (coord["runs"][split["lease_id"]]["agent_id"],
                                                  coord["runs"][split["lease_id"]]["native_status"]))
        self.assertEqual("native_uncertain", coord["scheduler"]["diagnostics"][-1]["code"])
        # Repeated identical failures collapse into one bounded diagnostic row.
        before_count = coord["scheduler"]["diagnostics"][-1]["count"]
        self._observe([
            {"lease_id": split["lease_id"], "agent_id": "agent-A", "status": "running"},
            {"lease_id": healthy["lease_id"], "agent_id": "agent-B", "status": "running"}])
        diagnostics = m.coordination(self.store).read()["scheduler"]["diagnostics"]
        self.assertEqual(1, len(diagnostics))
        self.assertEqual(before_count + 1, diagnostics[0]["count"])

    def test_mismatched_row_is_skipped_and_healthy_rows_commit(self):
        healthy = self.call("event", {"fork": True}, event="reserve", item_id=self.board.item, priority="P1")
        self.call("event", {"host_id": "agent-B"}, event="bind", lease_id=healthy["lease_id"])
        other = self.call("event", {"fork": True}, event="reserve", item_id=self.board.item2, priority="P1")
        self.call("event", {"host_id": "agent-C"}, event="bind", lease_id=other["lease_id"])
        result = self._observe([
            {"lease_id": other["lease_id"], "agent_id": "main", "status": "running"},
            {"lease_id": "not-a-lease", "agent_id": "agent-x", "status": "running"},
            {"lease_id": healthy["lease_id"], "agent_id": "agent-B", "status": "running"}])
        self.assertEqual({other["lease_id"], "not-a-lease"}, {row["lease_id"] for row in result["skipped"]})
        coord = m.coordination(self.store).read()
        self.assertEqual("running", coord["runs"][healthy["lease_id"]]["native_status"])
        # The mismatched agent was never recorded as a fork.
        records = m.native_evidence(self.store).read()["records"]
        self.assertNotIn(other["lease_id"], records)
        self.assertEqual("agent-B", records[healthy["lease_id"]]["agent_id"])

    def test_host_salvage_repairs_split_before_salvage_receipt(self):
        reserve = self._split_lease_with_token(workspace=True)
        salvaged = self.call("event", {"proof": "native terminal"}, event="host_salvage",
                             lease_id=reserve["lease_id"], reason="missing exit report")
        self.assertTrue(salvaged["salvaged"])
        run = m.coordination(self.store).read()["runs"][reserve["lease_id"]]
        self.assertEqual(("handed_off", "agent-A"), (run["state"], run["agent_id"]))

    def test_exhausted_coordination_retries_raise_mod_retryable_and_replay_repairs(self):
        from ciel_runtime_support.work_parallel_receipts import WorkCoordinationStore
        reserve = self.call("event", {"fork": True}, event="reserve", item_id=self.board.item, priority="P1")
        original = WorkCoordinationStore.bind_run

        def always_racing(self_, *args, **kwargs):
            self_.diagnostic("claude:other", "P2", "capacity", expected_version=self_.read()["version"])
            return original(self_, *args, **kwargs)
        with patch.object(WorkCoordinationStore, "bind_run", always_racing):
            # register.js parallelEvent retries on /state changed; reread/.
            with self.assertRaisesRegex(WorkParallelError, "state changed; reread"):
                self.call("event", {"host_id": "agent-A"}, event="bind", lease_id=reserve["lease_id"])
        self.assertEqual("agent-A", self.store.read()["leases"][reserve["lease_id"]]["host_id"])
        # The host retry takes the idempotent ledger-bound branch and completes it.
        self.call("event", {"host_id": "agent-A"}, event="bind", lease_id=reserve["lease_id"])
        run = m.coordination(self.store).read()["runs"][reserve["lease_id"]]
        self.assertEqual(("bound", "agent-A"), (run["state"], run["agent_id"]))

    def test_idempotent_bind_repairs_run_left_reserved(self):
        ident = self._ledger_bound_coordination_reserved()
        ledger_version = self.store.read()["version"]
        self.call("event", {"host_id": "agent-A"}, event="bind", lease_id=ident)
        run = m.coordination(self.store).read()["runs"][ident]
        self.assertEqual(("bound", "agent-A", "name-A"), (run["state"], run["agent_id"], run["agent_name"]))
        self.assertEqual(ledger_version, self.store.read()["version"])
        # Native observation for the repaired run now succeeds.
        version = self.call("status", {})["forks"]["evidence_version"] or 0
        self.call("event", {}, event="native_observation", complete=True, expected_evidence_version=version,
                  rows=[{"lease_id": ident, "agent_id": "agent-A", "status": "running"}])
        self.assertEqual("running", m.coordination(self.store).read()["runs"][ident]["native_status"])

    def test_idempotent_bind_refuses_run_bound_to_another_agent(self):
        ident = self._ledger_bound_coordination_reserved()
        path = self.tmp / "work-parallel-coordination.json"
        coord = json.loads(path.read_text(encoding="utf-8"))
        coord["runs"][ident].update(agent_id="agent-B", agent_name="agent-B", state="bound", bound_at=1,
                                    native_status="pending", native_seen_at=1)
        path.write_text(json.dumps(coord), encoding="utf-8")
        with self.assertRaisesRegex(WorkParallelError, "run is not bindable"):
            self.call("event", {"host_id": "agent-A"}, event="bind", lease_id=ident)
        self.assertEqual("agent-B", m.coordination(self.store).read()["runs"][ident]["agent_id"])

    def test_native_observation_repairs_lost_coordination_bind_after_restart(self):
        # After a CLI restart hydrate never rebinds a host-bound lease; the next
        # native observation of that same agent must complete the lost run bind
        # instead of failing the whole event with "unknown bound run".
        ident = self._ledger_bound_coordination_reserved()
        version = self.call("status", {})["forks"]["evidence_version"] or 0
        self.call("event", {}, event="native_observation", complete=True, expected_evidence_version=version,
                  rows=[{"lease_id": ident, "agent_id": "agent-A", "status": "running"}])
        run = m.coordination(self.store).read()["runs"][ident]
        self.assertEqual(("active", "agent-A", "running"), (run["state"], run["agent_id"], run["native_status"]))
        # An observation naming a different agent is refused by native evidence
        # and repairs nothing.
        version = self.call("status", {})["forks"]["evidence_version"]
        with self.assertRaises(WorkParallelError):
            self.call("event", {}, event="native_observation", complete=True, expected_evidence_version=version,
                      rows=[{"lease_id": ident, "agent_id": "agent-Z", "status": "running"}])
        self.assertEqual("agent-A", m.coordination(self.store).read()["runs"][ident]["agent_id"])

    def test_conflicting_coordination_run_fails_before_ledger_bind(self):
        reserve = self.call("event", {"fork": True}, event="reserve", item_id=self.board.item, priority="P1")
        path = self.tmp / "work-parallel-coordination.json"
        coord = json.loads(path.read_text(encoding="utf-8"))
        run = coord["runs"][reserve["lease_id"]]
        run.update(agent_id="someone-else", agent_name="someone-else", state="bound", bound_at=1,
                   native_status="pending", native_seen_at=1)
        path.write_text(json.dumps(coord), encoding="utf-8")
        before = self.store.read()
        with self.assertRaisesRegex(WorkParallelError, "run is not bindable"):
            self.call("event", {"host_id": "agent-new"}, event="bind", lease_id=reserve["lease_id"])
        after = self.store.read()
        self.assertEqual(before["version"], after["version"])
        lease = after["leases"][reserve["lease_id"]]
        self.assertEqual(("reserved", None), (lease["state"], lease["host_id"]))
        # An agent already bound to another live run is refused before the ledger too.
        other = self.call("event", {"fork": True}, event="reserve", item_id=self.board.item2, priority="P1")
        with self.assertRaisesRegex(WorkParallelError, "run is not bindable"):
            self.call("event", {"host_id": "someone-else"}, event="bind", lease_id=other["lease_id"])
        self.assertIsNone(self.store.read()["leases"][other["lease_id"]]["host_id"])

    # Compatibility -----------------------------------------------------------

    def test_finalization_file_is_strict_and_separate(self):
        reserve, workspace, artifact = self.completed()
        self.idle(reserve["lease_id"])
        final = WorkParallelFinalizationStore(self.tmp / "bad.json")
        final.path.write_text('{"schema":1,"version":0,"reviews":{},"extra":1}')
        with self.assertRaisesRegex(WorkParallelError, "corrupt"):
            final.read()
        legacy = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertTrue(set(legacy) <= {"schema", "version", "policy", "leases", "launches", "admission"})
        self.assertTrue((self.tmp / "work-parallel-finalization.json").exists())

    def test_old_readers_accept_ledger_after_approve_and_rework(self):
        modules = [legacy_module(commit) for commit in LEGACY_COMMITS]
        approved, _, _ = self.completed()
        self.idle(approved["lease_id"])
        self.review(approved["lease_id"], "approve", decision_id="approve")
        # Second item on the same approved goal takes the rework path.
        second = self.board.item2
        reserve = self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                            event="reserve", item_id=second, priority="P1")
        self.call("event", {"host_id": "worker-9"}, event="bind", lease_id=reserve["lease_id"])
        with patch.object(m, "ledger", return_value=self.store):
            m.report({"lease_id": reserve["lease_id"], "worker_token": reserve["worker_token"],
                      "event": "completion", "plan_version": self.board.read()["version"],
                      "artifact": "e" * 64, "evidence": "ok",
                      "detail": {"summary": "s", "coverage": [], "verification": "v"}}, self.board, self.config)
        self.idle(reserve["lease_id"])
        result = self.review(reserve["lease_id"], "rework", decision_id="rework")
        self.call("event", {"fork": True}, event="rework_dispatch", lease_id=result["dispatch"]["lease_id"])
        raw = json.loads(self.store.path.read_text(encoding="utf-8"))
        for module in modules:
            with self.subTest(module=module.__name__):
                module.validate(json.loads(json.dumps(raw)))
                read = module.WorkParallelStore(self.store.path).read()
                self.assertEqual(raw["version"], read["version"])
                self.assertEqual({"done", "submitted", "reserved"} & {r["state"] for r in read["leases"].values()},
                                 {"done", "reserved"})

    def test_legacy_reviewer_rows_stay_readable_in_status(self):
        doc = self.store.read()
        doc["leases"]["legacy-reviewer"] = {
            "id": "legacy-reviewer", "scope": SCOPE, "item_id": self.board.item, "priority": "P1",
            "created_at": 1, "role": "reviewer", "mode": "fork", "plan_version": 1, "state": "done",
            "host_id": "old", "slot": False, "usage": 0, "artifact": "b" * 64, "rework": 0,
            "evidence": "consumed", "idle_proof": "consumed", "cleanup_proof": "retained", "report_hash": None}
        self.store.path.write_text(json.dumps(doc), encoding="utf-8")
        status = self.call("status", {})
        self.assertEqual(1, status["lease_counts"]["done"])
        for module in (legacy_module(commit) for commit in LEGACY_COMMITS):
            module.validate(json.loads(self.store.path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
