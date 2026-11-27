"""Rework-worktree contracts; temp config only, no live runtime.

Item 1 records pre-bind content drift against the retained handoff baseline.
Its ``# DEFECT:`` markers still pin what is disclosed but not prevented: no
pre-bind write gate exists yet (item E). Item 2 converts an unavailable
retained worktree (including a native fork that ends without reporting) into
one explicit review row that main can recover in a fresh worktree without
inventing placeholder facts.
"""
import atexit
import functools
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support import work_parallel_workspace as workspace_module
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_http_controller import WorkHttpController
from ciel_runtime_support.work_parallel import WorkParallelError, WorkParallelStore
from ciel_runtime_support.work_parallel_finalization import WorkParallelFinalizationStore
from ciel_runtime_support.work_parallel_workspace import WorkspaceError
from ciel_runtime_support.work_plan_store import WorkPlanStore

SECRET = "rework-worktree-host-secret-" * 2
SCOPE = "claude:rework-worktree"
FIRST_PROCESS = "process-1"
LATER_PROCESS = "process-2"
BETA = "57dbfb3"
SOURCE_ROOT = Path(__file__).resolve().parents[1]


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@functools.lru_cache(maxsize=None)
def beta_module(name):
    """Load one module exactly as the installed beta runs it, without installing it."""
    try:
        source = subprocess.run(["git", "-C", str(SOURCE_ROOT), "show",
                                 BETA + ":ciel_runtime_support/" + name + ".py"],
                                check=True, capture_output=True, text=True, encoding="utf-8").stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise unittest.SkipTest("beta source unavailable: " + BETA) from error
    qualified = "ciel_runtime_support._beta_" + BETA + "_" + name
    module = types.ModuleType(qualified)
    module.__package__ = "ciel_runtime_support"
    exec(compile(source, qualified, "exec"), module.__dict__)
    return module


@functools.lru_cache(maxsize=None)
def beta_tree():
    """A private extract of the whole 57dbfb3 package, for a real beta process."""
    try:
        # Archive from the repository top: a pathspec is cwd-relative, and the
        # tests also run from copies nested inside the worktree (fail-before roots).
        top = subprocess.run(["git", "-C", str(SOURCE_ROOT), "rev-parse", "--show-toplevel"],
                             check=True, capture_output=True, text=True).stdout.strip()
        archive = subprocess.run(["git", "-C", top, "archive", "--format=tar", BETA,
                                  "ciel_runtime_support"], check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise unittest.SkipTest("beta source unavailable: " + BETA) from error
    root = Path(tempfile.mkdtemp(prefix="wp-beta-" + BETA + "-"))
    atexit.register(shutil.rmtree, root, True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as handle:
        handle.extractall(root, filter="data")
    return root


BETA_DRIVER = r"""
import json, sys
from pathlib import Path
from unittest.mock import patch
spec = json.loads(sys.argv[1])
sys.path.insert(0, spec["root"])
import ciel_runtime_support.runtime_paths as runtime_paths
from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.work_parallel import WorkParallelStore
from ciel_runtime_support.work_plan_store import WorkPlanStore
assert Path(m.__file__).resolve().is_relative_to(Path(spec["root"]).resolve()), m.__file__
out = []
with patch.object(runtime_paths, "CONFIG_DIR", Path(spec["config"])):
    store = WorkParallelStore(Path(spec["ledger"]))
    board = WorkPlanStore(Path(spec["board"]))
    for name, args in spec["calls"]:
        args = dict(args)
        args["_host"] = {"token": spec["token"], **args.pop("host", {})}
        if name == "work_parallel_item_review":
            args["expected_finalization_version"] = m.finalization(store).read()["version"]
        else:
            args.setdefault("expected_version", store.read()["version"])
        try:
            with patch.object(m, "ledger", return_value=store):
                out.append({"ok": json.loads(m.run(name, args, board, spec["scope"], spec["config_obj"], None))})
        except Exception as error:
            out.append({"error": type(error).__name__, "message": str(error)})
print(json.dumps(out))
"""


class Board:
    def __init__(self, path):
        self.store = WorkPlanStore(path)
        doc = self.store.read()
        doc, root = self.store.transaction(lambda d: tree.set_plan(d, {
            "title": "Goal", "note": "", "priority": "P1", "children": [
                {"title": "Item", "note": "", "ref": "", "waiting_on": "", "children": []},
            ]}, None), expected_version=doc["version"])
        self.root = root
        self.item = next(n["id"] for n in doc["nodes"] if n["parent_id"] == root)
        self.store.transaction(lambda d: tree.set_phase(d, "executing", root),
                               expected_version=doc["version"])

    def read(self):
        return self.store.read()

    def transaction(self, *args, **kwargs):
        return self.store.transaction(*args, **kwargs)


class ReworkFixture(unittest.TestCase):
    """One item, one reviewed result, one rework decision, in a real temp repo."""

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.repo = self.tmp / "repo"
        # Initialized before any other git command: an enclosing directory may
        # itself be a repository, and git would otherwise resolve to that one.
        git(self.tmp, "init", str(self.repo))
        git(self.repo, "config", "user.name", "fixture")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        (self.repo / "f").write_text("base\n")
        git(self.repo, "add", "f")
        git(self.repo, "commit", "-m", "base")
        self.board = Board(self.tmp / "work-plan.json")
        self.store = WorkParallelStore(self.tmp / "work-parallel.json")
        self.config = {"work_parallel_host": {"token": SECRET, "capability_ready": True}}
        runtime_patch = patch("ciel_runtime_support.runtime_paths.CONFIG_DIR", self.tmp / "config")
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)
        # The routed main session: a rework has a proven live owner (owner_offline fix).
        from work_owner_fixture import bind_router
        bind_router(self, SCOPE)
        # R2 B2: the suite's launch nonces are Work hosts of this test launcher
        # (one claude.exe at a time), so a later nonce may observe an earlier absent.
        from work_owner_fixture import attach_hosts
        attach_hosts(self.store, SCOPE, FIRST_PROCESS, LATER_PROCESS)

    # Backend calls -------------------------------------------------------------

    def call(self, name, host=None, **args):
        if name != "item_review":
            args.setdefault("expected_version", self.store.read()["version"])
        args["_host"] = {"token": SECRET, **(host or {})}
        with patch.object(m, "ledger", return_value=self.store):
            return json.loads(m.run("work_parallel_" + name, args, self.board, SCOPE,
                                    self.config, None))

    def report(self, lease_id, token, artifact, summary):
        """A worker's own completion report, authenticated only by its token."""
        with patch.object(m, "ledger", return_value=self.store):
            return json.loads(m.report({
                "lease_id": lease_id, "worker_token": token, "event": "completion",
                "plan_version": self.board.read()["version"], "artifact": artifact,
                "evidence": summary,
                "detail": {"summary": summary, "verification": summary,
                           "coverage": [{"item_id": self.board.item, "verified": True}]}},
                self.board, self.config))

    def idle(self, lease_id):
        return self.call("event", {"state": "idle", "proof": "native completed"},
                         event="reconcile", lease_id=lease_id)

    def review(self, lease_id, decision, decision_id, evidence="main verified"):
        version = self.call("status", {})["review"]["version"]
        return self.call("item_review", None, lease_id=lease_id, decision=decision,
                         decision_id=decision_id, evidence=evidence,
                         expected_finalization_version=version)

    # Durable state ---------------------------------------------------------------

    def reviews(self):
        return WorkParallelFinalizationStore(
            self.tmp / "work-parallel-finalization.json").read()["reviews"]

    def lease(self, lease_id):
        return self.store.read()["leases"][lease_id]

    def stranded_rows(self):
        """The stranded-v1 sidecar, which builds through 57dbfb3 never read."""
        from ciel_runtime_support.work_parallel_stranded import WorkParallelStrandedStore
        return WorkParallelStrandedStore(self.tmp / "work-parallel-stranded-v1.json").read()["rows"]

    def manifest(self, lease_id):
        """The raw ownership manifest, read without the Git verification."""
        folder = self.tmp / "config" / "work-parallel-workspaces"
        rows = [json.loads(path.read_text(encoding="utf-8")) for path in folder.glob("*.json")]
        return next(row for row in rows if row.get("lease_id") == lease_id)

    def durable_bytes(self):
        """Every durable file outside the Git repository, lock files excluded."""
        return {str(path.relative_to(self.tmp)): path.read_bytes()
                for path in sorted(self.tmp.rglob("*"))
                if path.is_file() and self.repo not in path.parents and path.suffix != ".lock"}

    def pending(self):
        """What main is shown: the review projection of work_parallel_status."""
        return self.call("status", {})["review"]["pending"]

    def beta_process(self, *calls):
        """Run ``m.run`` calls in a separate 57dbfb3 process on these exact files."""
        spec = {"root": str(beta_tree()), "config": str(self.tmp / "config"),
                "ledger": str(self.store.path), "board": str(self.tmp / "work-plan.json"),
                "scope": SCOPE, "token": SECRET, "config_obj": self.config,
                "calls": [[name, args] for name, args in calls]}
        done = subprocess.run([sys.executable, "-I", "-B", "-c", BETA_DRIVER, json.dumps(spec)],
                              capture_output=True, text=True, encoding="utf-8", timeout=300)
        self.assertEqual(0, done.returncode, done.stderr[-2000:])
        return json.loads(done.stdout.strip().splitlines()[-1])

    def assert_beta_reads_every_store(self, *workspace_leases):
        """State this build wrote must load in a 57dbfb3 process sharing the device.

        A beta process that cannot validate the finalization file reports it as
        corrupt and blocks every finalization write of its own.
        """
        final = self.tmp / "work-parallel-finalization.json"
        if final.exists():
            beta_module("work_parallel_finalization").WorkParallelFinalizationStore(final).read()
        beta_module("work_parallel").WorkParallelStore(self.store.path).read()
        coordination = self.tmp / "work-parallel-coordination.json"
        if coordination.exists():
            beta_module("work_parallel_receipts").WorkCoordinationStore(coordination).read()
        for lease_id in workspace_leases:
            beta_module("work_parallel_workspace").observe(self.repo, lease_id)

    # Scenario steps ---------------------------------------------------------------

    def submitted(self):
        """Generation 1 reports a wrong ``result.txt`` and waits for main's review."""
        reserve = self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                            event="reserve", item_id=self.board.item, priority="P1")
        first = reserve["lease_id"]
        self.call("event", {"host_id": "worker-1"}, event="bind", lease_id=first)
        worktree = Path(reserve["workspace"]["path"])
        (worktree / "result.txt").write_text("wrong result\n")
        self.report(first, reserve["worker_token"], "a" * 64, "first result")
        self.idle(first)
        return first, worktree

    def rework(self):
        """Main sends generation 1's result back; returns both leases and the worktree."""
        first, worktree = self.submitted()
        decided = self.review(first, "rework", "rework-1", evidence="fix result.txt")
        self.assertEqual("rework_ready", decided["state"])
        return first, decided["dispatch"]["lease_id"], worktree

    def launch(self, replacement, attempt, agent, nonce=FIRST_PROCESS, generation=2):
        """The host launch up to spawn_observed: the fork runs, the lease is unbound.

        This is the order register.js parallelDirectSpawn uses: capability, launch
        intent, native spawn, spawn observation. The ``bind`` event comes after.
        """
        token = self.call("event", {"fork": True}, event="rework_dispatch",
                          lease_id=replacement)["worker_token"]
        name = "work-rework-" + replacement + "-a" + str(attempt)
        self.call("event", {"internal": True, "launch_nonce": nonce}, event="rework_launch_intent",
                  lease_id=replacement, dispatch_name=name, generation=generation)
        self.call("event", {"internal": True}, event="rework_spawn_observed",
                  lease_id=replacement, dispatch_name=name, agent_id=agent)
        return token, name

    def bind(self, replacement, agent, name):
        return self.call("event", {"host_id": agent, "agent_name": name},
                         event="bind", lease_id=replacement)

    def absent_after_restart(self, replacement, name, agent):
        """A later host process proves the exact observed fork absent from its list."""
        return self.call("event", {"internal": True, "launch_nonce": LATER_PROCESS,
                                   "absent_dispatch_name": name, "absent_agent_id": agent},
                         event="rework_launch_absent", lease_id=replacement, dispatch_name=name)


class FixtureSmokeTests(ReworkFixture):
    def test_fixture_reaches_an_observed_unbound_rework_launch(self):
        first, replacement, worktree = self.rework()
        token, name = self.launch(replacement, 1, "agent-a1")
        self.assertEqual(64, len(token))
        self.assertEqual(("reserved", None, True), tuple(
            self.lease(replacement)[key] for key in ("state", "host_id", "slot")))
        self.assertEqual("spawn_observed", self.reviews()[first]["launch"]["state"])
        self.assertEqual(str(worktree), self.manifest(replacement)["path"])
        self.assertEqual("rebound", self.manifest(first)["status"])
        # The handoff stored a baseline of the reviewed worktree, and no host is bound.
        self.assertEqual(64, len(self.manifest(replacement)["handoff"]["receipt_digest"]))
        self.assertIsNone(self.manifest(replacement)["host_id"])


# Item 1 ---------------------------------------------------------------------------
# Measured in owned E2E run-20261112-044325: rework attempt a1 was running but
# not bound, edited the retained worktree, and its completion report was refused.
# After a restart, attempt a2 bound, made no edit and reported completion.

# What main is shown about the worktree when the fix was written by either author.
SAME_REVIEW_FACTS = {
    "dirty": True, "changed_count": 2, "changed_files": ["fix_note.txt", "result.txt"],
    "diff_stat": "", "launch": {"state": "none", "attempt": 0}, "blocked": None,
}


