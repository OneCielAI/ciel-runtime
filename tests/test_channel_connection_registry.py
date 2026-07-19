import threading
import unittest

from ciel_runtime_support.channel_connection_registry import ChannelConnectionRegistry


class ChannelConnectionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.states = {"mcp-demo": {"running": True, "messages_received": 2}}
        lock = threading.Lock()
        self.registry = ChannelConnectionRegistry(
            states=self.states,
            lock=lock,
            rpc_condition=threading.Condition(),
            log=lambda _level, _message: None,
        )

    def test_status_is_public_projection(self):
        status = self.registry.statuses()["mcp-demo"]
        self.assertTrue(status["running"])
        self.assertEqual(2, status["messages_received"])
        self.assertNotIn("mcp_rpc_results", status)

    def test_mark_session_lost_is_atomic_state_transition(self):
        self.states["mcp-demo"].update(mcp_initialized=True, mcp_session_id="session")
        self.registry.mark_session_lost("mcp-demo", "expired")
        self.assertFalse(self.states["mcp-demo"]["mcp_initialized"])
        self.assertIsNone(self.states["mcp-demo"]["mcp_session_id"])
        self.assertEqual(1, self.states["mcp-demo"]["sse_reconnects"])

    def test_rpc_response_round_trip(self):
        self.assertTrue(self.registry.store_rpc_response("mcp-demo", '{"jsonrpc":"2.0","id":7,"result":{}}'))
        self.assertEqual({}, self.registry.take_rpc_response("mcp-demo", 7, 0.1)["result"])

    def test_server_alias_resolution(self):
        self.assertEqual("mcp-demo", self.registry.state_name_for_mcp_server("demo"))


if __name__ == "__main__":
    unittest.main()
