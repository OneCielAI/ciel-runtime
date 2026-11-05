import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from ciel_runtime_support.work_plan_store import WorkPlanStore
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_side_intake import enqueue


class SideIntakeTests(unittest.TestCase):
    def test_explicit_sse_lane_overrides_default_without_changing_raw_body(self):
        import json
        from ciel_runtime_support.runtime_input_gateway import RuntimeInputGateway
        captured = []
        config = {'enabled': True, 'type': 'btw_with_mcp'}
        gateway = RuntimeInputGateway(lambda p: captured.append(p) or {'id': 1},
                                      load_side_settings=lambda: config)
        args = dict(receiver_id='fixture', transport='sse', event_id='one',
                    event_type='event', event_source='source')
        raw = json.dumps({'data': {'injection_type': 'session', 'text': 'request'}})
        gateway.submit_external_event(raw, **args)
        self.assertEqual(raw, captured[-1]['message'])
        self.assertFalse(captured[-1]['meta'].get('_side_intake_admitted', False))
        config['type'] = 'session'
        gateway.submit_external_event('request', injection_type='btw_with_mcp', **args)
        self.assertTrue(captured[-1]['meta']['_side_intake_admitted'])
        config['enabled'] = False
        gateway.submit_external_event('request', injection_type='btw_with_mcp', **args)
        self.assertEqual('request', captured[-1]['message'])
        with self.assertRaises(ValueError):
            gateway.submit_external_event('request', injection_type='unknown', **args)

    def test_durable_dedup_preserves_active_and_approval(self):
        with tempfile.TemporaryDirectory() as root:
            store = WorkPlanStore(Path(root) / 'work.json')
            before, active = store.transaction(lambda d: tree.add_node(d, None, 'main'))
            receipt = enqueue(store, 'external request', 'source:1')
            again = enqueue(WorkPlanStore(store.path), 'external request', 'source:1')
            self.assertEqual(receipt['item_id'], again['item_id'])
            self.assertTrue(again['duplicate'])
            self.assertFalse(receipt['execution_approved'])
            doc = store.read()
            self.assertEqual(active, doc['active_root'])
            self.assertEqual(before['phase'], doc['phase'])
            self.assertEqual(2, len(doc['nodes']))
            with self.assertRaisesRegex(tree.WorkPlanError, 'reused'):
                enqueue(store, 'different', 'source:1')

    def test_gateway_side_type_preserves_key_and_never_uses_router_body(self):
        from ciel_runtime_support.runtime_input_gateway import RuntimeInputGateway
        captured = []
        gateway = RuntimeInputGateway(lambda payload: captured.append(payload) or {"id": 1},
                                      load_side_settings=lambda: {"enabled": True})
        gateway._append({"message": "request", "channel": "test", "meta": {
            "message_type": "btw_with_mcp", "event_id": "one", "source": "source"}})
        payload = captured[0]
        self.assertTrue(payload["meta"]["raw_injection"])
        self.assertEqual("session_socket", payload["meta"]["input_transport"])
        self.assertTrue(payload["message"].startswith('/btw_with_mcp '))
        with self.assertRaises(ValueError):
            gateway._append({"message": "request", "meta": {"message_type": "btw_with_mcp"}})

    def test_disabled_default_preserves_original_sse_then_live_enable_converts(self):
        from ciel_runtime_support.runtime_input_gateway import RuntimeInputGateway
        captured, settings = [], {}
        gateway = RuntimeInputGateway(lambda payload: captured.append(payload) or {"id": 1},
                                      load_side_settings=lambda: settings)
        args = dict(receiver_id="default", transport="sse", event_id="id1",
                    event_type="btw_with_mcp", event_source="source")
        gateway.submit_external_event("original", **args)
        self.assertEqual("original", captured[-1]["message"])
        self.assertNotIn("raw_injection", captured[-1]["meta"])
        settings["enabled"] = True
        gateway.submit_external_event("original", **{**args, "event_type": "ordinary.event"})
        self.assertTrue(captured[-1]["message"].startswith('/btw_with_mcp '))
        settings["enabled"] = False
        gateway.submit_external_event("original", **args)
        self.assertEqual("original", captured[-1]["message"])

    def test_settings_are_host_authenticated_and_default_off(self):
        from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
        with tempfile.TemporaryDirectory() as root:
            config = {"work_mode": True, "work_parallel_host": {"token": "x" * 40}}
            def respond(_id, text, error=False):
                return {"text": text, "error": error}
            def invoke(args):
                return dispatch_work_tool(1, "work_side_settings", args,
                    load_config=lambda: config, state_dir=Path(root), respond=respond,
                    save_config=lambda value: config.update(value))
            args = {"session_id": "claude:fixture", "_host": {"token": "x" * 40}}
            import json
            self.assertFalse(json.loads(invoke(args)["text"])["enabled"])
            self.assertTrue(json.loads(invoke({**args, "enabled": True})["text"])["enabled"])
            self.assertFalse(json.loads(invoke({**args, "enabled": False})["text"])["enabled"])
            self.assertTrue(invoke({"session_id": "claude:fixture", "enabled": True})["error"])

    def test_concurrent_retry_enqueues_once(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'work.json'
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: enqueue(WorkPlanStore(path), 'request', 'id:1'), range(4)))
            self.assertEqual(1, len({r['item_id'] for r in results}))
            self.assertEqual(1, len(WorkPlanStore(path).read()['nodes']))