class UnboundReworkForkTests(ReworkFixture):
    def write_fix(self, worktree):
        """The edit a rework fork makes: change the reviewed file, add one file."""
        (worktree / "result.txt").write_text("fixed result\n")
        (worktree / "fix_note.txt").write_text("fixed\n")

    def baseline_matches(self, first, replacement):
        """Whether the worktree still equals the snapshot the handoff recorded."""
        record = self.manifest(replacement)
        current = workspace_module._receipt_digest(
            workspace_module._snapshot(record)[1], first, record["handoff"]["prior_generation"])
        return current == record["handoff"]["receipt_digest"]

    def review_facts(self, lease_id):
        row = next(row for row in self.pending() if row["lease_id"] == lease_id)
        return {**{key: row["workspace"][key] for key in
                   ("dirty", "changed_count", "changed_files", "diff_stat")},
                "launch": row["launch"], "blocked": row["blocked"]}

    def test_unbound_attempt_edit_is_reviewed_as_the_bound_attempts_result(self):
        first, replacement, worktree = self.rework()
        token_a1, name_a1 = self.launch(replacement, 1, "agent-a1")
        self.assertTrue(self.baseline_matches(first, replacement))
        before = self.durable_bytes()

        # Attempt 1 runs without a bind: it edits, then its report is refused.
        self.write_fix(worktree)
        with self.assertRaisesRegex(WorkParallelError, "worker lease is not active"):
            self.report(replacement, token_a1, "b" * 64, "attempt 1 fixed it")
        # DEFECT: no durable file records the change or the refused report.
        self.assertEqual(before, self.durable_bytes())
        self.assertFalse(self.baseline_matches(first, replacement))

        # Restart: a later host process proves a1 absent and launches attempt 2.
        self.absent_after_restart(replacement, name_a1, "agent-a1")
        token_a2, name_a2 = self.launch(replacement, 2, "agent-a2", nonce=LATER_PROCESS)
        # The capability is fenced: attempt 1's token is revoked by the rotation.
        with self.assertRaisesRegex(WorkParallelError, "invalid or revoked worker capability"):
            self.report(replacement, token_a1, "b" * 64, "attempt 1 again")
        # DEFECT: the bind is accepted although the worktree no longer equals the
        # handoff baseline, and it records nothing about the difference.
        self.bind(replacement, "agent-a2", name_a2)
        self.assertEqual(("running", "agent-a2"), (self.lease(replacement)["state"],
                                                   self.lease(replacement)["host_id"]))
        self.assertNotIn("changed", json.dumps(self.manifest(replacement)))

        # Attempt 2 makes no edit and reports completion.
        self.report(replacement, token_a2, "c" * 64, "already fixed; no edit made")
        self.idle(replacement)

        # DEFECT: the refused attempt's edits are in the snapshot main reviews.
        self.assertEqual(SAME_REVIEW_FACTS, self.review_facts(replacement))
        self.assertEqual("fixed result\n", (worktree / "result.txt").read_text())
        # DEFECT: main is shown one row, for attempt 2's lease, that names no
        # earlier attempt; the row with the launch audit is no longer projected.
        shown = self.pending()
        self.assertEqual([replacement], [row["lease_id"] for row in shown])
        for trace in (name_a1, "agent-a1", "absent", "unbound"):
            self.assertNotIn(trace, json.dumps(shown))
        # DEFECT: the only durable trace of attempt 1 says its name was absent
        # after a restart; it does not say that it ran or changed the worktree.
        audit = self.reviews()[first]["launch"]["audit"]
        self.assertEqual([("absent", name_a1, 1)],
                         [(row["kind"], row["dispatch_name"], row["attempt"]) for row in audit])
        self.assertNotIn("worktree", json.dumps(audit))

        # DEFECT: main approves attempt 2's artifact for content attempt 1 wrote.
        approved = self.review(replacement, "approve", "approve-2")
        self.assertEqual("finalized", approved["state"])
        self.assertEqual("c" * 64, self.reviews()[replacement]["artifact"])

    def test_control_bound_attempt_writing_the_same_fix_is_shown_identically(self):
        """The same fix written by a bound attempt gives main the same facts."""
        first, replacement, worktree = self.rework()
        token, name = self.launch(replacement, 1, "agent-a1")
        self.bind(replacement, "agent-a1", name)
        self.write_fix(worktree)
        self.report(replacement, token, "c" * 64, "fixed it")
        self.idle(replacement)
        # DEFECT (with the test above): main cannot tell the two cases apart.
        self.assertEqual(SAME_REVIEW_FACTS, self.review_facts(replacement))

    def test_content_only_change_is_invisible_to_the_recorded_baseline(self):
        """Bounds any fix: the handoff baseline is names and counts, not content."""
        first, replacement, worktree = self.rework()
        self.launch(replacement, 1, "agent-a1")
        (worktree / "result.txt").write_text("rewritten by an unbound attempt\n")
        # DEFECT: an unbound attempt rewrote the reviewed file and the stored
        # baseline still matches, so comparing it at bind would not see this.
        self.assertTrue(self.baseline_matches(first, replacement))
        (worktree / "fix_note.txt").write_text("a new file is visible\n")
        self.assertFalse(self.baseline_matches(first, replacement))

    def test_bind_records_content_drift_once_and_replay_never_overwrites_it(self):
        first, replacement, worktree = self.rework()
        token, name = self.launch(replacement, 1, "agent-a1")
        # Handoff baseline is unchanged at the first bind.
        self.bind(replacement, "agent-a1", name)
        launch = self.reviews()[first]["launch"]
        self.assertIn("handoff_drift=false", launch["proof"])
        original = launch["proof"]
        # Content changes later. An idempotent bind replay must preserve what was
        # measured at the actual bind, not rewrite attribution after the fact.
        (worktree / "result.txt").write_text("changed after bind\n")
        self.bind(replacement, "agent-a1", name)
        self.assertEqual(original, self.reviews()[first]["launch"]["proof"])
        self.report(replacement, token, "c" * 64, "done")
        # Frozen beta validators ignore the sidecars and accept every persisted store.
        self.assert_beta_reads_every_store(replacement)

    def test_bind_preflight_incomplete_baseline_writes_no_ledger_or_coordination_half(self):
        first, replacement, _ = self.rework()
        _, name = self.launch(replacement, 1, "agent-a1")
        paths = workspace_module.content_baseline_paths(self.repo, replacement)
        paths["sidecar"].unlink()
        before = self.durable_bytes()
        with self.assertRaisesRegex(WorkspaceError, "baseline incomplete"):
            self.bind(replacement, "agent-a1", name)
        self.assertEqual(before, self.durable_bytes())
        self.assertEqual(("reserved", None), (self.lease(replacement)["state"],
                                              self.lease(replacement)["host_id"]))
        self.assertIsNone(m.coordination(self.store).read()["runs"][replacement]["agent_id"])

    def test_change_between_bind_preflight_and_revalidation_records_true(self):
        first, replacement, worktree = self.rework()
        _, name = self.launch(replacement, 1, "agent-a1")
        real = workspace_module.handoff_drift
        calls = 0
        def changing(*args, **kwargs):
            nonlocal calls
            calls += 1
            answer = real(*args, **kwargs)
            if calls == 1:
                (worktree / "result.txt").write_text("changed in bind gap\n")
            return answer
        with patch.object(workspace_module, "handoff_drift", side_effect=changing):
            self.bind(replacement, "agent-a1", name)
        self.assertGreaterEqual(calls, 2)
        self.assertIn("handoff_drift=true", self.reviews()[first]["launch"]["proof"])

    # Bind-order crash convergence (D). No lock spans ledger, coordination,
    # finalization and workspace, so every boundary must converge without
    # ever recording a clean (false) result that was not measured clean.

    def drift_token(self, first):
        proof = self.reviews()[first]["launch"]["proof"] or ""
        return next((part.split("=", 1)[1] for part in proof.split("; ")
                     if part.startswith("handoff_drift=")), None)

    def test_postcommit_revalidation_error_records_unknown_and_diagnostic(self):
        first, replacement, _ = self.rework()
        _, name = self.launch(replacement, 1, "agent-a1")
        real = workspace_module.handoff_drift
        calls = 0
        def failing_after_commit(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise WorkspaceError("content baseline changed during capture")
            return real(*args, **kwargs)
        with patch.object(workspace_module, "handoff_drift", side_effect=failing_after_commit):
            self.bind(replacement, "agent-a1", name)
        self.assertEqual("bound", self.reviews()[first]["launch"]["state"])
        self.assertEqual("unknown", self.drift_token(first))
        diagnostics = m.coordination(self.store).read()["scheduler"]["diagnostics"]
        self.assertIn("native_uncertain", [row["code"] for row in diagnostics])
        self.assertEqual("agent-a1", self.manifest(replacement)["host_id"])
        before = self.durable_bytes()
        self.bind(replacement, "agent-a1", name)
        self.assertEqual(before, self.durable_bytes())

    def test_ledger_only_crash_converges_on_replay_without_a_false_token(self):
        first, replacement, _ = self.rework()
        _, name = self.launch(replacement, 1, "agent-a1")
        with patch.object(m, "_bind_coordination_run",
                          side_effect=m.WorkParallelConflict("Work coordination changed; reread")):
            with self.assertRaisesRegex(WorkParallelError, "changed; reread"):
                self.bind(replacement, "agent-a1", name)
        self.assertEqual(("running", "agent-a1"), (self.lease(replacement)["state"],
                                                   self.lease(replacement)["host_id"]))
        self.assertIsNone(m.coordination(self.store).read()["runs"][replacement]["agent_id"])
        self.bind(replacement, "agent-a1", name)
        self.assertEqual("agent-a1", m.coordination(self.store).read()["runs"][replacement]["agent_id"])
        self.assertEqual("bound", self.reviews()[first]["launch"]["state"])
        self.assertIn(self.drift_token(first), ("unknown", "true"))
        self.assertEqual("agent-a1", self.manifest(replacement)["host_id"])

    def test_coordination_bound_crash_converges_on_replay_without_a_false_token(self):
        first, replacement, _ = self.rework()
        _, name = self.launch(replacement, 1, "agent-a1")
        def lost(store, *args, **kwargs):
            raise m.WorkParallelConflict("Work finalization changed; reread")
        with patch.object(WorkParallelFinalizationStore, "launch_bound", lost):
            with self.assertRaisesRegex(WorkParallelError, "changed; reread"):
                self.bind(replacement, "agent-a1", name)
        self.assertEqual("agent-a1", m.coordination(self.store).read()["runs"][replacement]["agent_id"])
        self.assertEqual("spawn_observed", self.reviews()[first]["launch"]["state"])
        self.bind(replacement, "agent-a1", name)
        self.assertEqual("bound", self.reviews()[first]["launch"]["state"])
        self.assertIn(self.drift_token(first), ("unknown", "true"))

    def test_workspace_bind_failure_after_launch_bound_replay_repairs_and_keeps_proof(self):
        first, replacement, _ = self.rework()
        _, name = self.launch(replacement, 1, "agent-a1")
        with patch.object(workspace_module, "bind", side_effect=WorkspaceError("git rev-parse timed out")):
            with self.assertRaisesRegex(WorkspaceError, "timed out"):
                self.bind(replacement, "agent-a1", name)
        proof = self.reviews()[first]["launch"]["proof"]
        self.assertEqual("false", self.drift_token(first))
        self.assertIsNone(self.manifest(replacement)["host_id"])
        self.bind(replacement, "agent-a1", name)
        self.assertEqual("agent-a1", self.manifest(replacement)["host_id"])
        self.assertEqual(proof, self.reviews()[first]["launch"]["proof"])

    def test_old_build_handoff_without_baseline_binds_as_unknown(self):
        first, replacement, _ = self.rework()
        _, name = self.launch(replacement, 1, "agent-a1")
        paths = workspace_module.content_baseline_paths(self.repo, replacement)
        paths["intent"].unlink()
        paths["sidecar"].unlink()
        self.bind(replacement, "agent-a1", name)
        self.assertEqual("unknown", self.drift_token(first))
        self.assert_beta_reads_every_store(replacement)

    def test_entry_bound_lets_the_rework_proceed_with_unknown_drift(self):
        # Celly 2026-11-19: n1108's rework stayed "deciding" with "content
        # baseline entry bound exceeded: 20001/20000" on every pump; the
        # baseline only attributes drift, so it must not stop the rework.
        first, worktree = self.submitted()
        with patch.object(workspace_module, "MAX_CONTENT_ENTRIES", 0):
            decided = self.review(first, "rework", "rework-1", evidence="fix result.txt")
            self.assertEqual("rework_ready", decided["state"], decided)
            replacement = decided["dispatch"]["lease_id"]
            paths = workspace_module.content_baseline_paths(self.repo, replacement)
            self.assertIn("entry bound exceeded", paths["skipped"].read_text(encoding="utf-8"))
            self.assertNotIn(str(worktree), paths["skipped"].read_text(encoding="utf-8"))
            _, name = self.launch(replacement, 1, "agent-a1")
            self.bind(replacement, "agent-a1", name)
        self.assertEqual("unknown", self.drift_token(first))

    def test_late_bind_in_the_same_process_accepts_work_done_before_it(self):
        """No restart: the bind lands after the fork already edited and reported.

        Reachable without a test gate whenever parallelDirectSpawn's bind (or its
        spawn observation) throws: the host retries only at its next boundary.
        """
        first, replacement, worktree = self.rework()
        token, name = self.launch(replacement, 1, "agent-a1")
        self.write_fix(worktree)
        with self.assertRaisesRegex(WorkParallelError, "worker lease is not active"):
            self.report(replacement, token, "b" * 64, "fixed it")
        # The refusal changes nothing: the lease still waits for its bind.
        self.assertEqual(("reserved", None), (self.lease(replacement)["state"],
                                              self.lease(replacement)["host_id"]))
        self.assertEqual("spawn_observed", self.reviews()[first]["launch"]["state"])
        # The late bind is accepted but now records that content changed before it.
        self.bind(replacement, "agent-a1", name)
        self.assertFalse(self.baseline_matches(first, replacement))
        self.assertIn("handoff_drift=true", self.reviews()[first]["launch"]["proof"])
        # The same fork's repeated report is then accepted. The author is the
        # bound fork here, so this path attributes the change correctly.
        self.assertEqual("submitted", self.report(replacement, token, "b" * 64, "fixed it")["state"])
        self.idle(replacement)
        self.assertEqual(SAME_REVIEW_FACTS, self.review_facts(replacement))
        pending = next(row for row in self.pending() if row["lease_id"] == replacement)
        self.assertIs(pending["changed_before_bind"], True)
        public = WorkHttpController._public_reviews(self.call("status", {}))["parallel_reviews"]
        self.assertIs(next(row for row in public if row["lease_id"] == replacement)["changed_before_bind"],
                      True)

    def test_two_attempts_write_one_worktree_and_the_review_does_not_separate_them(self):
        first, replacement, worktree = self.rework()
        token_a1, name_a1 = self.launch(replacement, 1, "agent-a1")
        self.write_fix(worktree)
        self.absent_after_restart(replacement, name_a1, "agent-a1")
        token_a2, name_a2 = self.launch(replacement, 2, "agent-a2", nonce=LATER_PROCESS)
        # Both attempts were given the same directory.
        self.assertEqual(str(worktree), self.lease(replacement)["workspace"]["path"])
        self.bind(replacement, "agent-a2", name_a2)
        (worktree / "a2_note.txt").write_text("attempt 2\n")
        self.report(replacement, token_a2, "c" * 64, "attempt 2 added a note")
        self.idle(replacement)
        # DEFECT: one list, under attempt 2's lease, holds both attempts' files.
        self.assertEqual(["a2_note.txt", "fix_note.txt", "result.txt"],
                         self.review_facts(replacement)["changed_files"])


# Launch event races (fix C) ----------------------------------------------------------
# A host launch event reads the finalization version and then writes with it. A
# write by another process in between used to fail the event with "Work
# finalization changed; reread". register.js parallelEvent retries only
# "state changed; reread", so parallelDirectSpawn gave up after one attempt
# (spawn_observation_pending) and sent no bind while the fork was running.

class LaunchEventRaceTests(ReworkFixture):
    def other_process_writes_finalization(self):
        path = self.tmp / "work-parallel-finalization.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["version"] += 1
        path.write_text(json.dumps(state), encoding="utf-8")

    def racing(self, method, times=1):
        """Let another process's write land just before ``method`` writes, ``times`` times."""
        original = getattr(WorkParallelFinalizationStore, method)
        self.calls = 0

        def wrapper(store, *args, **kwargs):
            self.calls += 1
            if self.calls <= times:
                self.other_process_writes_finalization()
            return original(store, *args, **kwargs)
        return patch.object(WorkParallelFinalizationStore, method, wrapper)

    def host(self, event, lease_id, host=None, **args):
        return self.call("event", {"internal": True, **(host or {})}, event=event,
                         lease_id=lease_id, **args)

    def reach(self, stage):
        """Build one rework launch up to ``stage``; returns the ids and the launch name."""
        first, replacement, _ = self.rework()
        name = "work-rework-" + replacement + "-a1"
        self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        if stage == "dispatched":
            return first, replacement, name
        self.host("rework_launch_intent", replacement, {"launch_nonce": FIRST_PROCESS},
                  dispatch_name=name, generation=2)
        if stage == "intent":
            return first, replacement, name
        self.host("rework_spawn_observed", replacement, dispatch_name=name, agent_id="agent-a1")
        if stage == "observed":
            return first, replacement, name
        self.host("rework_launch_manual_request", first, {"user_confirmed": True},
                  launch_ref=self.reviews()[first]["launch"]["launch_ref"])
        return first, replacement, name

    def launch_ref(self, first):
        return self.reviews()[first]["launch"]["launch_ref"]

    def events(self):
        """(label, store method, stage before, the host event, launch state after)."""
        absent = {"launch_nonce": LATER_PROCESS, "absent_agent_id": "agent-a1"}
        return (
            ("intent", "launch_intent", "dispatched", lambda f, r, n: self.host(
                "rework_launch_intent", r, {"launch_nonce": FIRST_PROCESS},
                dispatch_name=n, generation=2), "launch_intent"),
            ("spawn observed", "spawn_observed", "intent", lambda f, r, n: self.host(
                "rework_spawn_observed", r, dispatch_name=n, agent_id="agent-a1"), "spawn_observed"),
            ("denied", "launch_denied", "intent", lambda f, r, n: self.host(
                "rework_launch_denied", r, {"proof": "native spawn denied"}, dispatch_name=n),
             "spawn_denied"),
            ("absent", "launch_absent", "observed", lambda f, r, n: self.host(
                "rework_launch_absent", r, {**absent, "absent_dispatch_name": n}, dispatch_name=n),
             "spawn_denied"),
            ("manual request", "launch_manual_request", "observed", lambda f, r, n: self.host(
                "rework_launch_manual_request", f, {"user_confirmed": True},
                launch_ref=self.launch_ref(f)), "manual_requested"),
            ("manual resolve", "launch_manual", "requested", lambda f, r, n: self.host(
                "rework_launch_manual_resolve", r, {**absent, "absent_dispatch_name": n},
                dispatch_name=n, launch_ref=self.launch_ref(f)), "spawn_denied"),
            ("present", "launch_present", "requested", lambda f, r, n: self.host(
                "rework_launch_present", r, {"present_dispatch_name": n, "present_agent_id": "agent-a1"},
                dispatch_name=n, launch_ref=self.launch_ref(f)), "spawn_observed"),
        )

    def test_every_launch_event_survives_one_lost_version_race(self):
        for label, method, stage, send, after in self.events():
            with self.subTest(label):
                self.setUp()
                first, replacement, name = self.reach(stage)
                with self.racing(method):
                    result = send(first, replacement, name)
                self.assertEqual(2, self.calls)
                self.assertEqual(after, result["state"])
                self.assertEqual(after, self.reviews()[first]["launch"]["state"])
                self.assert_beta_reads_every_store(replacement)

    def test_review_wake_events_survive_one_lost_version_race(self):
        first, _ = self.submitted()
        with self.racing("wake_prepare"):
            prepared = self.host("review_wake", first, operation="prepare")
        self.assertEqual((2, "submitting"), (self.calls, prepared["state"]))
        with self.racing("wake_record"):
            recorded = self.host("review_wake", first, operation="record",
                                 attempt_id=prepared["attempt_id"], state="appended")
        self.assertEqual((2, "appended"), (self.calls, recorded["state"]))
        self.assert_beta_reads_every_store(first)

    def test_persistent_race_ends_with_the_wording_the_host_retries(self):
        first, replacement, name = self.reach("intent")
        with self.racing("spawn_observed", times=1000):
            with self.assertRaisesRegex(WorkParallelError, "state changed; reread"):
                self.host("rework_spawn_observed", replacement, dispatch_name=name, agent_id="agent-a1")
        self.assertEqual(m.COORDINATION_BIND_ATTEMPTS, self.calls)
        self.assertEqual("launch_intent", self.reviews()[first]["launch"]["state"])

    def test_a_refusal_that_is_not_a_version_race_is_not_retried(self):
        first, replacement, name = self.reach("intent")
        with self.racing("spawn_observed", times=0):
            with self.assertRaisesRegex(WorkParallelError, "stale rework spawn observation"):
                self.host("rework_spawn_observed", replacement, dispatch_name="another-name",
                          agent_id="agent-a1")
        self.assertEqual(1, self.calls)


# Idle observation settles the launch (fix B follow-up) --------------------------------
# A bind whose launch_bound half was lost leaves the ledger bound and the launch
# record unresolved. Fix B lets the idle observation publish the review anyway;
# the predecessor row is then hidden and the mod's bind resend no longer sees it.
# So the idle observation completes the launch half too, as a bind replay would.

class IdleSettlesLaunchTests(ReworkFixture):
    def lose_launch_bound(self):
        """Every launch_bound write loses its version race while this is active."""
        def lost(store, *args, **kwargs):
            raise m.WorkParallelConflict("Work finalization changed; reread")
        return patch.object(WorkParallelFinalizationStore, "launch_bound", lost)

    def ledger_bound_launch_unresolved(self, source):
        """The bind commits its ledger half; the launch stays ``source``."""
        first, replacement, _ = self.rework()
        token = self.call("event", {"fork": True}, event="rework_dispatch",
                          lease_id=replacement)["worker_token"]
        name = "work-rework-" + replacement + "-a1"
        internal = {"internal": True}
        self.call("event", {**internal, "launch_nonce": FIRST_PROCESS}, event="rework_launch_intent",
                  lease_id=replacement, dispatch_name=name, generation=2)
        if source != "launch_intent":
            self.call("event", internal, event="rework_spawn_observed", lease_id=replacement,
                      dispatch_name=name, agent_id="agent-a1")
        with self.lose_launch_bound():
            with self.assertRaisesRegex(WorkParallelError, "state changed; reread"):
                self.bind(replacement, "agent-a1", name)
        if source == "manual_requested":
            self.call("event", {**internal, "user_confirmed": True}, event="rework_launch_manual_request",
                      lease_id=first, launch_ref=self.reviews()[first]["launch"]["launch_ref"])
        self.assertEqual(("running", "agent-a1"), (self.lease(replacement)["state"],
                                                   self.lease(replacement)["host_id"]))
        self.assertEqual(source, self.reviews()[first]["launch"]["state"])
        return first, replacement, token, name

    def test_idle_observation_binds_an_unresolved_launch_before_the_review_publishes(self):
        for source in ("launch_intent", "spawn_observed", "manual_requested"):
            with self.subTest(source):
                self.setUp()
                first, replacement, token, name = self.ledger_bound_launch_unresolved(source)
                self.report(replacement, token, "c" * 64, "done")
                self.idle(replacement)
                launch = self.reviews()[first]["launch"]
                self.assertEqual(("bound", "agent-a1", None, None),
                                 (launch["state"], launch["agent_id"], launch["launch_ref"],
                                  launch["manual_from"]))
                # Main sees one row, and the host has no launch left to recover.
                self.assertEqual([replacement], [row["lease_id"] for row in self.pending()])
                self.assertEqual([], self.call("event", {"internal": True, "launch_nonce": FIRST_PROCESS},
                                               event="rework_launch_status")["launches"])
                # A later bind replay changes nothing.
                before = self.durable_bytes()
                self.bind(replacement, "agent-a1", name)
                self.assertEqual(before, self.durable_bytes())
                self.assert_beta_reads_every_store(replacement)

    def test_bind_replay_after_the_idle_observation_does_not_undo_it(self):
        """An identical bind retry used to write the workspace back to not idle.

        The ledger stayed idle, so the two proofs diverged and main's decision was
        refused until another idle observation repaired the workspace.
        """
        first, replacement, _ = self.rework()
        token, name = self.launch(replacement, 1, "agent-a1")
        self.bind(replacement, "agent-a1", name)
        self.report(replacement, token, "c" * 64, "done")
        self.idle(replacement)
        before = self.durable_bytes()
        self.bind(replacement, "agent-a1", name)
        record = self.manifest(replacement)
        self.assertEqual((True, "native completed"), (record["idle"], record["idle_proof"]))
        self.assertEqual(before, self.durable_bytes())
        self.assertEqual("finalized", self.review(replacement, "approve", "approve-2")["state"])

    def test_launch_that_does_not_match_the_ledger_agent_never_blocks_the_review(self):
        """Not reachable through the product (see the next test); the state is forced."""
        first, replacement, token, name = self.ledger_bound_launch_unresolved("spawn_observed")
        path = self.tmp / "work-parallel-finalization.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["reviews"][first]["launch"]["agent_id"] = "agent-other"
        path.write_text(json.dumps(state), encoding="utf-8")
        self.report(replacement, token, "c" * 64, "done")
        self.idle(replacement)
        # The completed result is reviewable; the launch record is left as it was.
        self.assertEqual([replacement], [row["lease_id"] for row in self.pending()])
        launch = self.reviews()[first]["launch"]
        self.assertEqual(("spawn_observed", "agent-other"), (launch["state"], launch["agent_id"]))
        diagnostics = m.coordination(self.store).read()["scheduler"]["diagnostics"]
        self.assertEqual([(SCOPE, "P1", "native_uncertain", 1)],
                         [(row["scope"], row["priority"], row["code"], row["count"])
                          for row in diagnostics])
        self.assert_beta_reads_every_store(replacement)

    def test_fresh_bind_refuses_an_agent_the_launch_did_not_observe(self):
        """Why the mismatch above cannot arise: the ledger never binds such an agent."""
        first, replacement, _ = self.rework()
        token, name = self.launch(replacement, 1, "agent-a1")
        before = self.durable_bytes()
        for agent, agent_name in (("agent-other", name), ("agent-other", "another-name")):
            with self.assertRaisesRegex(WorkParallelError, "rework launch intent was not observed"):
                self.bind(replacement, agent, agent_name)
        self.assertEqual(before, self.durable_bytes())
        self.assertEqual(("reserved", None), (self.lease(replacement)["state"],
                                              self.lease(replacement)["host_id"]))

    def test_first_generation_idle_observation_touches_no_launch(self):
        first, _ = self.submitted()
        row = self.reviews()[first]
        self.assertEqual(("pending", "none", 0, []), (row["state"], row["launch"]["state"],
                                                      row["launch"]["attempt"], row["launch"]["audit"]))
        self.assert_beta_reads_every_store(first)


# Fork attribution (item F) -------------------------------------------------------------
# A hot-reloaded mod can meet a native fork whose rework lease has left its status
# read. This host-only, read-only query says which launch such a fork name belongs
# to, so a fork of a resolved attempt can be stopped and nothing else is.

class ForkAttributionTests(ReworkFixture):
    def attribution(self, names, scope=SCOPE, host=None):
        args = {"event": "rework_fork_attribution", "names": names,
                "_host": {"token": SECRET, **({"internal": True} if host is None else host)}}
        with patch.object(m, "ledger", return_value=self.store):
            return json.loads(m.run("work_parallel_event", args, self.board, scope, self.config, None))

    def status(self, name, **kwargs):
        (row,) = self.attribution([name], **kwargs)["forks"]
        self.assertEqual(name, row["name"])
        return row["status"], row.get("reason")

    def approve_replacement(self, replacement, token):
        self.report(replacement, token, "c" * 64, "done")
        self.idle(replacement)
        self.assertEqual("finalized", self.review(replacement, "approve", "approve-2")["state"])

    def test_current_while_the_launch_is_unresolved_or_bound_and_the_lease_is_open(self):
        first, replacement, _ = self.rework()
        token, name = self.launch(replacement, 1, "agent-a1")
        # Observed and not yet bound: the fork that must never be stopped.
        self.assertEqual(("current", None), self.status(name))
        self.bind(replacement, "agent-a1", name)
        self.assertEqual(("current", None), self.status(name))
        self.report(replacement, token, "c" * 64, "done")
        self.idle(replacement)
        self.assertEqual(("current", None), self.status(name))
        row = self.attribution([name])["forks"][0]
        self.assertEqual((replacement, 1), (row["lease_id"], row["attempt"]))

    def test_stale_for_a_resolved_attempt_and_for_an_ended_lease(self):
        first, replacement, _ = self.rework()
        _, name_a1 = self.launch(replacement, 1, "agent-a1")
        self.absent_after_restart(replacement, name_a1, "agent-a1")
        # Resolved, and no newer attempt recorded yet.
        self.assertEqual(("stale", "attempt_resolved"), self.status(name_a1))
        token_a2, name_a2 = self.launch(replacement, 2, "agent-a2", nonce=LATER_PROCESS)
        self.assertEqual(("stale", "earlier_attempt"), self.status(name_a1))
        self.assertEqual(("current", None), self.status(name_a2))
        self.bind(replacement, "agent-a2", name_a2)
        self.approve_replacement(replacement, token_a2)
        # The replacement's review is finalized and its lease is done. The
        # predecessor's row still holds the launch, so the answer is still exact.
        self.assertEqual("done", self.lease(replacement)["state"])
        self.assertEqual(("stale", "lease_ended"), self.status(name_a2))
        self.assertEqual(("stale", "earlier_attempt"), self.status(name_a1))

    def assert_terminal_lease_is_stale(self, terminal):
        first, replacement, _ = self.rework()
        _, name = self.launch(replacement, 1, "agent-" + terminal)
        self.bind(replacement, "agent-" + terminal, name)
        state = self.store.read()
        if terminal == "unreported":
            self.store.terminate_unreported(
                replacement, proof="positive terminal observation",
                expected_version=state["version"], now=100)
        else:
            # The legacy no-owner release needs a truly ownerless lease. This
            # build reserves a rework only for a proven live owner (owner_offline
            # fix; bind_router in setUp), so the replacement carries one. A
            # replacement 57dbfb3 reserved without a router scope has owner None
            # (its _owner_from_host); model that lease before the legacy release.
            self.assertIsNotNone(state["leases"][replacement].get("owner"))
            state, _ = self.store._change(lambda d: d["leases"][replacement].update(owner=None),
                                          state["version"])
            self.assertIsNone(state["leases"][replacement]["owner"])
            self.store.orphan_release(
                replacement, proof="operator verified shutdown",
                observation={"owner_state": "legacy_no_owner", "host_dead": True,
                             "scope_board_missing": True, "pid_absent": True},
                expected_version=state["version"], now=100)
        lease = self.lease(replacement)
        self.assertEqual((terminal, False, None),
                         (lease["state"], lease["slot"], lease["report_hash"]))
        self.assertIsNotNone(lease["idle_proof"])
        self.assertIsNotNone(lease["terminal"])
        self.assertEqual(("stale", "lease_terminal"), self.status(name))

    def test_stale_for_terminal_unreported_lease(self):
        self.assert_terminal_lease_is_stale("unreported")

    def test_stale_for_terminal_orphaned_lease(self):
        self.assert_terminal_lease_is_stale("orphaned")

    def test_earlier_attempt_is_recognized_without_its_audit_row(self):
        """The audit list keeps 20 rows. The lease id and attempt in the name suffice."""
        first, replacement, _ = self.rework()
        _, name_a1 = self.launch(replacement, 1, "agent-a1")
        self.absent_after_restart(replacement, name_a1, "agent-a1")
        _, name_a2 = self.launch(replacement, 2, "agent-a2", nonce=LATER_PROCESS)
        self.bind(replacement, "agent-a2", name_a2)
        path = self.tmp / "work-parallel-finalization.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["reviews"][first]["launch"]["audit"] = []
        path.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(("stale", "earlier_attempt"), self.status(name_a1))
        self.assertEqual(("current", None), self.status(name_a2))

    def test_unknown_without_evidence(self):
        first, replacement, _ = self.rework()
        _, name = self.launch(replacement, 1, "agent-a1")
        prefix = "work-rework-" + replacement + "-a"
        for label, other in (("free text", "some-other-fork"),
                             ("no such lease", "work-rework-" + "0" * 32 + "-a1"),
                             ("attempt never recorded", prefix + "2"),
                             ("reviewed lease, not a replacement", "work-rework-" + first + "-a1")):
            with self.subTest(label):
                self.assertEqual(("unknown", None), self.status(other))
        # Another conversation asking about this conversation's fork learns nothing.
        self.assertEqual(("unknown", None), self.status(name, scope="claude:another-conversation"))
        # Once the row is gone there is no evidence left.
        path = self.tmp / "work-parallel-finalization.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["reviews"] = {}
        path.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(("unknown", None), self.status(name))

    def test_answers_every_name_in_order_and_writes_nothing(self):
        first, replacement, _ = self.rework()
        _, name_a1 = self.launch(replacement, 1, "agent-a1")
        self.absent_after_restart(replacement, name_a1, "agent-a1")
        _, name_a2 = self.launch(replacement, 2, "agent-a2", nonce=LATER_PROCESS)
        before = self.durable_bytes()
        answer = self.attribution([name_a2, "stranger", name_a1, name_a2])
        self.assertEqual([(name_a2, "current"), ("stranger", "unknown"), (name_a1, "stale"),
                          (name_a2, "current")],
                         [(row["name"], row["status"]) for row in answer["forks"]])
        self.assertEqual([], self.attribution([])["forks"])
        self.assertEqual(before, self.durable_bytes())

    def test_only_the_mod_internal_host_may_ask(self):
        first, replacement, _ = self.rework()
        _, name = self.launch(replacement, 1, "agent-a1")
        # What the mod forwards for a main-model call, and for a fork's call.
        for host in ({"agent_id": None}, {"agent_id": "agent-a1", "internal": True},
                     {"internal": False}, {}):
            with self.subTest(host=host):
                with self.assertRaisesRegex(WorkParallelError, "verified mod-internal launch host required"):
                    self.attribution([name], host=host)
        with self.assertRaisesRegex(WorkParallelError, "authenticated parallel host required"):
            with patch.object(m, "ledger", return_value=self.store):
                m.run("work_parallel_event", {"event": "rework_fork_attribution", "names": [name]},
                      self.board, SCOPE, self.config, None)

    def test_names_are_bounded(self):
        for label, names in (("too many", ["n" + str(index) for index in range(65)]),
                             ("too long", ["n" * 201]), ("empty name", [""]),
                             ("not text", [7]), ("not a list", "work-rework-x-a1"), ("missing", None)):
            with self.subTest(label):
                with self.assertRaisesRegex(WorkParallelError, "fork names"):
                    self.attribution(names)
        self.assertEqual(64, len(self.attribution(["n" + str(index) for index in range(64)])["forks"]))


class ForkIdentityTests(ReworkFixture):
    """Host-only lookup for the SendMessage revival gate: every Work fork, any state."""

    def identity(self, ids=(), names=(), scope=SCOPE, host=None):
        args = {"event": "fork_identity", "ids": list(ids), "names": list(names),
                "_host": {"token": SECRET, **({"internal": True} if host is None else host)}}
        with patch.object(m, "ledger", return_value=self.store):
            return json.loads(m.run("work_parallel_event", args, self.board, scope, self.config, None))

    def matches(self, **kwargs):
        return sorted((row["query"], row["by"], row["owner"], row["lease_id"], row["lease_state"],
                       row["active"], row["current"]) for row in self.identity(**kwargs)["matches"])

    def test_active_bound_fork_is_current_by_id_and_name(self):
        first, replacement, _ = self.rework()
        _, name = self.launch(replacement, 1, "agent-a1")
        self.bind(replacement, "agent-a1", name)
        self.assertEqual([
            ("agent-a1", "id", "launch", replacement, "running", False, True),
            ("agent-a1", "id", "lease", replacement, "running", True, True),
            (name, "name", "launch", replacement, "running", False, True),
            (name, "name", "lease", replacement, "running", True, True),
        ], self.matches(ids=["agent-a1"], names=[name]))

    def test_terminal_and_done_leases_still_match_and_are_never_active(self):
        reserve = self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                            event="reserve", item_id=self.board.item, priority="P1")
        lease = reserve["lease_id"]
        self.call("event", {"host_id": "agent-gone", "agent_name": "work-gone"}, event="bind", lease_id=lease)
        self.store.terminate_unreported(lease, proof="positive terminal observation",
                                        expected_version=self.store.read()["version"])
        self.assertEqual([
            ("agent-gone", "id", "lease", lease, "unreported", False, True),
            ("work-gone", "name", "lease", lease, "unreported", False, True),
        ], self.matches(ids=["agent-gone"], names=["work-gone"]))
        # A finalized first generation (done) is matched as well.
        first, worktree = self.submitted()
        self.assertEqual("finalized", self.review(first, "approve", "approve-1")["state"])
        self.assertEqual([("worker-1", "id", "lease", first, "done", False, True)],
                         self.matches(ids=["worker-1"]))

    def test_earlier_attempt_names_match_only_through_the_launch_audit(self):
        first, replacement, _ = self.rework()
        _, name_a1 = self.launch(replacement, 1, "agent-a1")
        self.absent_after_restart(replacement, name_a1, "agent-a1")
        _, name_a2 = self.launch(replacement, 2, "agent-a2", nonce=LATER_PROCESS)
        self.assertEqual([(name_a1, "name", "launch", replacement, "reserved", False, False)],
                         self.matches(names=[name_a1]))
        # The earlier attempt's agent id is not durable once the launch moved on.
        self.assertEqual([], self.matches(ids=["agent-a1"]))
        self.assertEqual([("agent-a2", "id", "launch", replacement, "reserved", False, True)],
                         self.matches(ids=["agent-a2"]))

    def test_unmatched_and_other_scope_queries_are_omitted(self):
        first, replacement, _ = self.rework()
        _, name = self.launch(replacement, 1, "agent-a1")
        self.bind(replacement, "agent-a1", name)
        self.assertEqual([], self.matches(ids=["agent-a1"], names=[name], scope="claude:other-conversation"))
        self.assertEqual([], self.matches(ids=["never-seen"], names=["work-never-seen"]))

    def test_reply_carries_no_secret_or_host_detail_and_writes_nothing(self):
        first, replacement, _ = self.rework()
        _, name = self.launch(replacement, 1, "agent-a1")
        self.bind(replacement, "agent-a1", name)
        before = self.durable_bytes()
        reply = self.identity(ids=["agent-a1"], names=[name])
        self.assertEqual(before, self.durable_bytes())
        self.assertEqual({"matches", "ledger_version", "finalization_version"}, set(reply))
        allowed = {"query", "by", "owner", "lease_id", "lease_state", "active", "current"}
        self.assertTrue(all(set(row) == allowed for row in reply["matches"]))
        encoded = json.dumps(reply)
        for secret in (SECRET, FIRST_PROCESS, str(self.repo)):
            self.assertNotIn(secret, encoded)

    def test_only_the_mod_internal_host_and_bounded_queries_are_accepted(self):
        for host in ({}, {"internal": False}, {"agent_id": "worker"}):
            with self.assertRaisesRegex(WorkParallelError, "verified mod-internal launch host required"):
                self.identity(ids=["x"], host=host)
        for ids, names in (([], []), (["x"] * 65, []), ([], ["n"] * 65), ([""], []),
                           (["x" * 201], []), ([1], []), ("x", ["n"]), (None, None)):
            with self.subTest(ids=ids, names=names):
                args = {"event": "fork_identity", "ids": ids, "names": names,
                        "_host": {"token": SECRET, "internal": True}}
                with patch.object(m, "ledger", return_value=self.store):
                    with self.assertRaisesRegex(WorkParallelError, "bounded fork ids/names required"):
                        m.run("work_parallel_event", args, self.board, SCOPE, self.config, None)

    def test_unreadable_store_is_an_error_not_an_empty_answer(self):
        first, replacement, _ = self.rework()
        (self.tmp / "work-parallel-finalization.json").write_text("{corrupt", encoding="utf-8")
        with self.assertRaisesRegex(WorkParallelError, "corrupt Work finalization state"):
            self.identity(ids=["agent-a1"])

    def test_event_schema_admits_ids(self):
        props = next(tool for tool in m.schemas() if tool["name"] == "work_parallel_event")["inputSchema"]["properties"]
        self.assertEqual({"type": "array", "maxItems": 64, "items": {"type": "string", "maxLength": 200}},
                         props["ids"])


