import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from ciel_runtime_support import work_mcp_tools as m
from ciel_runtime_support.channel_mcp_tools import channel_mcp_tool_schemas
from ciel_runtime_support.work_plan_store import WorkPlanStore


class WorkMcpToolsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="work-mcp-test-")
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.store = WorkPlanStore(self.directory / "work-plan.json")
        self.config = {}

    def call(self, name, **args):
        def respond(request_id, text, is_error=False):
            return {"id": request_id, "text": text, "error": is_error}
        args.setdefault("session_id", "claude:test-session")
        return m.dispatch_work_tool(7, name, args, load_config=lambda: self.config,
                                   state_dir=self.directory, respond=respond, store=self.store)

    def plan(self):
        response = self.call("work_plan_set", goal="goal", children=[
            {"title": "phase", "children": [{"title": "one"}, {"title": "two"}]}])
        self.assertFalse(response["error"], response)
        return self.store.read()

    def test_real_dispatch_isolates_conversations_and_preserves_resume(self):
        def call(session_id, name, **args):
            return m.dispatch_work_tool(7, name, {"session_id": session_id, **args},
                load_config=lambda: {}, state_dir=self.directory,
                respond=lambda request_id, text, is_error=False: {"text": text, "error": is_error})
        a = "claude:conversation-a"
        b = "claude:conversation-b"
        self.assertFalse(call(a, "work_plan_set", goal="A private goal", children=[
            {"title": "phase", "children": [{"title": "A step"}]}])["error"])
        self.assertFalse(call(a, "work_execute")["error"])
        snap_a = json.loads(call(a, "work_status", format="json")["text"])
        snap_b = json.loads(call(b, "work_status", format="json")["text"])
        self.assertEqual(snap_a["phase"], "executing")
        self.assertIsNone(snap_b["active_root"])
        self.assertEqual(snap_b["phase"], "planning")
        self.assertTrue(call(b, "work_node_remove", id=snap_a["active_root"]["id"])["error"])
        self.assertEqual(json.loads(call(a, "work_status", format="json")["text"]), snap_a)
        for identity in (None, "", "claude:../escape"):
            self.assertTrue(call(identity, "work_status", format="json")["error"])

    def test_codex_native_metadata_overrides_model_session_id(self):
        def call(metadata, name, **args):
            return m.dispatch_work_tool(7, name, args, metadata=metadata,
                load_config=lambda: {}, state_dir=self.directory,
                respond=lambda request_id, text, is_error=False: {"text": text, "error": is_error})
        self.assertFalse(call({"sessionId": "root-a", "threadId": "child-a"}, "work_plan_set",
                              goal="native A", session_id="claude:forged")["error"])
        a = json.loads(call({"sessionId": "root-a", "threadId": "other-child"}, "work_status", format="json")["text"])
        b = json.loads(call({"sessionId": "root-b"}, "work_status", format="json")["text"])
        self.assertEqual(a["active_root"]["title"], "native A")
        self.assertIsNone(b["active_root"])
        for metadata, args in (({}, {"session_id": "codex:root-a"}),
                               ({"sessionId": ""}, {"session_id": "claude:forged"}),
                               ({"sessionId": "../escape"}, {})):
            self.assertTrue(call(metadata, "work_status", **args)["error"])

    def test_schema_on_off(self):
        expected = set(m.WORK_TOOL_NAMES)
        self.assertEqual({s["name"] for s in m.work_tool_schemas()}, expected)
        self.assertEqual(len(m.work_tool_schemas()), len(expected))
        for config, enabled in (({}, True), ({"work_mode": True}, True), ({"work_mode": False}, False)):
            with self.subTest(config=config):
                names = {s["name"] for s in channel_mcp_tool_schemas(lambda: config)}
                self.assertEqual(names & expected, expected if enabled else set())
        self.assertFalse({s["name"] for s in channel_mcp_tool_schemas()} & expected)

    def test_off_never_accesses_store(self):
        self.config = {"work_mode": False}
        self.store = Mock()
        for name in m.WORK_TOOL_NAMES:
            self.assertTrue(self.call(name)["error"])
        response = self.call("work_status", format="json")
        self.assertFalse(response["error"])
        self.assertFalse(json.loads(response["text"])["enabled"])
        self.assertEqual(self.store.mock_calls, [])

    def test_json_status(self):
        doc = self.plan()
        response = self.call("work_status", format="json")
        snap = json.loads(response["text"])
        self.assertEqual(response["id"], 7)
        self.assertEqual((snap["enabled"], snap["phase"], snap["approval"]), (True, "planning", "auto"))
        self.assertEqual((snap["version"], snap["total"], snap["done"]), (doc["version"], 2, 0))
        self.assertEqual(snap["next"]["path"], ["goal", "phase", "one"])
        self.assertIn("goal", self.call("work_status")["text"])

    def test_detail_is_opt_in_read_only_and_active_goal_only(self):
        doc = self.plan()
        root = doc["active_root"]
        leaf = doc["nodes"][-1]["id"]
        self.call("work_node_update", id=leaf, note="Inspect this", status="done", evidence="Verified output")
        self.call("work_node_add", title="other private goal", children=[{"title": "other step"}])
        self.call("work_select", root_id=root)
        before = self.store.read()
        self.assertNotIn("nodes", json.loads(self.call("work_status", format="json")["text"]))
        detail = json.loads(self.call("work_status", format="json", detail=True)["text"])
        self.assertEqual(len(detail["nodes"]), len(doc["nodes"]))
        node = next(n for n in detail["nodes"] if n["id"] == leaf)
        self.assertEqual((node["note"], node["evidence"]), ("Inspect this", "Verified output"))
        self.assertEqual(self.store.read(), before)
        # Detail nodes stay with the active goal; the queued goal appears only as a queue entry.
        self.assertNotIn("other", json.dumps(detail["nodes"]))
        self.assertEqual([q["title"] for q in detail["queue"]], ["other private goal"])

    def test_auto_execute_and_replan(self):
        self.plan()
        self.assertFalse(self.call("work_execute")["error"])
        self.assertEqual(self.store.read()["phase"], "executing")
        version = self.store.read()["version"]
        self.assertIn("already", self.call("work_execute")["text"])
        self.assertEqual(self.store.read()["version"], version)
        self.assertFalse(self.call("work_replan", reason="changed")["error"])
        self.assertEqual(self.store.read()["phase"], "planning")

    def test_ask_requires_literal_true(self):
        self.config = {"work_plan_approval_ask": True}
        self.plan()
        before = self.store.read()
        for confirmed in (None, False, "true", 1):
            with self.subTest(confirmed=confirmed):
                response = self.call("work_execute", user_confirmed=confirmed)
                self.assertFalse(response["error"], response)
                self.assertIn("confirmation", response["text"])
                self.assertEqual(self.store.read(), before)
        self.assertEqual(json.loads(self.call("work_status", format="json")["text"])["approval"], "ask")
        self.assertFalse(self.call("work_execute", user_confirmed=True)["error"])
        self.assertEqual(self.store.read()["phase"], "executing")

    def test_execute_rejects_empty_and_shallow(self):
        self.assertTrue(self.call("work_execute")["error"])
        self.call("work_plan_set", goal="shallow", children=[{"title": "phase"}])
        before = self.store.read()
        self.assertTrue(self.call("work_execute", user_confirmed=True)["error"])
        self.assertEqual(self.store.read(), before)

    def test_add_remove_update_completion(self):
        doc = self.plan()
        phase = doc["nodes"][1]["id"]
        response = self.call("work_node_add", parent_id=phase, title="three", children=[{"title": "nested"}])
        self.assertFalse(response["error"], response)
        added = self.store.read()["nodes"][-2]["id"]
        self.assertFalse(self.call("work_node_remove", id=added)["error"])
        self.assertEqual(len(self.store.read()["nodes"]), 4)
        self.call("work_execute")
        for leaf in doc["nodes"][2:]:
            response = self.call("work_node_update", id=leaf["id"], status="done", evidence="verified")
            self.assertFalse(response["error"], response)
        snap = json.loads(self.call("work_status", format="json")["text"])
        self.assertEqual((snap["phase"], snap["done"], snap["total"], snap["next"]), ("planning", 2, 2, None))
        self.assertIn("Every step", response["text"])
        self.assertTrue(self.call("work_execute")["error"])

    def test_replace_select(self):
        root = self.plan()["active_root"]
        self.call("work_node_add", title="other")
        self.assertFalse(self.call("work_select", root_id=root)["error"])
        self.assertFalse(self.call("work_plan_set", goal="revised", root_id=root,
                                  children=[{"title": "new"}])["error"])
        self.assertEqual(len(self.store.read()["nodes"]), 3)
        self.assertEqual(self.store.read()["active_root"], root)

    def test_independent_request_queues_and_activates_after_the_active_goal(self):
        doc = self.plan()
        root = doc["active_root"]
        self.assertFalse(self.call("work_execute")["error"])
        queued = self.call("work_plan_set", goal="2. next request",
                           children=[{"title": "p", "children": [{"title": "s"}]}])
        self.assertIn(f"queued behind the active goal {root}", queued["text"])
        added = self.call("work_node_add", title="3. later request")
        self.assertIn(f"queued behind the active goal {root}", added["text"])
        snap = json.loads(self.call("work_status", format="json")["text"])
        self.assertEqual((snap["active_root"]["id"], snap["phase"]), (root, "executing"))
        self.assertEqual([q["title"] for q in snap["queue"]], ["2. next request", "3. later request"])
        for leaf in doc["nodes"][2:]:
            response = self.call("work_node_update", id=leaf["id"], status="done", evidence="verified")
        self.assertIn("next queued goal", response["text"])
        snap = json.loads(self.call("work_status", format="json")["text"])
        self.assertEqual((snap["active_root"]["title"], snap["phase"]), ("2. next request", "planning"))
        self.assertEqual([q["title"] for q in snap["queue"]], ["3. later request"])

    def test_cas_errors_do_not_mutate(self):
        doc = self.plan()
        before = self.store.read()
        for name, args in (("work_plan_set", {"goal": "stale"}), ("work_node_add", {"title": "stale"}),
                           ("work_node_update", {"id": doc["active_root"], "title": "stale"}),
                           ("work_node_remove", {"id": doc["active_root"]})):
            with self.subTest(name=name):
                response = self.call(name, expected_version=0, **args)
                self.assertTrue(response["error"], response)
                self.assertIn("changed", response["text"])
                self.assertEqual(self.store.read(), before)

    def test_bad_requests_rollback(self):
        self.plan()
        before = self.store.read()
        for name, args in (("work_node_update", {"id": "missing", "status": "done"}),
                           ("work_node_remove", {"id": "missing"}), ("work_select", {"root_id": "missing"}),
                           ("work_node_add", {"title": "bad", "children": [None]}),
                           ("work_plan_set", {"goal": "bad", "expected_version": "nonsense"}), ("work_unknown", {})):
            with self.subTest(name=name):
                self.assertTrue(self.call(name, **args)["error"])
                self.assertEqual(self.store.read(), before)


if __name__ == "__main__":
    unittest.main()
