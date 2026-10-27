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

    def test_waiting_active_can_select_exact_queued_candidate_with_cas_and_new_approval(self):
        doc = self.plan()
        root = doc["active_root"]
        self.assertFalse(self.call("work_execute")["error"])
        self.assertFalse(self.call("work_node_add", title="queue", children=[
            {"title": "queue phase", "children": [{"title": "queue step"}]}])["error"])
        queued = self.store.read()["nodes"][-3]["id"]
        for n in doc["nodes"]:
            if n["title"] in ("one", "two"):
                self.assertFalse(self.call("work_node_update", id=n["id"], waiting_on="review")["error"])
        before = self.store.read()
        stale = self.call("work_select", root_id=queued, expected_version=before["version"] - 1)
        self.assertTrue(stale["error"])
        self.assertEqual(self.store.read(), before)
        self.assertFalse(self.call("work_select", root_id=queued, expected_version=before["version"])["error"])
        after = self.store.read()
        self.assertEqual((after["active_root"], after["phase"]), (queued, "planning"))
        prior = next(n for n in after["nodes"] if n["id"] == root)
        self.assertEqual(prior["phase"], "executing")
        self.config["work_plan_approval_ask"] = True
        pending = self.call("work_execute")
        self.assertFalse(pending["error"], pending)
        self.assertIn("confirmation", pending["text"])
        self.assertEqual(self.store.read(), after)

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

    def test_hybrid_root_detail_scoped_refinement_and_priority_cas(self):
        doc = self.plan()
        root, phase = doc["active_root"], doc["nodes"][1]["id"]
        self.config["work_plan_approval_ask"] = True
        self.call("work_execute", user_confirmed=True)
        self.assertFalse(self.call("work_node_add", parent_id=phase, title="scoped", within_scope=True,
                                   children=[{"title": "verify scoped"}])["error"])
        self.assertEqual(self.store.read()["phase"], "executing")
        self.call("work_plan_set", goal="private queued title", note="PRIVATE-QUEUED-NOTE",
                  children=[{"title": "private phase", "children": [{"title": "PRIVATE-QUEUED-STEP"}]}])
        queued = next(n["id"] for n in self.store.read()["nodes"] if n["title"] == "private queued title")
        before = self.store.read()
        only = self.call("work_status", root_id=root, format="json", detail=True)
        self.assertNotIn("PRIVATE-QUEUED", only["text"])
        self.assertEqual({n["id"] for n in json.loads(only["text"])["nodes"]},
                         {n["id"] for n in before["nodes"] if n["title"] != "private queued title"
                          and not n["title"].startswith("private phase") and n["title"] != "PRIVATE-QUEUED-STEP"})
        self.assertEqual(self.store.read(), before)
        self.assertTrue(self.call("work_status", root_id="missing", format="json")["error"])
        self.assertTrue(self.call("work_select", root_id=queued)["error"])
        self.assertTrue(self.call("work_select", root_id=queued, user_requested=True,
                                  expected_version=before["version"] - 1)["error"])
        self.assertEqual(self.store.read(), before)
        self.assertFalse(self.call("work_select", root_id=queued, user_requested=True,
                                   expected_version=before["version"])["error"])
        self.assertEqual(self.store.read()["phase"], "planning")
        pending = self.call("work_execute")
        self.assertIn("confirmation", pending["text"])
        self.call("work_select", root_id=root, user_requested=True)
        self.assertEqual(self.store.read()["phase"], "executing")
        self.call("work_node_add", parent_id=phase, title="new scope")
        self.assertEqual(self.store.read()["phase"], "planning")
        self.assertIn("confirmation", self.call("work_execute")["text"])

    def test_expected_version_rejects_coercion(self):
        self.plan()
        before = self.store.read()
        for value in (True, False, 1.0, "1", -1):
            self.assertTrue(self.call("work_node_add", title="invalid version", expected_version=value)["error"])
            self.assertEqual(self.store.read(), before)

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


