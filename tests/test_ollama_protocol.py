import unittest

from ciel_runtime_support.protocols.ollama_chat import (
    anthropic_system_to_ollama_messages,
    anthropic_tools_to_ollama,
    decode_ollama_chat_response,
    encode_anthropic_message,
    ollama_claude_code_reminder,
)


class OllamaProtocolProjectionTests(unittest.TestCase):
    def test_projects_structured_anthropic_system_content(self):
        self.assertEqual(
            [{"role": "system", "content": "first\nsecond"}],
            anthropic_system_to_ollama_messages(
                [{"type": "text", "text": "first"}, {"type": "text", "text": "second"}]
            ),
        )

    def test_projects_anthropic_tool_schema(self):
        self.assertEqual(
            [{"type": "function", "function": {
                "name": "Read",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            }}],
            anthropic_tools_to_ollama([{
                "name": "Read",
                "description": "Read a file",
                "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
            }]),
        )

    def test_runtime_reminder_preserves_plan_mode_constraint(self):
        reminder = ollama_claude_code_reminder()
        self.assertEqual("system", reminder["role"])
        self.assertIn("Plan Mode", reminder["content"])
        self.assertIn("ExitPlanMode", reminder["content"])

    def test_decodes_ollama_response_envelope(self):
        decoded = decode_ollama_chat_response({
            "message": {
                "content": "done",
                "tool_calls": [{"function": {"name": "Read", "arguments": {"path": "a"}}}],
            },
            "done_reason": "stop",
            "prompt_eval_count": 12,
            "eval_count": 3,
        })

        self.assertEqual("done", decoded.text)
        self.assertEqual("Read", decoded.tool_calls[0]["function"]["name"])
        self.assertEqual((12, 3), (decoded.input_tokens, decoded.output_tokens))

    def test_encodes_anthropic_tool_use_stop_reason(self):
        message = encode_anthropic_message(
            message_id="msg_test",
            model="qwen",
            content=[{"type": "tool_use", "id": "tool_1", "name": "Read", "input": {}}],
            done_reason="stop",
            input_tokens=5,
            output_tokens=2,
        )

        self.assertEqual("tool_use", message["stop_reason"])
        self.assertEqual({"input_tokens": 5, "output_tokens": 2}, message["usage"])


if __name__ == "__main__":
    unittest.main()
