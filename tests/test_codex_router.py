import unittest

from ciel_runtime_support.codex_router import (
    CodexChannelContextPorts,
    CodexChannelContextProjector,
)


class CodexChannelContextProjectorTests(unittest.TestCase):
    def projector(self, *, addition=True, metadata=True):
        def to_anthropic(_body, _model):
            return {"messages": [{"role": "user", "content": "original"}]}

        def inject_pending(delivery):
            projected = dict(delivery)
            projected["messages"] = list(delivery["messages"])
            if addition:
                projected["messages"].append(
                    {"role": "assistant", "content": "channel answer"}
                )
            if metadata:
                projected["metadata"] = {"ciel_runtime_cursor": "7"}
            return projected

        return CodexChannelContextProjector(
            CodexChannelContextPorts(
                to_anthropic=to_anthropic,
                inject_pending=inject_pending,
                inject_tool_results=lambda delivery: delivery,
                content_to_text=str,
            )
        )

    def test_projects_channel_additions_without_forwarding_private_metadata(self):
        body = {
            "model": "codex",
            "input": "hello",
            "metadata": {"ciel_runtime_cursor": "old"},
        }

        projected, delivery = self.projector().project(body)

        self.assertNotIn("metadata", projected)
        self.assertEqual(2, len(projected["input"]))
        self.assertEqual("output_text", projected["input"][-1]["content"][0]["type"])
        self.assertEqual("7", delivery["metadata"]["ciel_runtime_cursor"])

    def test_returns_metadata_free_copy_when_no_delivery_context_exists(self):
        body = {"input": [], "metadata": {"private": True}}

        projected, _delivery = self.projector(
            addition=False, metadata=False
        ).project(body)

        self.assertEqual({"input": []}, projected)
        self.assertIn("metadata", body)


if __name__ == "__main__":
    unittest.main()
