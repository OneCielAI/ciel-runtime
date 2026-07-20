import unittest

from ciel_runtime_support.protocols.anthropic_content import content_to_text


class AnthropicContentProjectionTests(unittest.TestCase):
    def test_projects_nested_tool_result_content(self):
        content = [
            {"type": "text", "text": "Before"},
            {
                "type": "tool_result",
                "tool_use_id": "call-1",
                "content": [{"type": "text", "text": "Result"}],
            },
        ]

        self.assertEqual(
            "Before\nTool result for call-1:\nResult",
            content_to_text(content),
        )

    def test_ignores_unknown_blocks_and_preserves_scalar_values(self):
        self.assertEqual("42", content_to_text(42))
        self.assertEqual("", content_to_text(None))
        self.assertEqual(
            "plain",
            content_to_text([{"type": "image"}, "plain"]),
        )


if __name__ == "__main__":
    unittest.main()
