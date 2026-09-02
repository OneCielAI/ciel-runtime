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
            read_messages=self._read_messages,
            store_file_path=lambda path, name, content_type: {"name": name or str(path)},
            store_file_upload=lambda body: {"name": body["name"]},
            file_message_text=lambda message, uploads: f"{message} [{uploads[0]['name']}]",
            handle_llm_options=lambda action, preset: ([action, preset], action == "apply"),
            telemetry_logs=lambda action, args: {"action": action, "file": args.get("file")},
        )

    def _queue_compact(self, source, reason):
        self.compactions.append((source, reason))
        return {"id": "compact-1", "command": "/compact", "expires_at": 123}

    def _append_message(self, message):
        saved = {"id": len(self.messages) + 1, **message}
        self.messages.append(saved)
        return saved

    def _read_messages(self, after_id=0, channel=None, recipient=None, limit=100):
        return [
            message
            for message in self.messages
            if int(message.get("id") or 0) > after_id
            and (channel is None or message.get("channel") == channel)
        ][:limit]

    def test_catalog_exposes_only_supported_tools(self):
        names = {tool["name"] for tool in channel_mcp_tool_schemas()}
        self.assertEqual(
            {"submit_input", "compact_session", "send_message", "send_file", "llm_options", "telemetry_logs"},
            names,
        )

    def test_submit_input_uses_shared_runtime_gateway(self):
        admitted = []
        services = replace(
            self.services,
            submit_input=lambda body: admitted.append(body) or {"id": 41, **body},
        )

        response = dispatch_channel_mcp_tool(
            7,
            {
                "name": "submit_input",
                "arguments": {
                    "message": "from streamable MCP",
                    "input_transport": "session_socket",
                },
            },
            services,
        )

        self.assertEqual("from streamable MCP", admitted[0]["message"])
        self.assertEqual("session_socket", admitted[0]["input_transport"])
        self.assertFalse(response["result"]["isError"])

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

    def test_send_message_preserves_structured_web_response(self):
        response = dispatch_channel_mcp_tool(
            11,
            {
                "name": "send_message",
                "arguments": {
                    "channel": "chat",
                    "kind": "reply",
                    "response": {
                        "spoken": "짧게 말할 답변입니다.",
                        "overview": "요약입니다.",
                        "details": "- 근거 하나\n- 근거 둘",
                    },
                },
            },
            self.services,
        )

        self.assertFalse(response["result"]["isError"])
        self.assertEqual("요약입니다.\n\n- 근거 하나\n- 근거 둘", self.messages[0]["message"])
        self.assertEqual(
            {
                "spoken": "짧게 말할 답변입니다.",
                "overview": "요약입니다.",
                "details": "- 근거 하나\n- 근거 둘",
            },
            self.messages[0]["meta"]["web_response"],
        )

    def test_send_message_schema_allows_structured_response_without_legacy_message(self):
        schema = next(tool for tool in channel_mcp_tool_schemas() if tool["name"] == "send_message")["inputSchema"]

        self.assertEqual(["channel"], schema["required"])
        self.assertEqual(
            [{"required": ["message"]}, {"required": ["response"]}],
            schema["anyOf"],
        )
        self.assertEqual(
            {"spoken", "overview", "details"},
            set(schema["properties"]["response"]["properties"]),
        )

    def test_web_chat_reply_requires_current_parent_request(self):
        response = dispatch_channel_mcp_tool(
            12,
            {
                "name": "send_message",
                "arguments": {
                    "channel": "web-chat-thread-7",
                    "thread_id": "thread-7",
                    "kind": "reply",
                    "message": "must not leak",
                },
            },
            self.services,
        )

        self.assertTrue(response["result"]["isError"])
        self.assertIn("parent_id", response["result"]["content"][0]["text"])
        self.assertEqual([], self.messages)

    def test_web_chat_reply_is_correlated_and_final_reply_is_one_shot(self):
        self.messages.append(
            {
                "id": 1,
                "channel": "web-chat-thread-7",
                "thread_id": "thread-7",
                "kind": "web_chat",
                "message": "typed question",
                "meta": {"source": "ciel-runtime-web-chat", "input_mode": "text"},
            }
        )
        arguments = {
            "channel": "web-chat-thread-7",
            "thread_id": "thread-7",
            "parent_id": "1",
            "kind": "reply",
            "message": "correlated answer",
        }

        first = dispatch_channel_mcp_tool(
            13, {"name": "send_message", "arguments": arguments}, self.services
        )
        duplicate = dispatch_channel_mcp_tool(
            14, {"name": "send_message", "arguments": arguments}, self.services
        )

        self.assertFalse(first["result"]["isError"])
        self.assertTrue(duplicate["result"]["isError"])
        self.assertIn("already delivered", duplicate["result"]["content"][0]["text"])
        self.assertEqual(2, len(self.messages))

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

    def test_telemetry_logs_read_dispatches_cursor_and_delete_requires_confirmation(self):
        read = dispatch_channel_mcp_tool(
            15,
            {
                "name": "telemetry_logs",
                "arguments": {"action": "read", "file": "agent.log", "segment": 2, "offset": 10},
            },
            self.services,
        )
        denied = dispatch_channel_mcp_tool(
            16,
            {"name": "telemetry_logs", "arguments": {"action": "delete", "file": "agent.log"}},
            self.services,
        )

        self.assertEqual("read", json.loads(read["result"]["content"][0]["text"])["action"])
        self.assertTrue(denied["result"]["isError"])
        self.assertIn("confirm=true", denied["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
