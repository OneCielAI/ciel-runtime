"""Approval workflow: QA evidence reports, per-QA main review, rework, escalation, completion.

Real ledger, coordination and finalization stores in a temp config directory
(the MainReviewTests fixture of test_work_parallel_finalization), a real git
workspace per fork, and a workflow goal approved through the host-verified path.
"""
import hashlib
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support import work_workflow as wf
from ciel_runtime_support.work_parallel import WorkParallelError, WorkParallelStore
from ciel_runtime_support.work_plan_store import WorkPlanStore
from ciel_runtime_support.work_workflow_approval import verify
from ciel_runtime_support.work_workflow_tools import attach_packet, dispatch, review_apply

SECRET = "workflow-host-secret-" * 3
SCOPE = "claude:workflow-review"
LAUNCH_HOST = {"internal": True, "launch_nonce": "process-1"}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def proposal(qa=("QA-1", "QA-2")):
    return {"request_restated": "export", "analysis": "a", "alternatives": "b", "risks": "c", "spec": "d",
            "ui_impact": {"changes": False, "detail": "no UI"},
            "qa": [{"id": q, "claim": "c", "method": "command", "how": "pytest", "expected": "0", "evidence": "log",
                    "must_fail_before": False} for q in qa],
            "steps": [{"id": "S-1", "title": "implement", "input": "i", "procedure": "p", "done_when": "d",
                       "qa": list(qa)}]}


