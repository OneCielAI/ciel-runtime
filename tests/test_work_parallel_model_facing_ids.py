"""Fork identities and capabilities in model-facing Work payloads (lead ruling).

Deliberate exception: a bound worker's agent id stays in model-facing
work_parallel_status. It is the addressing handle main needs for the one
message the revival gate allows to a Work fork, the coordinator resuming a
running or waiting lease. Everything else main reads stays free of agent ids
and names: the main-review rows (which the wake payload and the Stop-gate
reason are built from), work_status, and the mod's own review and handoff
views. No worker token or its hash reaches any model-facing view.
"""
import json
import re
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.work_parallel import hash_worker_token

import test_work_parallel_rework_worktree as rw

AGENT_ID = "AGENT-ID-REVIEWED-FORK"
AGENT_NAME = "work-name-reviewed-fork"
REGISTER = Path(__file__).resolve().parents[1] / "ciel_runtime_support/claude_mods/ciel-work/hooks/register.js"
FORBIDDEN_KEYS = ("agent_id", "agentId", "host_id", "agent_name", "dispatch_name", "worker_token", "report_hash")


class ModelFacingIdentityTests(rw.ReworkFixture):
    def reviewed(self):
        reserve = self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                            event="reserve", item_id=self.board.item, priority="P1")
        lease = reserve["lease_id"]
        self.call("event", {"host_id": AGENT_ID, "agent_name": AGENT_NAME}, event="bind", lease_id=lease)
        (Path(reserve["workspace"]["path"]) / "result.txt").write_text("result\n")
        self.report(lease, reserve["worker_token"], "a" * 64, "reviewed result")
        self.idle(lease)
        return lease, reserve["worker_token"]

    def model_status(self):
        """Exactly what the router serves a model tool call: no host token."""
        with patch.object(m, "ledger", return_value=self.store):
            return json.loads(m.run("work_parallel_status", {}, self.board, rw.SCOPE, self.config, None))

    def model_tool(self, name, args):
        with patch.object(m, "ledger", return_value=self.store):
            response = dispatch_work_tool(1, name, {**args, "session_id": rw.SCOPE},
                                          load_config=lambda: {"work_mode": True, **self.config},
                                          state_dir=self.tmp / "state", store=self.board.store,
                                          respond=lambda rid, text, error=False: {"text": text, "error": error},
                                          save_config=lambda value: None)
        self.assertFalse(response["error"], response)
        return response["text"]

    def test_review_rows_main_reads_carry_no_agent_id_or_name(self):
        lease, _ = self.reviewed()
        status = self.model_status()
        review = json.dumps(status["review"])
        self.assertIn(lease, review)
        self.assertNotIn(AGENT_ID, review)
        self.assertNotIn(AGENT_NAME, review)

    def test_work_status_carries_no_agent_id_or_name(self):
        self.reviewed()
        text = self.model_tool("work_status", {})
        self.assertNotIn(AGENT_ID, text)
        self.assertNotIn(AGENT_NAME, text)

    def test_the_deliberate_exception_is_only_the_worker_addressing_handle(self):
        lease, _ = self.reviewed()
        status = self.model_status()
        self.assertEqual(AGENT_ID, status["leases"][lease]["host_id"])

    def test_no_worker_token_or_its_hash_reaches_a_model_facing_view(self):
        lease, token = self.reviewed()
        replacement = self.review(lease, "rework", "rework-ids", evidence="fix it")["dispatch"]["lease_id"]
        rotated = self.call("event", {"fork": True}, event="rework_dispatch", lease_id=replacement)["worker_token"]
        views = [json.dumps(self.model_status()), self.model_tool("work_status", {}),
                 self.model_tool("work_parallel_status", {})]
        for secret in (token, hash_worker_token(token), rotated, hash_worker_token(rotated)):
            for view in views:
                self.assertNotIn(secret, view)
        self.assertNotIn("report_hash", views[0])
        self.assertNotIn("worker_token", views[0])


def function_body(source, name):
    match = re.search(r"\nfunction " + re.escape(name) + r"\(.*?\n}\n", source, re.DOTALL)
    assert match, name
    return match.group(0)


class ModFacingViewByteTests(rw.unittest.TestCase):
    """Byte checks of the mod's model-facing builders (register.js, read only)."""

    def test_review_wake_stop_reason_and_handoff_views_name_no_agent_fields(self):
        source = REGISTER.read_text(encoding="utf-8")
        for name in ("parallelReviewView", "parallelStranded", "parallelReviewText", "parallelReviewGate",
                     "handoffView"):
            with self.subTest(function=name):
                body = function_body(source, name)
                for key in FORBIDDEN_KEYS:
                    self.assertNotIn(key, body)
