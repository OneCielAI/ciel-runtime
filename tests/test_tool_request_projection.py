import unittest

from ciel_runtime_support.tool_request_projection import (
    UltracodeSessionPolicy,
    forced_tool_choice_name,
    has_tool,
    synthetic_tool_use_response,
    tool_names_in_body,
)


class ToolRequestProjectionTests(unittest.TestCase):
    def test_tool_metadata_is_projected_from_request(self):
        body = {
            "tool_choice": {"type": "tool", "name": "Read"},
            "tools": [{"name": "Read"}, {"name": "Write"}, None],
        }

        self.assertEqual("Read", forced_tool_choice_name(body))
        self.assertEqual({"Read", "Write"}, tool_names_in_body(body))
        self.assertTrue(has_tool(body, "Write"))

    def test_non_tool_choice_is_ignored(self):
        self.assertIsNone(
            forced_tool_choice_name({"tool_choice": {"type": "auto"}})
        )

    def test_synthetic_response_uses_anthropic_tool_shape(self):
        response = synthetic_tool_use_response(
            "model",
            "Read",
            {"file_path": "a.txt"},
        )

        self.assertEqual("tool_use", response["stop_reason"])
        self.assertEqual("Read", response["content"][0]["name"])
        self.assertEqual(
            {"file_path": "a.txt"},
            response["content"][0]["input"],
        )

    def test_ultracode_policy_uses_latest_session_state(self):
        policy = UltracodeSessionPolicy(
            content_to_text=lambda value: str(value or "")
        )
        body = {
            "system": "Ultracode is on",
            "messages": [
                {"content": "Ultracode is off"},
                {"content": "Ultracode is still on"},
            ],
            "tools": [{"name": "Workflow"}],
        }

        self.assertTrue(policy.runtime_enabled(body))
        self.assertTrue(policy.workflow_preferred(body))


if __name__ == "__main__":
    unittest.main()
