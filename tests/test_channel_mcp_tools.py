import json
import unittest
from dataclasses import replace

from ciel_runtime_support.channel_mcp_tools import (
    ChannelMcpToolServices,
    channel_mcp_tool_schemas,
    dispatch_channel_mcp_tool,
)


class ChannelMcpToolsTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.compactions = []
        self.services = ChannelMcpToolServices(
            queue_compact=self._queue_compact,
            append_message=self._append_message,
            store_file_path=lambda path, name, content_type: {"name": name or str(path)},
            store_file_upload=lambda body: {"name": body["name"]},
            file_message_text=lambda message, uploads: f"{message} [{uploads[0]['name']}]",
            handle_llm_options=lambda action, preset: ([action, preset], action == "apply"),
        )

    def _queue_compact(self, source, reason):
        self.compactions.append((source, reason))
        return {"id": "compact-1", "command": "/compact", "expires_at": 123}

    def _append_message(self, message):
        self.messages.append(message)
        return {"id": 1, **message}

    def test_catalog_exposes_only_supported_tools(self):
        names = {tool["name"] for tool in channel_mcp_tool_schemas()}
        self.assertEqual({"compact_session", "send_message", "send_file", "llm_options"}, names)

    def test_send_message_builds_default_web_delivery(self):
        response = dispatch_channel_mcp_tool(
            7,
            {"name": "send_message", "arguments": {"channel": "chat", "message": "done"}},
            self.services,
        )
        result = json.loads(response["result"]["content"][0]["text"])

        self.assertFalse(response["result"]["isError"])
        self.assertEqual(1, result["message"]["id"])
        self.assertEqual("web", self.messages[0]["recipients"])
        self.assertEqual(["web"], self.messages[0]["delivery"])

    def test_send_file_converts_expected_storage_errors_to_tool_error(self):
        services = replace(
            self.services,
            store_file_path=lambda path, name, content_type: (_ for _ in ()).throw(FileNotFoundError("missing")),
        )
        response = dispatch_channel_mcp_tool(
            8,
            {"name": "send_file", "arguments": {"channel": "chat", "path": "missing.txt"}},
            services,
        )

        self.assertTrue(response["result"]["isError"])
        self.assertIn("missing", response["result"]["content"][0]["text"])

    def test_compact_and_llm_options_use_injected_services(self):
        compact = dispatch_channel_mcp_tool(
            9,
            {"name": "compact_session", "arguments": {"reason": "large"}},
            self.services,
        )
        options = dispatch_channel_mcp_tool(
            10,
            {"name": "llm_options", "arguments": {"action": "apply", "preset": "balanced"}},
            self.services,
        )

        self.assertEqual([("ciel-runtime-router-tool", "large")], self.compactions)
        self.assertEqual("compact-1", json.loads(compact["result"]["content"][0]["text"])["request_id"])
        self.assertTrue(json.loads(options["result"]["content"][0]["text"])["changed"])


if __name__ == "__main__":
    unittest.main()
