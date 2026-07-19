import tempfile
from pathlib import Path
import unittest
from unittest import mock

import ciel_runtime


class RuntimeObservabilityTests(unittest.TestCase):
    def test_sse_event_projection_failure_is_observable(self):
        trace = {"event_count": object(), "events": []}
        with mock.patch.object(ciel_runtime, "router_log") as log:
            ciel_runtime.record_outgoing_sse_event(trace, "content_block_delta", {})

        self.assertIn("sse_trace_event_record_failed", log.call_args.args[1])

    def test_sse_trace_persistence_failure_is_observable(self):
        trace = {}
        trace["cycle"] = trace
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", Path(tmp)),
                mock.patch.object(ciel_runtime, "router_log") as log,
            ):
                ciel_runtime.finish_outgoing_sse_trace(trace, outcome="complete")

        self.assertIn("sse_trace_finish_failed", log.call_args.args[1])

    def test_channel_delivery_guard_state_failure_is_observable(self):
        class ReadOnlyHandler:
            def __setattr__(self, _name, _value):
                raise RuntimeError("read only")

        body = {"metadata": {"ciel_runtime_channel_cursor_last_id": "9"}}
        with mock.patch.object(ciel_runtime, "router_log") as log:
            ciel_runtime.begin_pending_channel_delivery(ReadOnlyHandler(), body)  # type: ignore[arg-type]

        self.assertIn("channel_delivery_guard_begin_failed", log.call_args.args[1])

    def test_corrupt_managed_mcp_proxy_config_is_observable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proxy_path = root / "mcp-proxy.json"
            proxy_path.write_text("{broken", encoding="utf-8")
            with (
                mock.patch.object(ciel_runtime, "MCP_PROXY_CONFIG", proxy_path),
                mock.patch.object(ciel_runtime, "WEB_TOOLS_MCP_CONFIG", root / "missing.json"),
                mock.patch.object(ciel_runtime, "router_log") as log,
            ):
                self.assertEqual({}, ciel_runtime.discovered_ciel_runtime_managed_mcp_servers(root))

        self.assertIn("managed_mcp_proxy_config_read_failed", log.call_args.args[1])

    def test_corrupt_proxy_ownership_config_is_observable(self):
        with tempfile.TemporaryDirectory() as tmp:
            proxy_path = Path(tmp) / "mcp-proxy.json"
            proxy_path.write_text("{broken", encoding="utf-8")
            with (
                mock.patch.object(ciel_runtime, "MCP_PROXY_CONFIG", proxy_path),
                mock.patch.object(ciel_runtime, "router_log") as log,
            ):
                self.assertEqual(set(), ciel_runtime.proxy_owned_channel_server_names())

        self.assertIn("proxy_owned_channel_config_read_failed", log.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