# Item 2 ---------------------------------------------------------------------------
# Never measured before these tests. The retained worktree breaks after the
# rework decision (rework_ready) and before the replacement's bind event.

def delete_worktree(case, worktree, replacement):
    shutil.rmtree(worktree)


def switch_branch(case, worktree, replacement):
    git(worktree, "checkout", "-b", "other")


def repoint_registration(case, worktree, replacement):
    """The worktree stays a valid checkout, but the repository lists it elsewhere."""
    admin = Path(case.manifest(replacement)["git_dir"])
    (admin / "gitdir").write_text(str(case.tmp / "elsewhere" / ".git").replace("\\", "/") + "\n")


class BrokenRetainedWorktreeTests(ReworkFixture):
    def strand(self, breakage, message):
        first, replacement, worktree = self.rework()
        token, name = self.launch(replacement, 1, "agent-a1")
        breakage(self, worktree, replacement)

        # Ledger/coordination/launch commit even when the workspace half cannot:
        # the later idle observation converges that split bind into one row.
        with self.assertRaisesRegex(WorkspaceError, message):
            self.bind(replacement, "agent-a1", name)
        lease = self.lease(replacement)
        self.assertEqual(("running", "agent-a1", True),
                         (lease["state"], lease["host_id"], lease["slot"]))
        self.assertEqual("agent-a1", m.coordination(self.store).read()["runs"][replacement]["agent_id"])
        self.assertEqual("bound", self.reviews()[first]["launch"]["state"])
        self.assertIsNone(self.manifest(replacement)["host_id"])
        self.assertEqual([], self.call("event", {"internal": True, "launch_nonce": FIRST_PROCESS},
                                               event="rework_launch_status")["launches"])
        with self.assertRaisesRegex(WorkspaceError, message):
            self.bind(replacement, "agent-a1", name)
        return first, replacement, token

    def assert_stranded_before_review(self, first, replacement, token, message):
        self.assertEqual("submitted", self.report(replacement, token, "c" * 64, "done")["state"])
        observed = self.idle(replacement)
        lease = self.lease(replacement)
        self.assertEqual(("submitted", False, "native completed"),
                         (lease["state"], lease["slot"], lease["idle_proof"]))
        self.assertFalse(self.manifest(replacement)["idle"])

        # Exactly one main-decidable row replaces the predecessor projection. It
        # lives in the stranded-v1 sidecar only: schema-5 finalization never
        # holds an undecidable row a 57dbfb3 process would present or gate on.
        self.assertNotIn(replacement, self.reviews())
        self.assertEqual("pending", self.stranded_rows()[replacement]["state"])
        self.assertEqual("rework_ready", self.reviews()[first]["state"])
        shown = observed["review"]["pending"]
        self.assertEqual([replacement], [row["lease_id"] for row in shown])
        row = shown[0]
        self.assertEqual("workspace_unavailable", row["stranded"]["kind"])
        self.assertEqual("workspace_unavailable", row["blocked"]["code"])
        self.assertEqual({"path", "branch", "head", "unavailable", "reason"}, set(row["workspace"]))
        self.assertTrue(row["workspace"]["unavailable"])
        for invented in ("dirty", "changed_count", "changed_files", "diff_stat"):
            self.assertNotIn(invented, row["workspace"])
        self.assertIn(message, row["workspace"]["reason"])
        self.assertEqual("submitted", observed["leases"][replacement]["state"])
        self.assertNotIn(first, [value["lease_id"] for value in shown])

        web = WorkHttpController._public_reviews(observed)["parallel_reviews"]
        self.assertEqual([replacement], [value["lease_id"] for value in web])
        self.assertEqual({"code": "workspace_unavailable"}, web[0]["blocked"])
        # Browser projection keeps only the recorded identity: no path, no facts.
        self.assertEqual({"branch", "head"}, set(web[0]["workspace"]))
        self.assert_beta_reads_every_store()

        before = self.durable_bytes()
        with self.assertRaisesRegex(WorkParallelError,
                                    "workspace unavailable: .*approve refused; rework creates a fresh workspace"):
            self.review(replacement, "approve", "approve-2")
        self.assertEqual(before, self.durable_bytes())
        return row

    def recover_stranded(self, replacement, *, decision_id="rework-2", kind="workspace_unavailable"):
        prior_path = self.stranded_rows()[replacement]["binding"]["path"]
        result = self.review(replacement, "rework", decision_id, evidence="recover in a fresh workspace")
        self.assertEqual("rework_ready", result["state"])
        recovery = result["dispatch"]["lease_id"]
        self.assertEqual(kind, result["dispatch"]["recovery"])
        self.assertNotEqual(prior_path, result["dispatch"]["workspace"])
        self.assertEqual(result["dispatch"]["workspace"], self.lease(recovery)["workspace"]["path"])
        self.assertEqual(self.lease(replacement)["rework"], self.lease(recovery)["rework"])
        side = self.stranded_rows()[replacement]
        self.assertEqual(("recovered", recovery), (side["state"], side["replacement_lease_id"]))
        return recovery, result

    def test_deleted_worktree_strands_the_replacement_before_review(self):
        message = "owned path unavailable; retained"
        first, replacement, token = self.strand(delete_worktree, message)
        self.assert_stranded_before_review(first, replacement, token, message)
        recovery, _ = self.recover_stranded(replacement)
        self.assert_beta_reads_every_store(recovery)

    def test_a_refused_stranded_replace_is_retried_like_the_ledger(self):
        # Andy 2026-11-20 11:38:32Z: router_post_uncaught PermissionError
        # [WinError 5] from os.replace of work-parallel-stranded-v1.json while
        # _stranded_roll_forward recorded stage_failed. The ledger retries a
        # refused replace; the stranded store raised it to the router.
        import os
        from unittest.mock import patch
        first, replacement, token = self.strand(delete_worktree, "owned path unavailable; retained")
        self.assertEqual("submitted", self.report(replacement, token, "c" * 64, "done")["state"])
        real, refused = os.replace, []

        def replace(src, dst, *args, **kwargs):
            if str(dst).endswith("work-parallel-stranded-v1.json") and not refused:
                refused.append(dst)
                raise PermissionError(13, "Access is denied", str(dst))
            return real(src, dst, *args, **kwargs)
        with patch("os.replace", side_effect=replace):
            self.idle(replacement)
        self.assertEqual(1, len(refused))
        self.assertEqual("pending", self.stranded_rows()[replacement]["state"])

    def test_switched_branch_strands_the_replacement_before_review(self):
        message = "Git ownership/branch changed; retained"
        self.assert_stranded_before_review(*self.strand(switch_branch, message), message)

    def test_missing_registration_strands_the_replacement_before_review(self):
        message = "owned registration missing; retained"
        self.assert_stranded_before_review(*self.strand(repoint_registration, message), message)

    def test_stranded_recovery_is_fresh_and_not_counted_at_max_rework(self):
        message = "owned path unavailable; retained"
        first, replacement, token = self.strand(delete_worktree, message)
        self.assert_stranded_before_review(first, replacement, token, message)
        state = self.store.read()
        state["leases"][replacement]["rework"] = state["policy"]["max_rework"]
        self.store.path.write_text(json.dumps(state), encoding="utf-8")
        recovery, result = self.recover_stranded(replacement)
        self.assertEqual(state["policy"]["max_rework"], self.lease(recovery)["rework"])
        token2, name2 = self.launch(recovery, 1, "agent-a2", generation=result["dispatch"]["generation"])
        self.bind(recovery, "agent-a2", name2)
        (Path(result["dispatch"]["workspace"]) / "result.txt").write_text("recovered\n")
        self.report(recovery, token2, "d" * 64, "recovered")
        self.idle(recovery)
        self.assertEqual([recovery], [row["lease_id"] for row in self.pending()])

    def test_first_generation_deleted_worktree_converges_to_the_same_path(self):
        reserve = self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                            event="reserve", item_id=self.board.item, priority="P1")
        first = reserve["lease_id"]
        shutil.rmtree(reserve["workspace"]["path"])
        message = "owned path unavailable; retained"
        with self.assertRaisesRegex(WorkspaceError, message):
            self.call("event", {"host_id": "worker-1"}, event="bind", lease_id=first)
        self.report(first, reserve["worker_token"], "a" * 64, "done")
        self.idle(first)
        row = self.pending()[0]
        self.assertEqual((first, "not_applicable", "workspace_unavailable"),
                         (row["lease_id"], row["changed_before_bind"], row["stranded"]["kind"]))
        recovery, result = self.recover_stranded(first, decision_id="first-recovery")
        self.assertEqual(2, result["dispatch"]["generation"])
        self.assertTrue(self.lease(recovery)["workspace"]["path"])

    def test_restart_observation_of_a_stranded_replacement_is_idempotent(self):
        message = "owned path unavailable; retained"
        first, replacement, token = self.strand(delete_worktree, message)
        self.report(replacement, token, "c" * 64, "done")
        receipt = m.coordination(self.store).read()["runs"][replacement]["exit"]["digest"]
        out = self.call("event", {"internal": True, "launch_nonce": LATER_PROCESS, "absent_agent_id": "agent-a1",
                                  "absent_from_complete_list": True},
                        event="restart_submitted_terminal", lease_id=replacement,
                        generation=2, receipt_digest=receipt)
        self.assertTrue(out["review_pending"])
        before = self.durable_bytes()
        again = self.call("event", {"internal": True, "launch_nonce": LATER_PROCESS, "absent_agent_id": "agent-a1",
                                    "absent_from_complete_list": True},
                          event="restart_submitted_terminal", lease_id=replacement,
                          generation=2, receipt_digest=receipt)
        self.assertTrue(again["review_pending"])
        self.assertEqual(before, self.durable_bytes())
        self.assertEqual([replacement], [row["lease_id"] for row in self.pending()])

    def test_one_failed_workspace_bind_heals_at_the_next_idle_observation(self):
        """A transient bind failure on an intact worktree remains repairable."""
        first, replacement, worktree = self.rework()
        token, name = self.launch(replacement, 1, "agent-a1")
        with patch.object(workspace_module, "bind", side_effect=WorkspaceError("git rev-parse timed out after 30 s")):
            with self.assertRaisesRegex(WorkspaceError, "timed out"):
                self.bind(replacement, "agent-a1", name)
        self.assertEqual("bound", self.reviews()[first]["launch"]["state"])
        self.assertIsNone(self.manifest(replacement)["host_id"])
        self.report(replacement, token, "c" * 64, "done")
        self.idle(replacement)
        record = self.manifest(replacement)
        self.assertEqual(("agent-a1", True, "native completed"),
                         (record["host_id"], record["idle"], record["idle_proof"]))
        self.assertEqual([replacement], [row["lease_id"] for row in self.pending()])
        self.assertNotIn("stranded", self.pending()[0])
        self.assert_beta_reads_every_store(replacement)

    def test_repaired_placeholder_is_replaced_with_real_facts_and_one_new_wake(self):
        message = "Git ownership/branch changed; retained"
        first, replacement, token = self.strand(switch_branch, message)
        self.report(replacement, token, "c" * 64, "done")
        self.idle(replacement)
        wake1 = self.call("event", {"internal": True}, event="review_wake",
                          operation="prepare", lease_id=replacement)
        self.call("event", {"internal": True}, event="review_wake", operation="record",
                  lease_id=replacement, attempt_id=wake1["attempt_id"], state="queued")
        record = self.manifest(replacement)
        git(record["path"], "checkout", record["branch"])
        workspace_module.observe(self.repo, replacement)
        self.assertEqual(("queued", 1), (self.stranded_rows()[replacement]["wake"]["state"],
                                          self.stranded_rows()[replacement]["wake"]["attempts"]))
        self.idle(replacement)
        repaired = self.reviews()[replacement]
        self.assertFalse(repaired["workspace"]["diff_stat"].startswith("workspace unavailable: "))
        self.assertEqual("done", self.stranded_rows()[replacement]["state"])
        # Exactly one new wake for the real facts, on the real row.
        self.assertEqual(("pending", 0), (repaired["wake"]["state"], repaired["wake"]["attempts"]))
        self.assertEqual([(replacement, None)], [(row["lease_id"], row.get("stranded"))
                                                 for row in self.pending()])
        wake2 = self.call("event", {"internal": True}, event="review_wake",
                          operation="prepare", lease_id=replacement)
        self.call("event", {"internal": True}, event="review_wake", operation="record",
                  lease_id=replacement, attempt_id=wake2["attempt_id"], state="queued")
        before = self.durable_bytes()
        self.idle(replacement)
        self.assertEqual(before, self.durable_bytes())
        self.assertEqual(("queued", 1), (self.reviews()[replacement]["wake"]["state"],
                                          self.reviews()[replacement]["wake"]["attempts"]))
        self.assert_beta_reads_every_store(replacement)

    def test_running_no_report_releases_capability_and_publishes_one_row(self):
        reserve = self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                            event="reserve", item_id=self.board.item, priority="P1")
        lease_id = reserve["lease_id"]
        self.call("event", {"host_id": "agent-no-report"}, event="bind", lease_id=lease_id)
        shutil.rmtree(reserve["workspace"]["path"])
        salvage1 = self.call("event", {}, event="host_salvage", lease_id=lease_id,
                             reason="native completed without report")
        salvage2 = self.call("event", {}, event="host_salvage", lease_id=lease_id,
                             reason="native completed without report")
        self.assertTrue(salvage1["workspace_unavailable"])
        self.assertEqual(salvage1["receipt_digest"], salvage2["receipt_digest"])
        terminal = self.call("event", {"proof": "durable salvage receipt"},
                             event="unreported", lease_id=lease_id)
        self.assertTrue(terminal["stranded_review"])
        lease = self.lease(lease_id)
        self.assertEqual(("unreported", False, None),
                         (lease["state"], lease["slot"], lease["report_hash"]))
        self.assertEqual(0, self.call("status", {})["active_device"])
        with self.assertRaises(WorkParallelError):
            self.report(lease_id, reserve["worker_token"], "a" * 64, "late report")
        row = self.pending()[0]
        self.assertEqual((lease_id, "no_report", None, None),
                         (row["lease_id"], row["stranded"]["kind"], row["artifact"],
                          row["completion_receipt"]))
        self.assertEqual({"path", "branch", "head", "unavailable", "reason"}, set(row["workspace"]))
        before = self.durable_bytes()
        replay = self.call("event", {"proof": "durable salvage receipt"},
                           event="unreported", lease_id=lease_id)
        self.assertEqual(before, self.durable_bytes())
        self.assertTrue(replay["stranded_review"])
        with self.assertRaisesRegex(WorkParallelError, "stranded result awaiting main"):
            self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                      event="reserve", item_id=self.board.item, priority="P1")
        recovery, result = self.recover_stranded(lease_id, decision_id="recover-no-report", kind="no_report")
        self.assertEqual("unreported", self.lease(lease_id)["state"])
        self.assertEqual(0, self.lease(recovery)["rework"])

    def test_repair_before_decision_replaces_published_real_row_with_placeholder(self):
        first, replacement, worktree = self.rework()
        token, name = self.launch(replacement, 1, "agent-a1")
        self.bind(replacement, "agent-a1", name)
        self.report(replacement, token, "c" * 64, "done")
        self.idle(replacement)
        shutil.rmtree(worktree)
        with self.assertRaisesRegex(WorkParallelError,
                                    "workspace unavailable: .*; approve refused; rework creates a fresh workspace"):
            self.review(replacement, "approve", "approve-2")
        row = self.pending()[0]
        self.assertEqual("workspace_unavailable", row["stranded"]["kind"])
        # The ordinary schema-5 row published while healthy is terminalized:
        # removed by exact CAS, with the sidecar holding the exact audit.
        self.assertNotIn(replacement, self.reviews())
        tombstone = self.stranded_rows()[replacement]["legacy_review_removed"]
        self.assertEqual("removed", tombstone["state"])
        status, = self.beta_process(("work_parallel_status", {}))
        self.assertEqual([], [value for value in status["ok"]["review"]["pending"]
                              if value["state"] == "pending"])
        recovery, _ = self.recover_stranded(replacement)
        self.assertNotEqual(str(worktree), self.lease(recovery)["workspace"]["path"])
        # The new build shows one row for the item: the recovered stranded row.
        self.assertEqual([(replacement, "rework_ready")],
                         [(value["lease_id"], value["state"]) for value in self.pending()])
        status, = self.beta_process(("work_parallel_status", {}))
        self.assertNotIn(replacement, [value["lease_id"] for value in status["ok"]["review"]["pending"]])

    def published_then_broken(self):
        first, replacement, worktree = self.rework()
        token, name = self.launch(replacement, 1, "agent-a1")
        self.bind(replacement, "agent-a1", name)
        self.report(replacement, token, "c" * 64, "done")
        self.idle(replacement)
        shutil.rmtree(worktree)
        return first, replacement

    def test_legacy_review_removal_crash_points_converge_without_losing_a_review(self):
        from ciel_runtime_support import work_parallel_finalization as finalization_module
        from ciel_runtime_support import work_parallel_stranded as stranded_module
        points = (
            ("before sidecar save", stranded_module.WorkParallelStrandedStore, "pending", 1),
            ("after sidecar save, before delete", finalization_module.WorkParallelFinalizationStore,
             "remove_undecided", 1),
            ("after delete, before tombstone completion", stranded_module.WorkParallelStrandedStore,
             "legacy_review", 2),
            ("after completion", None, None, 0),
        )
        for label, owner, method, crash_call in points:
            with self.subTest(point=label):
                self.setUp()
                first, replacement = self.published_then_broken()
                if owner is not None:
                    real = getattr(owner, method)
                    calls = []

                    def crash(self_, *args, _real=real, _n=crash_call, **kwargs):
                        calls.append(1)
                        if len(calls) == _n:
                            raise OSError("injected crash: " + label)
                        return _real(self_, *args, **kwargs)
                    with patch.object(owner, method, crash):
                        with self.assertRaises(OSError):
                            self.review(replacement, "approve", "approve-2")
                    # A real review is never lost: either the schema-5 row or
                    # the durable sidecar row still carries the result.
                    self.assertTrue(replacement in self.reviews() or replacement in self.stranded_rows())
                # Replay: the next decision attempt converges the move exactly once.
                with self.assertRaisesRegex(WorkParallelError, "result is now stranded|workspace unavailable"):
                    self.review(replacement, "approve", "approve-3")
                self.assertNotIn(replacement, self.reviews())
                self.assertEqual("removed", self.stranded_rows()[replacement]["legacy_review_removed"]["state"])
                before = self.durable_bytes()
                with self.assertRaisesRegex(WorkParallelError, "workspace unavailable"):
                    self.review(replacement, "approve", "approve-4")
                self.assertEqual(before, self.durable_bytes())
                recovery, _ = self.recover_stranded(replacement)
                self.assert_beta_reads_every_store(recovery)

    def test_turn_end_idle_observation_moves_a_broken_published_review_without_a_decision(self):
        """The host's own boundary is enough: no main decision is needed for beta to stop gating."""
        first, replacement = self.published_then_broken()
        observed = self.idle(replacement)
        self.assertNotIn("review_publication_error", observed)
        self.assertNotIn(replacement, self.reviews())
        self.assertEqual("removed", self.stranded_rows()[replacement]["legacy_review_removed"]["state"])
        self.assertEqual([(replacement, "pending", "workspace_unavailable")],
                         [(row["lease_id"], row["state"], row["stranded"]["kind"])
                          for row in observed["review"]["pending"]])
        status, = self.beta_process(("work_parallel_status", {}))
        self.assertEqual([], [row for row in status["ok"]["review"]["pending"] if row["state"] == "pending"])
        self.assert_beta_reads_every_store()

    def test_legacy_review_changed_between_capture_and_delete_is_a_conflict_and_never_deleted(self):
        from ciel_runtime_support import work_parallel_finalization as finalization_module
        first, replacement = self.published_then_broken()
        real = finalization_module.WorkParallelFinalizationStore.remove_undecided

        def changed_first(self_, lease_id, *, row_digest, expected_version):
            # Another writer (an old build's wake record) changes the row first.
            state = self_.read()
            wake = self_.wake_prepare(lease_id, expected_version=state["version"])[1]
            self_.wake_record(lease_id, wake["attempt_id"], "queued", expected_version=self_.read()["version"])
            return real(self_, lease_id, row_digest=row_digest, expected_version=self_.read()["version"])
        with patch.object(finalization_module.WorkParallelFinalizationStore, "remove_undecided", changed_first):
            with self.assertRaisesRegex(WorkParallelError, "legacy review changed since capture"):
                self.review(replacement, "approve", "approve-2")
        self.assertIn(replacement, self.reviews())
        self.assertEqual(("queued", 1), (self.reviews()[replacement]["wake"]["state"],
                                          self.reviews()[replacement]["wake"]["attempts"]))
        self.assertEqual("prepared", self.stranded_rows()[replacement]["legacy_review_removed"]["state"])

    def test_committed_result_whose_workspace_breaks_after_publication_still_moves_to_stranded(self):
        """Celly n1635 (2026-11-27): the fork committed its fix, so the published
        review's snapshot HEAD is the fork's commit while the lease keeps the HEAD
        it was reserved at. When the workspace later fails verification, the move
        to the sidecar must not refuse that row as a different one, forever."""
        first, replacement, worktree = self.rework()
        token, name = self.launch(replacement, 1, "agent-a1")
        self.bind(replacement, "agent-a1", name)
        (worktree / "result.txt").write_text("fixed result\n")
        git(worktree, "add", "result.txt")
        git(worktree, "commit", "-m", "fix result")
        self.report(replacement, token, "c" * 64, "done")
        self.idle(replacement)
        reserved_head = self.lease(replacement)["workspace"]["head"]
        published_head = self.reviews()[replacement]["workspace"]["head"]
        self.assertNotEqual(reserved_head, published_head)
        shutil.rmtree(worktree)
        with self.assertRaisesRegex(WorkParallelError,
                                    "workspace unavailable: .*; approve refused; rework creates a fresh workspace"):
            self.review(replacement, "approve", "approve-2")
        self.assertNotIn(replacement, self.reviews())
        side = self.stranded_rows()[replacement]
        self.assertEqual("removed", side["legacy_review_removed"]["state"])
        # The exact removed row is still bound by its digest; a replay writes nothing.
        self.assertEqual(64, len(side["legacy_review_removed"]["row_digest"]))
        row = self.pending()[0]
        self.assertEqual((replacement, "workspace_unavailable"), (row["lease_id"], row["stranded"]["kind"]))
        before = self.durable_bytes()
        with self.assertRaisesRegex(WorkParallelError, "workspace unavailable"):
            self.review(replacement, "approve", "approve-3")
        self.assertEqual(before, self.durable_bytes())
        recovery, _ = self.recover_stranded(replacement)
        self.assertNotEqual(str(worktree), self.lease(recovery)["workspace"]["path"])

    def test_git_timeout_at_decision_is_not_a_broken_workspace(self):
        """Celly n1635 (2026-11-27): one verification git call timed out after 39 s
        under host memory pressure, and the healthy worktree was recorded as
        unavailable (stranded). A timeout proves nothing about the worktree: the
        decision fails retryably, no stranded row is written, and the next
        decision on the same healthy worktree finalizes the published review."""
        first, replacement, worktree = self.rework()
        token, name = self.launch(replacement, 1, "agent-a1")
        self.bind(replacement, "agent-a1", name)
        self.report(replacement, token, "c" * 64, "done")
        self.idle(replacement)
        self.assertIn(replacement, self.reviews())
        with patch.object(workspace_module, "_GIT_TIMEOUT_SECONDS", 0.0001):
            with self.assertRaisesRegex(ValueError, "timed out"):
                self.review(replacement, "approve", "approve-2")
        self.assertNotIn(replacement, self.stranded_rows())
        self.assertIn(replacement, self.reviews())
        self.assertTrue(worktree.is_dir())
        self.assertEqual("finalized", self.review(replacement, "approve", "approve-3")["state"])

    def test_non_idle_activity_write_still_needs_a_bound_workspace(self):
        first, replacement, worktree = self.rework()
        self.launch(replacement, 1, "agent-a1")
        with self.assertRaisesRegex(WorkspaceError, "workspace host binding mismatch"):
            workspace_module.set_activity(self.repo, replacement, "agent-a1", idle=False)
        self.assertIsNone(self.manifest(replacement)["host_id"])

    def test_idle_observation_never_takes_a_workspace_bound_to_another_host(self):
        first, replacement, worktree = self.rework()
        token, name = self.launch(replacement, 1, "agent-a1")
        self.bind(replacement, "agent-a1", name)
        for idle in (True, False):
            with self.assertRaisesRegex(WorkspaceError, "workspace host binding mismatch"):
                workspace_module.set_activity(self.repo, replacement, "agent-other", idle=idle,
                                              proof="observed" if idle else None)
        self.assertEqual("agent-a1", self.manifest(replacement)["host_id"])

    # Never-reported running fork --------------------------------------------------

    def _block_p1(self):
        """Fill P1 with an unrelated running lease (same helper as the finalization tests)."""
        state = self.store.read()
        state["policy"]["priority_limits"]["P1"] = 1
        template = next(iter(state["leases"].values()))
        state["leases"]["blocker"] = {**template, "id": "blocker", "item_id": "other", "state": "running",
                                      "slot": True, "usage": .05, "artifact": None, "host_id": "other-host",
                                      "role": "worker", "idle_proof": None, "cleanup_proof": None,
                                      "report_hash": "b" * 64}
        for key in ("workspace", "terminal"):
            state["leases"]["blocker"].pop(key, None)
        self.store.path.write_text(json.dumps(state), encoding="utf-8")

    def _unblock_p1(self):
        state = self.store.read()
        del state["leases"]["blocker"]
        self.store.path.write_text(json.dumps(state), encoding="utf-8")

    def running_fork_without_report(self, breakage=True):
        """A bound first-generation fork ends without a report; salvage may fail."""
        reserve = self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                            event="reserve", item_id=self.board.item, priority="P1")
        lease_id = reserve["lease_id"]
        self.call("event", {"host_id": "agent-no-report"}, event="bind", lease_id=lease_id)
        if breakage:
            shutil.rmtree(reserve["workspace"]["path"])
        salvage = self.call("event", {}, event="host_salvage", lease_id=lease_id,
                            reason="native completed without report")
        return reserve, lease_id, salvage

    def test_terminal_write_without_an_intent_publishes_no_stranded_row(self):
        """Another writer ended the lease first: no intent, no row (lead ruling)."""
        reserve, lease_id, salvage = self.running_fork_without_report()
        self.assertTrue(salvage["workspace_unavailable"])
        self.store.terminate_unreported(lease_id, proof="another build's terminal write",
                                        expected_version=self.store.read()["version"])
        replay = self.call("event", {"proof": "durable salvage receipt"}, event="unreported", lease_id=lease_id)
        self.assertNotIn("stranded_review", replay)
        self.assertEqual(({}, {}), (self.reviews(), self.stranded_rows()))
        self.assertEqual([], self.pending())
        # The item falls under the existing redispatch behavior.
        again = self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                          event="reserve", item_id=self.board.item, priority="P1")
        self.assertNotEqual(lease_id, again["lease_id"])

    def test_intent_is_captured_while_active_and_the_terminal_write_uses_its_version(self):
        reserve, lease_id, salvage = self.running_fork_without_report()
        before = self.store.read()
        self.call("event", {"proof": "durable salvage receipt"}, event="unreported", lease_id=lease_id)
        row = self.stranded_rows()[lease_id]
        self.assertEqual(("pending", [lease_id], before["version"]),
                         (row["state"], row["known_lease_ids"], row["capture_ledger_version"]))
        self.assertEqual(before["version"] + 1, self.store.read()["version"])

    def test_crash_after_intent_before_terminal_write_converges_on_replay(self):
        from ciel_runtime_support import work_parallel as ledger_module
        reserve, lease_id, salvage = self.running_fork_without_report()
        real = ledger_module.WorkParallelStore.terminate_unreported
        with patch.object(ledger_module.WorkParallelStore, "terminate_unreported",
                          side_effect=OSError("injected crash before the terminal write")):
            with self.assertRaises(OSError):
                self.call("event", {"proof": "durable salvage receipt"}, event="unreported", lease_id=lease_id)
        self.assertEqual(("running", "intent"), (self.lease(lease_id)["state"],
                                                  self.stranded_rows()[lease_id]["state"]))
        # An intent is never a decidable row, and the active lease still fences dispatch.
        self.assertEqual([], self.pending())
        with self.assertRaisesRegex(WorkParallelError, "stranded result awaiting main|no eligible candidate"):
            self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                      event="reserve", item_id=self.board.item, priority="P1")
        del real
        self.call("event", {"proof": "durable salvage receipt"}, event="unreported", lease_id=lease_id)
        self.assertEqual(("unreported", "pending"), (self.lease(lease_id)["state"],
                                                      self.stranded_rows()[lease_id]["state"]))
        self.assertEqual([lease_id], [row["lease_id"] for row in self.pending()])

    def test_crash_after_terminal_write_before_activation_uses_the_persisted_known_set(self):
        from ciel_runtime_support import work_parallel_stranded as stranded_module
        reserve, lease_id, salvage = self.running_fork_without_report()
        with patch.object(stranded_module.WorkParallelStrandedStore, "activate",
                          side_effect=OSError("injected crash before activation")):
            with self.assertRaises(OSError):
                self.call("event", {"proof": "durable salvage receipt"}, event="unreported", lease_id=lease_id)
        captured = self.stranded_rows()[lease_id]
        self.assertEqual(("unreported", "intent"), (self.lease(lease_id)["state"], captured["state"]))
        # The next dispatch attempt completes the intent; it never recaptures.
        with self.assertRaisesRegex(WorkParallelError, "stranded result awaiting main"):
            self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                      event="reserve", item_id=self.board.item, priority="P1")
        row = self.stranded_rows()[lease_id]
        self.assertEqual(("pending", captured["known_lease_ids"], captured["capture_ledger_version"]),
                         (row["state"], row["known_lease_ids"], row["capture_ledger_version"]))

    def test_ledger_write_between_intent_and_terminal_cas_recaptures_and_beta_is_fenced(self):
        from ciel_runtime_support import work_parallel_mcp as mcp_module
        reserve, lease_id, salvage = self.running_fork_without_report()
        real = mcp_module._publish_stranded
        interleaved = {}

        def publish_then_interleave(store, facts):
            published = real(store, facts)
            if not interleaved:
                # A real 57dbfb3 process writes the ledger and tries to dispatch
                # the item while the stranded lease is still running.
                interleaved["configure"], interleaved["reserve"] = self.beta_process(
                    ("work_parallel_configure", {"changes": {"launches_per_minute": 30}}),
                    ("work_parallel_event", {"event": "reserve", "item_id": self.board.item, "priority": "P1",
                                             "host": {"fork": True, "workspace_repo": str(self.repo)}}))
            return published
        first_version = self.store.read()["version"]
        with patch.object(mcp_module, "_publish_stranded", publish_then_interleave):
            self.call("event", {"proof": "durable salvage receipt"}, event="unreported", lease_id=lease_id)
        self.assertIn("ok", interleaved["configure"])
        # Fenced by the still-active stranded lease, not by any other refusal.
        self.assertEqual(("WorkParallelError", "no eligible candidate"),
                         (interleaved["reserve"]["error"], interleaved["reserve"]["message"]))
        row = self.stranded_rows()[lease_id]
        self.assertEqual(("pending", [lease_id]), (row["state"], row["known_lease_ids"]))
        self.assertEqual(first_version + 1, row["capture_ledger_version"])
        self.assertEqual("unreported", self.lease(lease_id)["state"])

    def test_beta_terminal_and_reserve_before_the_new_terminal_write_supersede_the_row(self):
        from ciel_runtime_support import work_parallel_mcp as mcp_module
        reserve, lease_id, salvage = self.running_fork_without_report()
        real = mcp_module._publish_stranded
        beta = {}

        def publish_then_beta_ends_and_redispatches(store, facts):
            published = real(store, facts)
            if not beta:
                beta["unreported"], beta["reserve"] = self.beta_process(
                    ("work_parallel_event", {"event": "unreported", "lease_id": lease_id,
                                             "host": {"proof": "beta terminal observation"}}),
                    ("work_parallel_event", {"event": "reserve", "item_id": self.board.item, "priority": "P1",
                                             "host": {"fork": True, "workspace_repo": str(self.repo)}}))
            return published
        with patch.object(mcp_module, "_publish_stranded", publish_then_beta_ends_and_redispatches):
            self.call("event", {"proof": "durable salvage receipt"}, event="unreported", lease_id=lease_id)
        self.assertIn("ok", beta["unreported"])
        beta_lease = beta["reserve"]["ok"]["lease_id"]
        self.assertNotIn(beta_lease, self.stranded_rows()[lease_id]["known_lease_ids"])
        self.assertEqual([], self.pending())
        shown = self.call("status", {"internal": True})["review"]["ended"]
        self.assertEqual([(lease_id, "superseded", beta_lease)],
                         [(row["lease_id"], row["state"], row["stranded"]["superseding_lease_id"]) for row in shown])
        row = self.stranded_rows()[lease_id]
        self.assertEqual(("superseded", beta_lease), (row["state"], row["superseded"]["lease_id"]))
        for decision in ("approve", "rework"):
            with self.assertRaisesRegex(WorkParallelError, "superseded by lease " + beta_lease + "; reread"):
                self.review(lease_id, decision, decision + "-superseded")
        self.assert_beta_reads_every_store()

    def test_crash_in_the_supersede_write_converges_on_replay(self):
        from ciel_runtime_support import work_parallel_stranded as stranded_module
        reserve, lease_id, salvage = self.running_fork_without_report()
        self.call("event", {"proof": "durable salvage receipt"}, event="unreported", lease_id=lease_id)
        beta_reserve, = self.beta_process(("work_parallel_event", {
            "event": "reserve", "item_id": self.board.item, "priority": "P1",
            "host": {"fork": True, "workspace_repo": str(self.repo)}}))
        beta_lease = beta_reserve["ok"]["lease_id"]
        with patch.object(stranded_module.WorkParallelStrandedStore, "supersede",
                          side_effect=OSError("injected crash in the supersede write")):
            with self.assertRaises(OSError):
                self.review(lease_id, "rework", "rework-during-crash")
        self.assertEqual("pending", self.stranded_rows()[lease_id]["state"])
        # Read-time derivation already hides it as superseded, with no wake.
        self.assertEqual([], self.pending())
        self.assertEqual([("superseded", beta_lease)], [(row["state"], row["stranded"]["superseding_lease_id"])
                                                        for row in self.call("status", {"internal": True})
                                                        ["review"]["ended"]])
        with self.assertRaisesRegex(WorkParallelError, "superseded by lease " + beta_lease):
            self.review(lease_id, "rework", "rework-after-crash")
        self.assertEqual(("superseded", beta_lease), (self.stranded_rows()[lease_id]["state"],
                                                      self.stranded_rows()[lease_id]["superseded"]["lease_id"]))

    def test_injected_fence_bypass_cancels_an_unbound_recovery_once_and_crash_converges(self):
        """Defensive: the active-lease fence makes this unreachable for both builds."""
        from ciel_runtime_support import work_parallel as ledger_module
        reserve, lease_id, salvage = self.running_fork_without_report()
        self.call("event", {"proof": "durable salvage receipt"}, event="unreported", lease_id=lease_id)
        self._block_p1()
        waiting = self.review(lease_id, "rework", "rework-waits", evidence="recover")
        self.assertEqual("awaiting_capacity", waiting["state"])
        self._unblock_p1()
        # Reserve the recovery but crash before the workspace stage.
        from ciel_runtime_support import work_parallel_workspace as ws
        with patch.object(ws, "prepare", side_effect=OSError("injected crash before workspace")):
            with self.assertRaises(OSError):
                self.call("event", {"internal": True}, event="review_roll_forward", lease_id=lease_id)
        recovery = self.stranded_rows()[lease_id]["replacement_lease_id"]
        self.assertEqual(("reserved", None), (self.lease(recovery)["state"], self.lease(recovery)["host_id"]))
        # A writer that bypassed the active fence adds another execution row.
        state = self.store.read()
        intruder = dict(state["leases"][lease_id], id="intruder-lease", state="reserved", host_id=None,
                        slot=True, usage=.05, idle_proof=None, evidence=None, terminal=None, report_hash=None)
        state["leases"]["intruder-lease"] = intruder
        self.store.path.write_text(json.dumps(state), encoding="utf-8")
        real = ledger_module.WorkParallelStore.release_unbound
        calls = []

        def crash_once(self_, *args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("injected crash in cancel")
            return real(self_, *args, **kwargs)
        with patch.object(ledger_module.WorkParallelStore, "release_unbound", crash_once):
            with self.assertRaises(OSError):
                self.call("event", {"internal": True}, event="review_roll_forward", lease_id=lease_id)
            result = self.call("event", {"internal": True}, event="review_roll_forward", lease_id=lease_id)
        self.assertEqual(("superseded", "intruder-lease"), (result["state"], result["superseding_lease_id"]))
        self.assertNotIn(recovery, self.store.read()["leases"])
        self.assertNotIn(recovery, m.coordination(self.store).read()["runs"])
        again = self.call("event", {"internal": True}, event="review_roll_forward", lease_id=lease_id)
        self.assertEqual("superseded", again["state"])

    def test_older_same_item_lease_and_own_recovery_never_supersede(self):
        first, replacement, worktree = self.rework()
        token, name = self.launch(replacement, 1, "agent-a1")
        self.bind(replacement, "agent-a1", name)
        shutil.rmtree(worktree)
        self.call("event", {}, event="host_salvage", lease_id=replacement, reason="native completed without report")
        self.call("event", {"proof": "durable salvage receipt"}, event="unreported", lease_id=replacement)
        row = self.stranded_rows()[replacement]
        self.assertIn(first, row["known_lease_ids"])
        self.assertEqual("pending", row["state"])
        recovery, _ = self.recover_stranded(replacement, kind="no_report")
        self.assertEqual("recovered", self.stranded_rows()[replacement]["state"])
        self.call("status", {})
        self.assertEqual("recovered", self.stranded_rows()[replacement]["state"])

    def test_rework_decided_first_fences_a_later_beta_reserve(self):
        reserve, lease_id, salvage = self.running_fork_without_report()
        self.call("event", {"proof": "durable salvage receipt"}, event="unreported", lease_id=lease_id)
        recovery, result = self.recover_stranded(lease_id, decision_id="recover-first", kind="no_report")
        beta_reserve, beta_next = self.beta_process(
            ("work_parallel_event", {"event": "reserve", "item_id": self.board.item, "priority": "P1",
                                     "host": {"fork": True, "workspace_repo": str(self.repo)}}),
            ("work_parallel_event", {"event": "reserve_next", "host": {"fork": True, "workspace_repo": str(self.repo)}}))
        for refused in (beta_reserve, beta_next):
            self.assertEqual("WorkParallelError", refused["error"])
            self.assertIn("no eligible candidate", refused["message"])
        self.assertEqual("recovered", self.stranded_rows()[lease_id]["state"])

    def test_more_than_128_known_leases_publishes_no_row_and_keeps_the_lease_active(self):
        reserve, lease_id, salvage = self.running_fork_without_report()
        state = self.store.read()
        template = state["leases"][lease_id]
        for index in range(128):
            key = "old-%03d" % index
            state["leases"][key] = dict(template, id=key, state="done", slot=False, usage=0,
                                        artifact="a" * 64, idle_proof="old", cleanup_proof="old",
                                        evidence="old", terminal=None, report_hash=None)
        self.store.path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(WorkParallelError, "known-lease bound exceeded: 129/128; lease kept active"):
            self.call("event", {"proof": "durable salvage receipt"}, event="unreported", lease_id=lease_id)
        self.assertEqual(("running", True), (self.lease(lease_id)["state"], self.lease(lease_id)["slot"]))
        self.assertEqual({}, self.stranded_rows())

    def test_sidecar_validator_is_exact_and_old_readers_ignore_intent_and_superseded(self):
        from ciel_runtime_support import work_parallel_stranded as stranded_module
        reserve, lease_id, salvage = self.running_fork_without_report()
        self.call("event", {"proof": "durable salvage receipt"}, event="unreported", lease_id=lease_id)
        good = json.loads((self.tmp / "work-parallel-stranded-v1.json").read_text(encoding="utf-8"))
        stranded_module.validate(good)
        row = good["rows"][lease_id]
        mutations = {
            "extra field": lambda r: r.update(extra=1),
            "superseded without outcome": lambda r: r.update(state="superseded"),
            "intent on workspace_unavailable": lambda r: r.update(state="intent", kind="workspace_unavailable",
                                                                  artifact="a" * 64),
            "duplicate known ids": lambda r: r.update(known_lease_ids=[lease_id, lease_id]),
            "lease missing from known": lambda r: r.update(known_lease_ids=["other"]),
            "known ids over bound": lambda r: r.update(known_lease_ids=[lease_id] + ["k%d" % i for i in range(128)]),
            "bad tombstone": lambda r: r.update(legacy_review_removed={"state": "removed"}),
        }
        for label, mutate in mutations.items():
            with self.subTest(mutation=label):
                bad = json.loads(json.dumps(good))
                mutate(bad["rows"][lease_id])
                with self.assertRaises(WorkParallelError):
                    stranded_module.validate(bad)
        del row
        self.assert_beta_reads_every_store()
        status, = self.beta_process(("work_parallel_status", {}))
        self.assertEqual([], status["ok"]["review"]["pending"])

    def test_healthy_salvage_keeps_todays_redispatch_and_publishes_nothing(self):
        reserve, lease_id, salvage = self.running_fork_without_report(breakage=False)
        self.assertNotIn("workspace_unavailable", salvage)
        terminal = self.call("event", {"proof": "durable salvage receipt"},
                             event="unreported", lease_id=lease_id)
        self.assertNotIn("stranded_review", terminal)
        self.assertEqual(({}, {}), (self.reviews(), self.stranded_rows()))
        again = self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                          event="reserve", item_id=self.board.item, priority="P1")
        self.assertNotEqual(lease_id, again["lease_id"])

    def test_no_report_replacement_recovers_at_max_rework_through_approval(self):
        first, replacement, worktree = self.rework()
        token, name = self.launch(replacement, 1, "agent-a1")
        self.bind(replacement, "agent-a1", name)
        shutil.rmtree(worktree)
        salvage = self.call("event", {}, event="host_salvage", lease_id=replacement,
                            reason="native completed without report")
        self.assertTrue(salvage["workspace_unavailable"])
        self.assertTrue(self.call("event", {"proof": "durable salvage receipt"},
                                  event="unreported", lease_id=replacement)["stranded_review"])
        shown = self.pending()
        # The bind itself was clean (measured); only the later salvage failed.
        self.assertEqual([(replacement, "no_report", False)],
                         [(row["lease_id"], row["stranded"]["kind"], row["changed_before_bind"])
                          for row in shown])
        self.assert_beta_reads_every_store()
        before = self.durable_bytes()
        with self.assertRaisesRegex(WorkParallelError,
                                    "no worker report: approve refused; rework creates a fresh workspace"):
            self.review(replacement, "approve", "approve-2")
        self.assertEqual(before, self.durable_bytes())
        state = self.store.read()
        state["leases"][replacement]["rework"] = state["policy"]["max_rework"]
        self.store.path.write_text(json.dumps(state), encoding="utf-8")
        recovery, result = self.recover_stranded(replacement, kind="no_report")
        self.assertEqual((3, state["policy"]["max_rework"]),
                         (result["dispatch"]["generation"], self.lease(recovery)["rework"]))
        self.assert_beta_reads_every_store(recovery)
        token3, name3 = self.launch(recovery, 1, "agent-a3", generation=3)
        self.bind(recovery, "agent-a3", name3)
        (Path(result["dispatch"]["workspace"]) / "result.txt").write_text("recovered\n")
        self.report(recovery, token3, "e" * 64, "recovered")
        self.idle(recovery)
        row = self.pending()[0]
        self.assertEqual((recovery, "not_applicable", None),
                         (row["lease_id"], row["changed_before_bind"], row["blocked"]))
        self.assertNotIn("stranded", row)
        self.assertEqual(["result.txt"], row["workspace"]["changed_files"])
        self.assertEqual("done", self.stranded_rows()[replacement]["state"])
        self.assertEqual([recovery], [value["lease_id"] for value in self.pending()])
        self.assert_beta_reads_every_store(recovery)
        self.assertEqual("finalized", self.review(recovery, "approve", "approve-3")["state"])

    def test_recovery_saga_crash_at_every_stage_converges_on_replay(self):
        from ciel_runtime_support import work_parallel_receipts as receipts_module
        from ciel_runtime_support import work_parallel as ledger_module
        injections = (
            ("reserved", ledger_module.WorkParallelStore, "reserve_stranded_recovery"),
            ("workspace", ledger_module.WorkParallelStore, "attach_workspace"),
            ("coordination", receipts_module.WorkCoordinationStore, "supersede"),
            ("retired", ledger_module.WorkParallelStore, "retire_stranded"),
        )
        for stage, owner, method in injections:
            with self.subTest(stage=stage):
                self.setUp()
                message = "owned path unavailable; retained"
                first, replacement, token = self.strand(delete_worktree, message)
                self.report(replacement, token, "c" * 64, "done")
                self.idle(replacement)
                real = getattr(owner, method)
                calls = []

                def crash_once(self_, *args, _real=real, **kwargs):
                    calls.append(1)
                    if len(calls) == 1:
                        raise WorkParallelError("injected crash at " + stage)
                    return _real(self_, *args, **kwargs)
                with patch.object(owner, method, crash_once):
                    result = self.review(replacement, "rework", "rework-crash",
                                         evidence="recover in a fresh workspace")
                self.assertEqual(("deciding", "host_pump"), (result["state"], result["retry"]))
                self.assertEqual("stage_failed", self.stranded_rows()[replacement]["blocked"]["code"])
                replayed = self.call("event", {"internal": True}, event="review_roll_forward",
                                     lease_id=replacement)
                self.assertEqual("rework_ready", replayed["state"])
                recovery = replayed["dispatch"]["lease_id"]
                again = self.call("event", {"internal": True}, event="review_roll_forward",
                                  lease_id=replacement)
                self.assertEqual(replayed["dispatch"], again["dispatch"])
                leases = [key for key, row in self.store.read()["leases"].items()
                          if row["item_id"] == self.board.item and row["role"] == "resume"
                          and row["state"] == "reserved"]
                self.assertEqual([recovery], leases)
                self.assertEqual("recovered", self.stranded_rows()[replacement]["state"])
                self.assertNotIn(replacement, self.reviews())
                self.assert_beta_reads_every_store(recovery)

    def test_positive_control_ordinary_rework_at_max_rework_is_still_refused(self):
        first, worktree = self.submitted()
        state = self.store.read()
        state["leases"][first]["rework"] = state["policy"]["max_rework"]
        self.store.path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(WorkParallelError, "rework limit reached"):
            self.review(first, "rework", "rework-limit")

    # Mixed version: a real 57dbfb3 process on the same files ------------------------

    def test_beta_process_fails_closed_on_a_workspace_unavailable_row(self):
        message = "owned path unavailable; retained"
        first, replacement, token = self.strand(delete_worktree, message)
        self.report(replacement, token, "c" * 64, "done")
        self.idle(replacement)
        before = self.durable_bytes()
        status, approve, rework = self.beta_process(
            ("work_parallel_status", {}),
            ("work_parallel_item_review", {"lease_id": replacement, "decision": "approve",
                                           "decision_id": "beta-approve", "evidence": "beta"}),
            ("work_parallel_item_review", {"lease_id": replacement, "decision": "rework",
                                           "decision_id": "beta-rework", "evidence": "beta"}))
        shown = status["ok"]["review"]["pending"]
        # 57dbfb3 sees only the predecessor's finished hand-off (rework_ready),
        # which its parallelReviewPending never gates on: no undecidable row.
        self.assertEqual([(first, "rework_ready")], [(row["lease_id"], row["state"]) for row in shown])
        self.assertEqual([], [row for row in shown if row["state"] == "pending"])
        for refused in (approve, rework):
            self.assertEqual(("WorkspaceError", message), (refused["error"], refused["message"]))
        self.assertEqual(before, self.durable_bytes())
        # Old writer transactions on the same files: a beta idle reconcile of the
        # stranded lease, a beta replay of the predecessor and a beta policy write.
        beta_idle, beta_replay, beta_configure = self.beta_process(
            ("work_parallel_event", {"event": "reconcile", "lease_id": replacement,
                                     "host": {"state": "idle", "proof": "native completed"}}),
            ("work_parallel_event", {"event": "review_roll_forward", "lease_id": first,
                                     "host": {"internal": True}}),
            ("work_parallel_configure", {"changes": {"launches_per_minute": 30}}))
        self.assertEqual("WorkspaceError", beta_idle["error"])
        self.assertIn("ok", beta_replay, beta_replay)
        self.assertEqual(30, beta_configure["ok"]["policy"]["launches_per_minute"])
        self.assertNotIn(replacement, self.reviews())
        self.assertEqual([replacement], [row["lease_id"] for row in self.pending()])
        recovery, _ = self.recover_stranded(replacement)
        self.assert_beta_reads_every_store(recovery)

    def test_beta_process_fails_closed_on_a_no_report_row_and_its_dispatch_is_fenced(self):
        reserve, lease_id, salvage = self.running_fork_without_report()
        self.call("event", {"proof": "durable salvage receipt"}, event="unreported", lease_id=lease_id)
        before = self.durable_bytes()
        approve, rework = self.beta_process(
            ("work_parallel_item_review", {"lease_id": lease_id, "decision": "approve",
                                           "decision_id": "beta-approve", "evidence": "beta"}),
            ("work_parallel_item_review", {"lease_id": lease_id, "decision": "rework",
                                           "decision_id": "beta-rework", "evidence": "beta"}))
        for refused in (approve, rework):
            self.assertEqual("WorkParallelError", refused["error"])
            self.assertIn("durable completion receipt required before main review", refused["message"])
        self.assertEqual(before, self.durable_bytes())
        status, = self.beta_process(("work_parallel_status", {}))
        self.assertEqual([], status["ok"]["review"]["pending"])
        # Ruling (a): 57dbfb3 redispatches (its existing healthy-salvage behavior).
        # The new build's stranded row is superseded and never recovers later.
        beta_reserve, = self.beta_process(("work_parallel_event", {
            "event": "reserve", "item_id": self.board.item, "priority": "P1",
            "host": {"fork": True, "workspace_repo": str(self.repo)}}))
        beta_lease = beta_reserve["ok"]["lease_id"]
        for decision in ("approve", "rework"):
            with self.assertRaisesRegex(WorkParallelError, "superseded by lease " + beta_lease + "; reread"):
                self.review(lease_id, decision, decision + "-after-beta")
        self.assertEqual(("superseded", beta_lease), (self.stranded_rows()[lease_id]["state"],
                                                      self.stranded_rows()[lease_id]["superseded"]["lease_id"]))
        self.assertEqual([], self.pending())
        self.assertEqual([("superseded", beta_lease)], [(row["state"], row["stranded"]["superseding_lease_id"])
                                                        for row in self.call("status", {"internal": True})
                                                        ["review"]["ended"]])
        # Beta's lease ends; the new build still never starts its own recovery.
        state = self.store.read()
        state["leases"][beta_lease].update(state="unreported", slot=False, usage=0, report_hash=None,
                                           host_id="beta-agent", idle_proof="beta ended", evidence="beta ended",
                                           terminal={"reason": "unreported", "observed_at": 1.0,
                                                     "proof": "beta ended"})
        self.store.path.write_text(json.dumps(state), encoding="utf-8")
        replay = self.call("event", {"internal": True}, event="review_roll_forward", lease_id=lease_id)
        self.assertEqual("superseded", replay["state"])
        self.assertEqual([], [key for key, row in self.store.read()["leases"].items()
                              if row["role"] == "resume" and row["item_id"] == self.board.item])
        # Still visible to the host/operator after the superseding lease ended
        # (lead ruling, measured on a real 57dbfb3 console). Main's pending queue
        # never contains it.
        self.assertEqual([], self.pending())
        ended = self.call("status", {"internal": True})["review"]["ended"]
        self.assertEqual([(lease_id, "superseded", beta_lease, "unreported")],
                         [(row["lease_id"], row["state"], row["stranded"]["superseding_lease_id"],
                           row["stranded"]["superseding_state"]) for row in ended])
        self.assertNotIn(lease_id, self.reviews())
        self.assert_beta_reads_every_store()