class WorkflowReviewTests(unittest.TestCase):
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
        self.board = WorkPlanStore(self.tmp / "work-sessions" / "s" / "work-plan.json")
        self.store = WorkParallelStore(self.tmp / "work-parallel.json")
        self.config = {"work_parallel_host": {"token": SECRET, "capability_ready": True},
                       "work_workflow": {"enabled": True}}
        runtime_patch = patch("ciel_runtime_support.runtime_paths.CONFIG_DIR", self.tmp / "config")
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)
        ledger_patch = patch.object(m, "ledger", return_value=self.store)
        ledger_patch.start()
        self.addCleanup(ledger_patch.stop)
        from work_owner_fixture import attach_hosts, bind_router
        bind_router(self, SCOPE)
        attach_hosts(self.store, SCOPE, "process-1", "process-2", "process-3", "process-4", "process-5")
        self.root = self.approved_goal()
        self.item = next(n["id"] for n in self.board.read()["nodes"] if n.get("ref") == "wf:" + self.root + ":S-1")

    def approved_goal(self):
        doc, root = self.board.transaction(lambda d: tree.add_node(d, None, "CSV export", priority="P1"))
        self.board.transaction(lambda d: wf.start(d, root, receipt_id="r" * 64, priority="P1"))
        dispatch("work_proposal_submit", {"root_id": root, "proposal": proposal()}, self.board, self.tmp, SCOPE,
                 self.config, None, host_verified=False)
        dispatch("work_approval_requested", {"root_id": root, "conversation_id": "c-1", "message_id": "77",
                                             "approvers": ["u1"], "proposal_version": 1}, self.board, self.tmp,
                 SCOPE, self.config, None, host_verified=False)
        record = wf.record(self.board.read(), root)
        source = {"source_context": "verified_thread", "conversation_id": "c-1", "root_id": "77",
                  "messages": [{"id": "77", "text": record["proposal"]["code"], "approvers": ["u1"],
                                "approvals": [{"user_id": "u1", "decision": "approved", "superseded_by": None,
                                               "withdrawn_at": None}]}]}
        verdict = verify(record, event=None, thread=source)
        self.assertEqual("approved", verdict["decision"])

        def approve(doc):
            wf.apply_decision(doc, root, verdict)
            tree.set_phase(doc, tree.PHASE_EXECUTING, root, workflow_approved=True)
        self.board.transaction(approve)
        return root

    def call(self, name, host=None, **args):
        if name != "item_review":
            args.setdefault("expected_version", self.store.read()["version"])
        args["_host"] = {"token": SECRET, **(host or {})}
        return json.loads(m.run("work_parallel_" + name, args, self.board, SCOPE, self.config, None))

    def report(self, lease_id, token, plan_version, workspace, *, detail=None, files=None, artifact="a" * 64):
        evidence = workspace / ".work-evidence" / lease_id
        evidence.mkdir(parents=True, exist_ok=True)
        log = evidence / "pytest.log"
        log.write_text("2 passed " + lease_id, encoding="utf-8")
        sha = hashlib.sha256(log.read_bytes()).hexdigest()
        rel = ".work-evidence/" + lease_id + "/pytest.log"
        body = detail if detail is not None else {
            "summary": "done", "changes": "export.py", "deviations": "", "open_issues": "",
            "evidence_files": files if files is not None else [{"path": rel, "kind": "log", "sha256": sha}],
            "qa_results": [{"id": q, "status": "pass", "command": "pytest", "exit_code": 0, "actual": "2 passed",
                            "evidence_files": [rel]} for q in ("QA-1", "QA-2")]}
        return json.loads(m.report({"lease_id": lease_id, "worker_token": token, "event": "completion",
                                    "plan_version": plan_version, "artifact": artifact, "evidence": "tests pass",
                                    "detail": body, "usage": {"model": "cheap-model", "tokens": 1200,
                                                              "seconds": 30}}, self.board, self.config))

    def started(self, worker="worker-1"):
        reserve = self.call("event", {"fork": True, "workspace_repo": str(self.repo)}, event="reserve",
                            item_id=self.item, priority="P1")
        self.call("event", {"host_id": worker}, event="bind", lease_id=reserve["lease_id"])
        return reserve, Path(reserve["workspace"]["path"])

    def idle(self, lease_id):
        self.call("event", {"state": "idle", "proof": "native completed"}, event="reconcile", lease_id=lease_id)

    def review(self, lease_id, decision, decision_id, **args):
        version = self.call("status", {})["review"]["version"]
        return self.call("item_review", args.pop("_host", None), lease_id=lease_id, decision=decision,
                         decision_id=decision_id, evidence="main read the log",
                         expected_finalization_version=version, **args)

    def verdicts(self, verdict="pass"):
        return [{"id": "QA-1", "verdict": verdict, "note": "log read"}, {"id": "QA-2", "verdict": "pass", "note": "ok"}]

    def packet(self):
        return {"failed_qa": ["QA-1"], "observed": "CSV header missing", "fix": "write the header",
                "evidence_required": "pytest log with the header test"}

    def rework(self, lease_id, decision_id, worker):
        result = self.review(lease_id, "rework", decision_id, qa_verdicts=self.verdicts("fail"), rework=self.packet())
        replacement = result["dispatch"]["lease_id"]
        dispatched = self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)
        name = "resume-" + decision_id
        self.call("event", LAUNCH_HOST, event="rework_launch_intent", lease_id=replacement, dispatch_name=name,
                  generation=self.store.read()["leases"][replacement]["rework"] + 1)
        self.call("event", {"host_id": worker, "agent_name": name}, event="bind", lease_id=replacement)
        return replacement, dispatched

    def test_ac6_report_without_evidence_is_refused_and_with_evidence_moves_to_review(self):
        reserve, workspace = self.started()
        lease, token, plan = reserve["lease_id"], reserve["worker_token"], reserve["plan_version"]
        self.assertEqual("executing", wf.record(self.board.read(), self.root)["state"])
        refusals = {
            "no detail": dict(detail={"summary": "done"}),
            "no evidence files": dict(files=[]),
            "missing file": dict(files=[{"path": "nope.log", "kind": "log", "sha256": "0" * 64}]),
        }
        for name, kw in refusals.items():
            with self.subTest(name), self.assertRaises(WorkParallelError) as raised:
                self.report(lease, token, plan, workspace, **kw)
            self.assertIn("workflow report refused", str(raised.exception))
        with self.assertRaises(WorkParallelError) as raised:
            self.report(lease, token, plan, workspace, files=[{"path": ".work-evidence/" + lease + "/pytest.log",
                                                              "kind": "log", "sha256": "1" * 64}])
        self.assertIn("sha256", str(raised.exception))
        missing_qa = {"summary": "s", "changes": "c", "evidence_files": [], "qa_results": []}
        with self.assertRaises(WorkParallelError):
            self.report(lease, token, plan, workspace, detail=missing_qa)
        self.assertEqual("running", self.store.read()["leases"][lease]["state"])
        # An honest fail/not_run with evidence is accepted and goes to review.
        done = self.report(lease, token, plan, workspace)
        self.assertEqual("submitted", done["state"])
        record = wf.record(self.board.read(), self.root)
        self.assertEqual("awaiting_review", record["state"])
        self.assertEqual({"QA-1": "pass", "QA-2": "pass"}, record["steps"][self.item]["reports"][-1]["qa"])
        stored = list((self.board.path.parent / "workflow" / self.root / "evidence" / lease).iterdir())
        self.assertEqual(1, len(stored))

    def test_ac7_rework_then_approve_completes_once_and_the_fork_gets_the_packet(self):
        from ciel_runtime_support.work_workflow_tools import status
        reserve, workspace = self.started()
        lease = reserve["lease_id"]
        self.report(lease, reserve["worker_token"], reserve["plan_version"], workspace)
        # Main is offered the review only once the ledger published it (fork idle);
        # before that work_parallel_item_review is refused (E2E 2026-11-18).
        self.assertNotIn("next_for_main", status(self.board, self.tmp, SCOPE, self.config))
        self.idle(lease)
        offered = status(self.board, self.tmp, SCOPE, self.config)["next_for_main"]
        self.assertEqual((self.root, "awaiting_review"), (offered["root_id"], offered["state"]))
        # A review without per-QA verdicts, or a vague rework, is refused.
        with self.assertRaises(WorkParallelError):
            self.review(lease, "approve", "a0")
        with self.assertRaises(WorkParallelError):
            self.review(lease, "rework", "r0", qa_verdicts=self.verdicts("fail"))
        with self.assertRaises(WorkParallelError) as raised:
            self.review(lease, "approve", "a1", qa_verdicts=self.verdicts("fail"), evidence_checked=True)
        self.assertIn("QA-1", str(raised.exception))
        replacement, dispatched = self.rework(lease, "r1", "worker-2")
        self.assertEqual("rework", wf.record(self.board.read(), self.root)["state"])
        packet = json.loads(attach_packet(json.dumps(dispatched), self.board, self.tmp))["workflow"]
        self.assertEqual("CSV header missing", packet["rework"]["observed"])
        self.assertIn("QA-1", packet["rework"]["instructions"])
        self.assertEqual(["QA-1", "QA-2"], [q["id"] for q in packet["qa"]])
        self.assertEqual("S-1", packet["step"]["id"])
        self.report(replacement, dispatched["worker_token"], dispatched["plan_version"], workspace, artifact="b" * 64)
        self.idle(replacement)
        self.assertEqual("awaiting_review", wf.record(self.board.read(), self.root)["state"])
        with self.assertRaises(WorkParallelError) as raised:
            self.review(replacement, "approve", "a2", qa_verdicts=self.verdicts())
        self.assertIn("evidence_checked", str(raised.exception))
        final = self.review(replacement, "approve", "a3", qa_verdicts=self.verdicts(), evidence_checked=True)
        self.assertEqual("finalized", final["state"])
        record = wf.record(self.board.read(), self.root)
        self.assertEqual("completed", record["state"])
        self.assertEqual({"state": "due"}, {k: v for k, v in record["final_report"].items() if k == "state"})
        self.assertEqual(1, sum(1 for h in record["history"] if h["to"] == "completed"))
        metrics = wf.metrics(self.board.read())["models"][0]
        self.assertEqual(("cheap-model", 1, 0, 1, 1200 * 2), (metrics["model"], metrics["steps"], metrics["first_pass"],
                                                              metrics["approved"], metrics["tokens"]))
        self.assertEqual(1.0, metrics["final_pass_rate"])

    def test_ac8_rework_limit_escalates_and_main_chooses_another_model(self):
        reserve, workspace = self.started()
        lease = reserve["lease_id"]
        self.report(lease, reserve["worker_token"], reserve["plan_version"], workspace)
        self.idle(lease)
        current = lease
        for index, worker in enumerate(("worker-2", "worker-3"), 1):
            current, dispatched = self.rework(current, "r%d" % index, worker)
            self.report(current, dispatched["worker_token"], dispatched["plan_version"], workspace,
                        artifact=str(index) * 64)
            self.idle(current)
        escalated = self.review(current, "rework", "r3", qa_verdicts=self.verdicts("fail"), rework=self.packet())
        self.assertEqual("escalated", escalated["decision"])
        self.assertEqual("escalated", wf.record(self.board.read(), self.root)["state"])
        todo = wf.todo(self.board.read())
        self.assertEqual("escalated", todo[0]["state"])
        resolved = dispatch("work_workflow_resolve", {"root_id": self.root, "action": "escalate_model",
                                                      "model": "strong-model"}, self.board, self.tmp, SCOPE,
                            self.config, None, host_verified=False)
        self.assertEqual("awaiting_review", resolved["state"])
        self.assertEqual(1, resolved["rework_grant"]["granted"])
        replacement, dispatched = self.rework(current, "r4", "worker-4")
        projected = m.project(self.store, SCOPE, self.board.read(), config=self.config)
        self.assertEqual("strong-model", projected["fork_models"]["overrides"][replacement]["id"])
        packet = json.loads(attach_packet(json.dumps(dispatched), self.board, self.tmp))["workflow"]
        self.assertEqual("strong-model", packet["model"])

    def test_ac8_escalation_main_finishes_it_itself(self):
        reserve, workspace = self.started()
        lease = reserve["lease_id"]
        self.report(lease, reserve["worker_token"], reserve["plan_version"], workspace)
        self.idle(lease)
        self.store.update_policy = None
        current = lease
        for index, worker in enumerate(("worker-2", "worker-3"), 1):
            current, dispatched = self.rework(current, "r%d" % index, worker)
            self.report(current, dispatched["worker_token"], dispatched["plan_version"], workspace,
                        artifact=str(index) * 64)
            self.idle(current)
        self.review(current, "rework", "r3", qa_verdicts=self.verdicts("fail"), rework=self.packet())
        dispatch("work_workflow_resolve", {"root_id": self.root, "action": "escalate_main"}, self.board, self.tmp,
                 SCOPE, self.config, None, host_verified=False)
        final = self.review(current, "approve", "a-main", qa_verdicts=self.verdicts(), evidence_checked=True)
        self.assertEqual("finalized", final["state"])
        self.assertEqual("completed", wf.record(self.board.read(), self.root)["state"])

    def test_ac12_review_fork_on_mains_model_never_the_implementer(self):
        reserve, workspace = self.started(worker="worker-1")
        lease = reserve["lease_id"]
        self.report(lease, reserve["worker_token"], reserve["plan_version"], workspace)
        self.idle(lease)
        dispatch("work_workflow_delegate", {"root_id": self.root, "role": "review"}, self.board, self.tmp, SCOPE,
                 self.config, None, host_verified=False)
        from ciel_runtime_support.work_workflow_tools import delegated_event, delegations_due
        offered = delegations_due(self.board, self.tmp, self.config, now=1e9)
        self.assertEqual(("review", self.item, lease), (offered[0]["role"], offered[0]["item_id"], offered[0]["lease_id"]))
        self.assertIn("review fork", offered[0]["prompt"])
        # The implementer cannot be the review fork.
        delegated_event({"root_id": self.root, "role": "review", "state": "bound", "agent_id": "worker-1"},
                        self.board, self.tmp)
        with self.assertRaises(WorkParallelError):
            self.review(lease, "approve", "fork-a", qa_verdicts=self.verdicts(), evidence_checked=True,
                        workflow_review_fork="worker-1")
        # A separate fork reviews it; the record names it and main's model.
        self.board.transaction(lambda d: wf.record(d, self.root).update(
            delegation={**wf.record(d, self.root)["delegation"], "state": "requested"}))
        delegations_due(self.board, self.tmp, self.config, now=2e9)
        delegated_event({"root_id": self.root, "role": "review", "state": "bound", "agent_id": "reviewer-9"},
                        self.board, self.tmp)
        final = self.review(lease, "approve", "fork-b", qa_verdicts=self.verdicts(), evidence_checked=True,
                            workflow_review_fork="reviewer-9")
        self.assertEqual("finalized", final["state"])
        reviews = wf.record(self.board.read(), self.root)["steps"][self.item]["reviews"]
        self.assertEqual({"kind": "review_fork", "id": "reviewer-9", "model": "main"}, reviews[-1]["reviewer"])

    def test_review_apply_validates_without_writing(self):
        before = self.board.read()["version"]
        with self.assertRaises(tree.WorkPlanError):
            review_apply(self.board, self.root, {"id": "x", "item_id": self.item}, "approve", {}, max_rework=2,
                         dry_run=True)
        self.assertEqual(before, self.board.read()["version"])


if __name__ == "__main__":
    unittest.main()
