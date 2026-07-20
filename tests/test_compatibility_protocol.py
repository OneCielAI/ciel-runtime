import io
import unittest
import urllib.error

from ciel_runtime_support.compatibility_protocol import (
    CompatibilityProtocolCodec,
    CompatibilityProtocolPorts,
)


class CompatibilityProtocolCodecTests(unittest.TestCase):
    def setUp(self):
        self.codec = CompatibilityProtocolCodec(
            "compat_echo",
            CompatibilityProtocolPorts(
                max_tokens_for_model=lambda model: 64 if "small" in model else 128,
                first_header=lambda headers, names: next(
                    (headers[name] for name in names if name in headers), None
                ),
                parse_retry_after=lambda value: float(value) if value else None,
                format_duration=lambda value: f"{value:g} seconds",
            ),
        )

    def test_text_and_tool_requests_use_protocol_limits(self):
        text = self.codec.text_request("small-model")
        tool = self.codec.tool_request("model")

        self.assertEqual(64, text["max_tokens"])
        self.assertFalse(text["stream"])
        self.assertEqual("compat_echo", tool["tools"][0]["name"])
        self.assertEqual({"type": "tool", "name": "compat_echo"}, tool["tool_choice"])

    def test_tool_result_request_preserves_tool_identity_and_input(self):
        request = self.codec.tool_result_request(
            "model", {"id": "tool-1", "input": {"text": "ping"}}
        )

        assistant = request["messages"][1]["content"][0]
        result = request["messages"][2]["content"][0]
        self.assertEqual("tool-1", assistant["id"])
        self.assertEqual({"text": "ping"}, assistant["input"])
        self.assertEqual("tool-1", result["tool_use_id"])

    def test_find_tool_use_validates_name_input_and_id(self):
        block = {
            "type": "tool_use", "id": "tool-1", "name": "compat_echo",
            "input": {"text": "ping"},
        }
        found, error = self.codec.find_tool_use({"content": [block]})

        self.assertIs(block, found)
        self.assertEqual("", error)
        _found, error = self.codec.find_tool_use(
            {"content": [{**block, "name": "wrong"}]}
        )
        self.assertIn("unexpected tool name", error)

    def test_missing_tool_use_reports_content_types_and_preview(self):
        found, error = self.codec.find_tool_use(
            {"content": [{"type": "text", "text": "no tool"}]}
        )
        self.assertIsNone(found)
        self.assertIn("content blocks: text", error)
        self.assertIn("no tool", error)

    def test_summary_projects_stop_content_and_usage(self):
        lines = self.codec.summarize_response(
            {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "OK"}],
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
            "Text",
        )
        self.assertEqual("Text: OK", lines[0])
        self.assertIn("Stop reason: end_turn", lines)
        self.assertIn("Tokens: in=2, out=1", lines)

    def test_http_error_preserves_type_and_retry_after(self):
        error = urllib.error.HTTPError(
            "https://example.invalid", 429, "rate limited",
            {"Retry-After": "2.5"},
            io.BytesIO(b'{"error":{"type":"rate_limit","message":"slow down"}}'),
        )
        self.assertEqual(
            "rate_limit: slow down Retry-After: 2.5 seconds (2.5s)",
            self.codec.http_error_message(error),
        )


if __name__ == "__main__":
    unittest.main()
