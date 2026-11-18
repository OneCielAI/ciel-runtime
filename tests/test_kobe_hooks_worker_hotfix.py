"""KOBE hooks-worker hotfix (2026-11-18): router contracts behind the mod fixes.

- C: a bounded source thread is recorded on its side receipt (work_side_renew
  source_bounded), so the side receipt pane and the SSE pane show it instead of
  one main-visible log line per event.
- B: an event's expected_version is read as the ledger version alone
  (work_parallel_status projection "version"), not as the whole paged status.
- D: a configured CielAiRouter model the gateway lists but ciel-runtime's agent
  filter left out is named as filtered, with the missing capability.
"""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool

HOST_TOKEN = 'x' * 40


class SideReceiptBoundedThreadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.identity = 'claude:parent'
        self.config = {'work_mode': True, 'work_side_intake': {'enabled': True},
                       'work_parallel_host': {'token': HOST_TOKEN}}
        self.inbox = SideIntakeInbox(self.root, self.identity)
        self.inbox.receive('Question about the long thread', 'event-1')

    def invoke(self, name, args, host=None):
        return dispatch_work_tool(1, name, {**args, 'session_id': self.identity, '_host': host or {'token': HOST_TOKEN}},
                                  load_config=lambda: self.config, state_dir=self.root,
                                  respond=lambda _id, text, error=False: {'text': text, 'error': error})

    def claim(self):
        result = self.invoke('work_side_claim', {'owner': 'actor'})
        self.assertFalse(result['error'], result)
        event = json.loads(result['text'])['claim']
        return event, {'token': HOST_TOKEN, 'receipt_id': event['receipt_id'], 'generation': event['generation']}

    def test_renew_records_the_bounded_thread_on_the_receipt_without_shortening_the_lease(self):
        event, host = self.claim()
        row = self.inbox._load(event['receipt_id'])
        row['processing']['lease_until'] = row['processing']['lease_until'] + 3000
        self.inbox._save_file(self.inbox._event_path('pending', event['receipt_id']), row)
        long_lease = row['processing']['lease_until']
        result = self.invoke('work_side_renew', {'source_bounded': {'omitted_replies': 53}}, host)
        self.assertFalse(result['error'], result)
        self.assertEqual(long_lease, json.loads(result['text'])['lease_until'])
        summary = next(r for r in self.inbox.summaries() if r['receipt_id'] == event['receipt_id'])
        self.assertEqual({'omitted_replies': 53}, summary['source_bounded'])
        # The host-only side status the panes read carries it too.
        status = json.loads(self.invoke('work_side_status', {})['text'])
        row = next(r for r in status['side_inbox_receipts'] if r['receipt_id'] == event['receipt_id'])
        self.assertEqual({'omitted_replies': 53}, row['source_bounded'])

    def test_a_plain_heartbeat_records_nothing_and_a_bad_fact_is_refused(self):
        event, host = self.claim()
        self.assertFalse(self.invoke('work_side_renew', {}, host)['error'])
        summary = next(r for r in self.inbox.summaries() if r['receipt_id'] == event['receipt_id'])
        self.assertNotIn('source_bounded', summary)
        for bad in ({'omitted_replies': 0}, {'omitted_replies': '5'}, {}, 'many'):
            with self.subTest(bad=bad):
                self.assertTrue(self.invoke('work_side_renew', {'source_bounded': bad}, host)['error'])
        # Another generation never records anything.
        self.assertTrue(self.invoke('work_side_renew', {'source_bounded': {'omitted_replies': 4}},
                                    {**host, 'generation': 'f' * 32})['error'])
        summary = next(r for r in self.inbox.summaries() if r['receipt_id'] == event['receipt_id'])
        self.assertNotIn('source_bounded', summary)

    def test_the_renew_schema_names_the_fact(self):
        from ciel_runtime_support.side_intake_mcp import schemas
        renew = next(row for row in schemas() if row['name'] == 'work_side_renew')
        self.assertEqual(['omitted_replies'], renew['inputSchema']['properties']['source_bounded']['required'])


class HostVersionProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.identity = 'claude:parent'
        self.config = {'work_mode': True, 'work_parallel_host': {'token': HOST_TOKEN}}
        patch = mock.patch.dict(os.environ, {'CIEL_RUNTIME_CONFIG_DIR': str(self.root / 'config')})
        patch.start()
        self.addCleanup(patch.stop)
        from ciel_runtime_support import runtime_paths
        ledger = mock.patch.object(runtime_paths, 'CONFIG_DIR', self.root / 'config')
        ledger.start()
        self.addCleanup(ledger.stop)
        (self.root / 'config').mkdir()

    def invoke(self, args, host):
        result = dispatch_work_tool(1, 'work_parallel_status', {**args, 'session_id': self.identity, '_host': host},
                                    load_config=lambda: self.config, state_dir=self.root,
                                    respond=lambda _id, text, error=False: {'text': text, 'error': error})
        self.assertFalse(result['error'], result)
        return json.loads(result['text'])

    def test_the_host_reads_the_ledger_version_alone(self):
        full = self.invoke({}, {'token': HOST_TOKEN})
        version = self.invoke({'projection': 'version'}, {'token': HOST_TOKEN})
        self.assertEqual({'version': full['version'], 'projection': 'version'}, version)
        self.assertLess(len(json.dumps(version)), 80)
        self.assertGreater(len(json.dumps(full)), len(json.dumps(version)))

    def test_a_model_read_gets_the_ordinary_projection(self):
        model = self.invoke({'projection': 'version'}, {})
        self.assertIn('policy', model)
        self.assertNotIn('projection', model)


class FilteredGatewayModelTests(unittest.TestCase):
    """KOBE 2026-11-18: _Walkie_Sol61-Opus55-Fast is listed by the gateway with
    tool_calling and reasoning but no thinking flag; the launch said only that
    it was not in the provider catalog."""

    LISTED = {"id": "_Walkie_Sol61-Opus55-Fast", "owned_by": "combo",
              "capabilities": {"tool_calling": True, "reasoning": True}}
    ASTRA = {"id": "ASTRA", "owned_by": "combo",
             "capabilities": {"tool_calling": True, "reasoning": True, "thinking": True, "supportsThinking": True}}

    def run_selection(self, current, rows, *, refresh_first=True):
        import ciel_runtime
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["cielairouter"])
            pcfg.update(api_key="isolated-key", current_model=current)
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "MODEL_LIST_CACHE_PATH", root / "list.json"),
                mock.patch.object(ciel_runtime, "MODEL_REGISTRY_PATH", root / "registry.json"),
                mock.patch.object(ciel_runtime, "http_json", return_value={"data": rows}) as http,
            ):
                if refresh_first:
                    ciel_runtime.upstream_model_ids("cielairouter", pcfg, force_refresh=True)
                    http.reset_mock()
                ok, messages = ciel_runtime.ensure_current_model_from_provider_list("cielairouter", pcfg)
                return ok, messages, http.call_count

    def test_the_agent_filter_and_its_reasons_agree(self):
        from ciel_runtime_support.providers.cielairouter import excluded_agent_models, select_agent_models
        rows = [self.ASTRA, self.LISTED, {"id": "img", "type": "image", "capabilities": {}},
                {"id": "notools", "capabilities": {"thinking": True}}]
        self.assertEqual(["ASTRA"], [row["id"] for row in select_agent_models(rows)])
        self.assertEqual({"_Walkie_Sol61-Opus55-Fast": "missing capability thinking", "img": "not a chat model",
                          "notools": "missing capability tool_calling"}, excluded_agent_models(rows))

    def test_a_filtered_model_is_named_with_the_missing_capability_from_the_cached_catalog(self):
        ok, messages, fetches = self.run_selection(self.LISTED["id"], [self.ASTRA, self.LISTED])
        self.assertFalse(ok)
        self.assertEqual(0, fetches)  # read from the cache the refresh wrote
        message = messages[0]
        self.assertIn("is not in the provider catalog", message)
        self.assertIn("the gateway lists it, but ciel-runtime filtered it out", message)
        self.assertIn("missing capability thinking", message)
        self.assertIn("gateway owns its capability flags", message)

    def test_a_model_the_gateway_does_not_list_keeps_the_plain_message(self):
        ok, messages, _ = self.run_selection("gone-model", [self.ASTRA, self.LISTED])
        self.assertFalse(ok)
        self.assertIn("is not in the provider catalog. Refresh the model list", messages[0])
        self.assertNotIn("filtered", messages[0])

    def test_which_models_pass_is_unchanged(self):
        ok, messages, _ = self.run_selection("ASTRA", [self.ASTRA, self.LISTED])
        self.assertTrue(ok, messages)


if __name__ == '__main__':
    unittest.main()
