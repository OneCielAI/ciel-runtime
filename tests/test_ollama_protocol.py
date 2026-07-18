import unittest

from ciel_runtime_support.protocols.ollama_chat import (
    anthropic_system_to_ollama_messages,
    anthropic_tools_to_ollama,
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


if __name__ == "__main__":
    unittest.main()