# Snapshot names (fix A) ------------------------------------------------------------
# ``_git`` strips its output. A first status row that starts with a space (an
# unstaged change to a tracked file, " M name") used to lose that space before
# the name was sliced: "solution.py" was shown as "olution.py", and a tracked
# one-character name became empty, which refused the review row for good.

class SnapshotNameTests(ReworkFixture):
    def submit_unstaged_change(self, name):
        if not (self.repo / name).exists():
            (self.repo / name).write_text("base\n")
            git(self.repo, "add", name)
            git(self.repo, "commit", "-m", "add " + name)
        reserve = self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                            event="reserve", item_id=self.board.item, priority="P1")
        first = reserve["lease_id"]
        self.call("event", {"host_id": "worker-1"}, event="bind", lease_id=first)
        (Path(reserve["workspace"]["path"]) / name).write_text("changed\n")
        self.report(first, reserve["worker_token"], "a" * 64, "changed " + name)
        return first, self.idle(first)

    def observed(self, change, lease_id="lease-names", module=workspace_module):
        """Bounded snapshot of a fresh owned worktree after ``change`` ran in it."""
        record = workspace_module.prepare(self.repo, lease_id, config_dir=self.tmp / "config")
        change(Path(record["path"]))
        return module.salvage_observe(self.repo, lease_id, generation="1", terminal=True,
                                      slot=False, config_dir=self.tmp / "config")

    def test_unstaged_tracked_change_is_shown_with_its_full_name(self):
        first, _ = self.submit_unstaged_change("solution.py")
        workspace = self.pending()[0]["workspace"]
        self.assertEqual(["solution.py"], workspace["changed_files"])
        self.assertIn("solution.py", workspace["diff_stat"])

    def test_one_character_tracked_name_is_reviewed_and_approved(self):
        first, idle = self.submit_unstaged_change("f")
        self.assertNotIn("review_publication_error", idle)
        self.assertEqual(["f"], self.pending()[0]["workspace"]["changed_files"])
        self.assert_beta_reads_every_store(first)
        self.assertEqual("finalized", self.review(first, "approve", "approve-1")["state"])
        self.assert_beta_reads_every_store(first)

    def test_every_status_row_kind_keeps_its_whole_name(self):
        def tracked(worktree, *names):
            for name in names:
                (worktree / name).write_text("base\n")
            git(worktree, "add", "--", *names)
            git(worktree, "commit", "-m", "tracked")

        def modified_first(worktree):
            tracked(worktree, "with space.txt")
            (worktree / "with space.txt").write_text("changed\n")
            (worktree / "new file.txt").write_text("new\n")

        def deleted_first(worktree):
            (worktree / "f").unlink()

        def renamed(worktree):
            git(worktree, "mv", "f", "renamed name.txt")

        def renamed_after_unstaged(worktree):
            tracked(worktree, "a.txt")
            (worktree / "a.txt").write_text("changed\n")
            git(worktree, "mv", "f", "z renamed.txt")

        def renamed_then_edited(worktree):
            git(worktree, "mv", "f", "renamed name.txt")
            (worktree / "renamed name.txt").write_text("base\nedited\n")

        cases = {
            "modified tracked name with a space, then untracked": (
                modified_first, ["with space.txt", "new file.txt"]),
            "deleted tracked one-character name": (deleted_first, ["f"]),
            "staged rename": (renamed, ["f -> renamed name.txt"]),
            "unstaged change before a staged rename": (
                renamed_after_unstaged, ["a.txt", "f -> z renamed.txt"]),
            # Status "RM": the source path is still the second half of one row.
            "staged rename, then edited": (renamed_then_edited, ["f -> renamed name.txt"]),
        }
        for index, (label, (change, expected)) in enumerate(cases.items()):
            with self.subTest(label):
                seen = self.observed(change, "lease-names-" + str(index))
                self.assertEqual(expected, seen["changed_files"])
                self.assertEqual(len(expected), seen["changed_files_total"])
                self.assertTrue(seen["dirty"])

    def test_digests_stay_what_the_installed_beta_computes(self):
        """A stored digest is compared by whichever build decides the review.

        The beta recomputes digests from its own (truncated) names, so the fixed
        names must not change the digest of the same worktree.
        """
        beta = beta_module("work_parallel_workspace")

        def unstaged(worktree):
            (worktree / "solution.py").write_text("changed\n")

        def renamed_then_edited(worktree):
            git(worktree, "mv", "f", "renamed.txt")
            (worktree / "renamed.txt").write_text("base\nedited\n")

        (self.repo / "solution.py").write_text("base\n")
        git(self.repo, "add", "solution.py")
        git(self.repo, "commit", "-m", "add solution.py")
        cases = {"unstaged": (unstaged, ["solution.py"], ["olution.py"]),
                 # The beta reads the rename source as a row of its own.
                 "renamed then edited": (renamed_then_edited, ["f -> renamed.txt"], ["renamed.txt", ""])}
        for label, (change, fixed, truncated) in cases.items():
            with self.subTest(label):
                now = self.observed(change, "lease-" + label[:7])
                then = beta.salvage_observe(self.repo, "lease-" + label[:7], generation="1",
                                            terminal=True, slot=False, config_dir=self.tmp / "config")
                self.assertEqual((fixed, truncated), (now["changed_files"], then["changed_files"]))
                self.assertEqual(then["receipt_digest"], now["receipt_digest"])

    def test_row_published_with_a_truncated_name_is_not_refused_on_republication(self):
        """A review row written before the fix keeps working after the upgrade."""
        first, _ = self.submit_unstaged_change("solution.py")
        path = self.tmp / "work-parallel-finalization.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["reviews"][first]["workspace"]["changed_files"] = ["olution.py"]
        path.write_text(json.dumps(state), encoding="utf-8")
        again = self.idle(first)
        self.assertNotIn("review_publication_error", again)
        self.assertEqual("finalized", self.review(first, "approve", "approve-1")["state"])


if __name__ == "__main__":
    unittest.main()
