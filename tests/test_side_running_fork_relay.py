"""Running-fork board visibility and direct side-to-fork handoff (no duplicate queue or side reply)."""
import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim, finish
from ciel_runtime_support.side_reply_outbox import prepare as prepare_reply
from ciel_runtime_support.side_work_decision import apply, validate
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.work_parallel import WorkParallelStore
from ciel_runtime_support.work_plan_store import session_work_plan_store
from ciel_runtime_support.work_workflow_prompts import DEFAULTS, fingerprint, set_prompt
from ciel_runtime_support.work_workflow_tools import side_instruction

IDENTITY = "claude:direct-fork-1"


def _walkie_event(message_id: str, text: str) -> str:
    return json.dumps({
        "type": "com.ciel.walkie.support.message",
        "data": {
            "conversation": "34886995-4a66-4527-a618-814e9cb0a5bf",
            "message_id": message_id,
            "event_type": "message.created",
            "text": text,
            "message_context": {"id": message_id, "thread_task": {"priority": 2}},
        },
    })


class SideRunningForkRelayTests(unittest.TestCase):
    def test_workload_and_work_status_expose_active_forks_without_secrets(self):
        from ciel_runtime_support.side_workload import project, project_active_forks

        doc = tree.empty_document()
        root = tree.add_node(doc, None, "API migration", priority="P1", refs=["thread-88"])
        step = tree.add_node(doc, root, "Update handler")
        other_root = tree.add_node(doc, None, "Other goal", priority="P2")
        parallel_doc = {
            "leases": {
                "lease-run": {
                    "id": "lease-run",
                    "scope": IDENTITY,
                    "item_id": step,
                    "priority": "P1",
                    "role": "worker",
                    "state": "running",
                    "host_id": "agent-live-1",
                    "worker_token": "secret-tok-must-never-appear",
                },
                "lease-done": {
                    "id": "lease-done",
                    "scope": IDENTITY,
                    "item_id": other_root,
                    "priority": "P2",
                    "role": "worker",
                    "state": "done",
                    "host_id": "agent-done-1",
                },
                "lease-foreign": {
                    "id": "lease-foreign",
                    "scope": "claude:other-session",
                    "item_id": step,
                    "priority": "P0",
                    "role": "worker",
                    "state": "running",
                    "host_id": "agent-foreign-1",
                },
            }
        }
        forks = project_active_forks(parallel_doc, doc, IDENTITY)
        self.assertEqual(1, len(forks), forks)
        row = forks[0]
        self.assertEqual(step, row["item_id"])
        self.assertEqual(root, row["root_id"])
        self.assertEqual("running", row["state"])
        self.assertEqual("agent-live-1", row["agent_id"])
        self.assertEqual(["thread-88"], row["refs"])
        self.assertNotIn("secret-tok-must-never-appear", json.dumps(forks))

        workload = project(doc, active_forks=forks)
        self.assertEqual(forks, workload["active_forks"])
        step_node = next(n for n in workload["nodes"] if n["id"] == step)
        root_node = next(n for n in workload["nodes"] if n["id"] == root)
        self.assertEqual("agent-live-1", step_node["active_fork"]["agent_id"])
        self.assertEqual("agent-live-1", root_node["active_fork"]["agent_id"])

        # work_status (both text and json) also exposes active_forks from the live parallel ledger
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            store = session_work_plan_store(state, IDENTITY)
            store.transaction(lambda d: (
                d.update(doc),
                d["version"],
            ))
            # Seed WorkParallelStore in same config dir (patched via work_parallel_mcp.ledger)
            from unittest.mock import patch
            pstore = WorkParallelStore(state / "work-parallel.json")
            pstore.configure({"enabled": True}, capability_ready=True, expected_version=pstore.read()["version"])
            base_cand = {
                "created_at": 100.0,
                "approved": True,
                "focused": False,
                "dependencies_done": True,
                "plan_version": doc["version"],
                "usage": 0,
                "role": "worker",
                "mode": "fork",
            }
            _, lease_id = pstore.reserve(
                [{**base_cand, "item_id": step, "priority": "P1"}],
                scope=IDENTITY, expected_version=pstore.read()["version"],
            )
            pstore.bind(lease_id, "agent-live-1", expected_version=pstore.read()["version"])
            # Also add a non-active (reserved, unbound) lease in the same session to confirm only bound active forks appear
            pstore.reserve(
                [{**base_cand, "item_id": other_root, "priority": "P2"}],
                scope=IDENTITY, expected_version=pstore.read()["version"],
            )

            def respond(req_id, text, is_error=False):
                return {"id": req_id, "text": text, "isError": is_error}

            with patch("ciel_runtime_support.work_parallel_mcp.ledger", return_value=pstore):
                as_json = dispatch_work_tool(
                    1, "work_status", {"session_id": IDENTITY, "format": "json"},
                    load_config=lambda: {"work_mode": True}, state_dir=state, respond=respond,
                )
                as_text = dispatch_work_tool(
                    2, "work_status", {"session_id": IDENTITY, "format": "text"},
                    load_config=lambda: {"work_mode": True}, state_dir=state, respond=respond,
                )
            self.assertFalse(as_json["isError"], as_json)
            parsed = json.loads(as_json["text"])
            self.assertEqual(1, len(parsed["active_forks"]))
            self.assertEqual("agent-live-1", parsed["active_forks"][0]["agent_id"])
            self.assertNotIn("secret-tok-must-never-appear", as_json["text"])
            self.assertFalse(as_text["isError"], as_text)
            self.assertIn("Active forks:", as_text["text"])
            self.assertIn("agent-live-1", as_text["text"])

    def test_relay_with_no_reply_marks_fork_responds_and_skips_side_reply(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inbox = SideIntakeInbox(root, IDENTITY)
            store = session_work_plan_store(root, IDENTITY)
            _, g1 = store.transaction(lambda d: tree.add_node(d, None, "Existing work", refs=["500"]))
            before_count = len(store.read()["nodes"])
            key = inbox.receive(
                _walkie_event("501", "Also handle empty payload case please"),
                "direct:501",
                route={"source_kind": "external_event", "channel": "external:default"},
            )["receipt_id"]
            leased = claim(root, IDENTITY, "side-1")
            decision = {
                "action": "update",
                "kind": "request",
                "item_id": g1,
                "summary": "Forwarded follow-up directly to running fork",
                "reply": "Handed off directly to running fork.",
                "note": "Requester added empty payload requirement",
                "refs": ["501"],
                "relay": True,
                "no_reply": True,
            }
            validate(decision, source_has_ids=True)
            self.assertIs(True, decision.get("no_reply"))
            self.assertIs(True, decision.get("relay"))
            result = apply(store, key, decision, store.read()["version"])
            self.assertEqual(before_count, len(store.read()["nodes"]))
            self.assertIs(True, result.get("no_reply"))
            self.assertEqual({"item_id": g1, "fork_responds": True}, result.get("relay"))
            finish(root, IDENTITY, key, leased["generation"], result)
            outbox = prepare_reply(root, IDENTITY, key, config={"work_side_intake": {"enabled": True}})
            self.assertEqual("not_applicable", outbox["state"])
            self.assertNotIn("arguments", outbox)

    def test_side_instruction_includes_managed_running_fork_relay_prompt(self):
        self.assertIn("side_running_fork_relay", DEFAULTS)
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            text = side_instruction({}, state, "")
            self.assertNotIn(DEFAULTS['side_running_fork_relay'], text)
            rule = DEFAULTS["side_running_fork_relay"]
            set_prompt(state, "side_running_fork_relay", "custom fork relay rule", fingerprint(rule))
            self.assertIn("custom fork relay rule", side_instruction({}, state, ""))


if __name__ == "__main__":
    unittest.main()