class WorkContinuationMcpTests(unittest.TestCase):
    # Reuse the temporary store helpers, without inheriting the original tests.
    setUp = WorkMcpToolsTests.setUp
    call = WorkMcpToolsTests.call
    plan = WorkMcpToolsTests.plan

    def status(self):
        response = self.call("work_status", format="json", detail=True)
        self.assertFalse(response["error"], response)
        return json.loads(response["text"])

    def test_new_schemas_have_fields_guards_version_and_exact_session(self):
        schemas = {s["name"]: s["inputSchema"] for s in m.work_tool_schemas()}
        for name in ("work_plan_set", "work_node_add", "work_node_update", "work_urgent"):
            props = schemas[name]["properties"]
            self.assertEqual(props["ref"], {"type": "string", "maxLength": m.tree.MAX_REF_CHARS})
            self.assertEqual(props["expected_version"]["minimum"], 0)
            if name != "work_urgent":
                self.assertEqual(props["waiting_on"], {"type": "string", "maxLength": m.tree.MAX_WAIT_CHARS})
            if name != "work_node_update":
                nested = props["children"]["items"]
                self.assertEqual(nested["required"], ["title"])
                self.assertIn("ref", nested["properties"])
                self.assertIn("waiting_on", nested["properties"])
        reorder = schemas["work_reorder"]
        self.assertEqual(set(reorder["required"]), {"root_id", "position"})
        self.assertEqual(reorder["properties"]["position"], {"type": "integer", "minimum": 1})
        self.assertEqual(reorder["properties"]["user_requested"], {"type": "boolean"})
        self.assertEqual(set(schemas["work_urgent"]["required"]), {"goal", "user_requested"})
        for name in schemas:
            identity = schemas[name]["properties"]["session_id"]
            self.assertEqual(identity["pattern"], "^claude:[A-Za-z0-9_-]{1,200}$")
            self.assertNotIn("session_id", schemas[name].get("required", []))
        for name in ("work_reorder", "work_urgent"):
            self.assertIn("expected_version", schemas[name]["properties"])
            self.assertNotIn("urgent", schemas[name]["properties"])

    def test_ref_wait_fields_roundtrip_through_plan_add_update_replace(self):
        response = self.call("work_plan_set", goal="labels", ref=" R\n 1 ", waiting_on=" review\t peer ",
                             children=[{"title": "phase", "ref": " P\n2 ", "children": [
                                 {"title": "step", "ref": " S\n3 ", "waiting_on": " build\t4 "}]}])
        self.assertFalse(response["error"], response)
        snap = self.status()
        root = snap["active_root"]["id"]
        self.assertEqual((snap["active_root"]["ref"], snap["active_root"]["waiting_on"]), ("R 1", "review peer"))
        self.assertEqual(snap["breakdown"][0]["children"][0]["ref"], "S 3")
        self.assertEqual(snap["continuation"]["actionable"], 0)
        self.assertFalse(self.call("work_node_add", parent_id=root, title="added", ref=" A\n5 ", waiting_on=" peer\t6 ")["error"])
        added = self.store.read()["nodes"][-1]
        self.assertEqual((added["ref"], added["waiting_on"]), ("A 5", "peer 6"))
        self.assertFalse(self.call("work_node_update", id=root, ref="", waiting_on="")["error"])
        self.assertFalse(self.call("work_node_update", id=added["id"], ref="", waiting_on="")["error"])
        after = self.status()
        self.assertEqual((after["active_root"]["ref"], after["active_root"]["waiting_on"]), ("", ""))
        self.assertEqual(after["continuation"]["next"]["id"], added["id"])
        self.assertFalse(self.call("work_plan_set", root_id=root, goal="replacement", ref="new", waiting_on="new wait")["error"])
        self.assertFalse(self.call("work_plan_set", root_id=root, goal="cleared", ref="", waiting_on="")["error"])
        persisted = WorkPlanStore(self.store.path).read()
        self.assertEqual((persisted["nodes"][0]["ref"], persisted["nodes"][0]["waiting_on"]), ("", ""))
        self.assertEqual(persisted, self.store.read())

    def test_reorder_dispatch_version_success_noop_and_invalid_rollback(self):
        first = self.plan()["active_root"]
        self.call("work_node_add", title="second")
        second = self.store.read()["nodes"][-1]["id"]
        self.call("work_node_add", title="third")
        third = self.store.read()["nodes"][-1]["id"]
        self.call("work_execute")
        before = self.store.read()
        response = self.call("work_reorder", root_id=third, position=2, expected_version=before["version"])
        self.assertFalse(response["error"], response)
        after = self.store.read()
        self.assertEqual((after["version"], after["active_root"], after["phase"]), (before["version"] + 1, first, "executing"))
        self.assertEqual([q["id"] for q in self.status()["queue"]], [third, second])
        self.assertFalse(self.call("work_reorder", root_id=third, position=2)["error"])
        self.assertEqual(self.store.read(), after)
        bytes_before = self.store.path.read_bytes()
        for position in (None, 0, -1, 4, True, False, 1.0, "2"):
            with self.subTest(position=position):
                response = self.call("work_reorder", root_id=third, position=position, user_requested=True)
                self.assertTrue(response["error"], response)
                self.assertIn("position", response["text"])
                self.assertEqual(self.store.read(), after)
                self.assertEqual(self.store.path.read_bytes(), bytes_before)
        for requested in (None, False, "true", 1):
            with self.subTest(user_requested=requested):
                response = self.call("work_reorder", root_id=second, position=1, user_requested=requested)
                self.assertTrue(response["error"], response)
                self.assertIn("explicit user request", response["text"])
                self.assertEqual(self.store.read(), after)
        response = self.call("work_reorder", root_id=second, position=1, user_requested=True,
                             expected_version=before["version"])
        self.assertTrue(response["error"], response)
        self.assertIn("changed", response["text"])
        self.assertEqual(self.store.read(), after)
        self.assertFalse(self.call("work_reorder", root_id=second, position=1, user_requested=True,
                                   expected_version=after["version"])["error"])
        self.assertEqual((self.store.read()["active_root"], self.store.read()["phase"]), (second, "planning"))
        self.assertEqual([q["id"] for q in self.status()["queue"]], [first, third])

    def test_urgent_dispatch_guards_stale_version_and_partial_spec_rollback(self):
        self.plan()
        before = self.store.read()
        contents = self.store.path.read_bytes()
        cases = [({"goal": "urgent", "user_requested": value}, "explicit user request")
                 for value in (None, False, "true", 1)]
        cases += [({"goal": "urgent", "user_requested": True, "expected_version": 0}, "changed"),
                  ({"goal": "urgent", "user_requested": True, "children": [{"title": "allocated"}, None]}, "object"),
                  ({"goal": "", "user_requested": True}, "empty")]
        for args, message in cases:
            with self.subTest(args=args):
                response = self.call("work_urgent", **args)
                self.assertTrue(response["error"], response)
                self.assertIn(message, response["text"])
                self.assertEqual(self.store.read(), before)
                self.assertEqual(self.store.path.read_bytes(), contents)
        response = self.call("work_urgent", goal="urgent", ref=" U\n1 ", user_requested=True,
                             expected_version=before["version"], children=[
                                 {"title": "phase", "children": [{"title": "verify", "ref": "step"}]}])
        self.assertFalse(response["error"], response)
        self.assertIn("held until it finishes", response["text"])
        state = self.status()
        self.assertEqual((state["version"], state["phase"], state["active_root"]["ref"], state["active_root"]["urgent"]),
                         (before["version"] + 1, "planning", "U 1", True))
        self.assertEqual(state["queue"][0]["id"], before["active_root"])
        self.assertTrue(state["queue"][0]["held"])
        self.assertFalse(self.call("work_execute")["error"])
        leaf = state["continuation"]["next"]["id"]
        response = self.call("work_node_update", id=leaf, status="done", evidence="verified")
        self.assertFalse(response["error"], response)
        self.assertIn("next queued goal", response["text"])
        self.assertEqual((self.status()["active_root"]["id"], self.status()["phase"]), (before["active_root"], "planning"))
        self.assertEqual(self.status()["continuation"]["held"], 0)

    def test_auto_continue_json_is_live_config_and_status_read_is_not_a_write(self):
        self.plan()
        self.call("work_execute")
        before = self.store.read()
        contents = self.store.path.read_bytes()
        for config, expected in (({}, True), ({"work_auto_continue": False}, False),
                                 ({"work_auto_continue": True}, True), ({"work_auto_continue": "false"}, True)):
            with self.subTest(config=config):
                self.config = config
                state = self.status()
                self.assertIs(state["auto_continue"], expected)
                self.assertEqual(state["continuation"]["actionable"], 2)
                self.assertEqual(state["phase"], "executing")
                self.assertEqual(self.store.read(), before)
                self.assertEqual(self.store.path.read_bytes(), contents)

    def test_real_dispatch_new_tools_and_continuation_are_exact_session_scoped_and_off_is_readless(self):
        def call(identity, name, metadata=None, **args):
            return m.dispatch_work_tool(8, name, {"session_id": identity, **args}, metadata=metadata,
                load_config=lambda: self.config, state_dir=self.directory,
                respond=lambda request_id, text, is_error=False: {"text": text, "error": is_error})
        a, b = "claude:continuation-a", "claude:continuation-b"
        for identity, title in ((a, "private A"), (b, "private B")):
            self.assertFalse(call(identity, "work_plan_set", goal=title, waiting_on="review")["error"])
            self.assertFalse(call(identity, "work_node_add", title=title + " queue")["error"])
        b_before = call(b, "work_status", format="json", detail=True)["text"]
        self.assertFalse(call(a, "work_reorder", root_id="n2", position=1, user_requested=True)["error"])
        self.assertFalse(call(a, "work_urgent", goal="A urgent", user_requested=True)["error"])
        a_state = json.loads(call(a, "work_status", format="json")["text"])
        self.assertEqual(a_state["continuation"]["next"]["title"], "A urgent")
        self.assertEqual(a_state["continuation"]["held"], 2)
        self.assertEqual(call(b, "work_status", format="json", detail=True)["text"], b_before)
        # Native conversation metadata wins for both of the new mutation tools.
        self.assertFalse(call(a, "work_urgent", metadata={"sessionId": "native-new"},
                              goal="native urgent", user_requested=True)["error"])
        self.assertFalse(call(a, "work_reorder", metadata={"sessionId": "native-new"},
                              root_id="n1", position=1)["error"])
        self.assertEqual(json.loads(call(a, "work_status", format="json")["text"]), a_state)
        for identity in (None, "", "claude:../escape"):
            for name, args in (("work_reorder", {"root_id": "n1", "position": 1}),
                               ("work_urgent", {"goal": "bad", "user_requested": True})):
                with self.subTest(identity=identity, name=name):
                    self.assertTrue(call(identity, name, **args)["error"])
        files = {p: p.read_bytes() for p in self.directory.rglob("work-plan.json")}
        self.config = {"work_mode": False, "work_auto_continue": True}
        for identity in (a, None):
            response = call(identity, "work_status", format="json", detail=True)
            self.assertFalse(response["error"], response)
            state = json.loads(response["text"])
            self.assertIs(state["enabled"], False)
            self.assertIsNone(state["active_root"])
            self.assertEqual(state["continuation"]["actionable"], 0)
            self.assertNotIn("private", response["text"])
            self.assertTrue(call(identity, "work_urgent", goal="off", user_requested=True)["error"])
            self.assertTrue(call(identity, "work_reorder", root_id="n1", position=1)["error"])
        self.assertEqual({p: p.read_bytes() for p in self.directory.rglob("work-plan.json")}, files)


if __name__ == "__main__":
    unittest.main()
