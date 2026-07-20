import unittest

from ciel_runtime_support.context_summary_policy import (
    ContextSummaryCompatibilityApi,
    ContextSummaryPolicy,
)


class ContextSummaryCompatibilityApiTests(unittest.TestCase):
    def policy(self, marker="first"):
        return ContextSummaryPolicy(
            estimate_tokens=lambda value: max(1, len(str(value)) // 4),
            positive_int=lambda value: int(value) if value else None,
            content_to_text=lambda value: str(value or ""),
            compact_json=lambda value, _limit: str(value),
            latest_user_text=lambda body: str(body.get("text") or marker),
        )

    def api(self, marker="first", logs=None):
        return ContextSummaryCompatibilityApi(
            policy_factory=lambda: self.policy(marker),
            compact_system_prompt="compact only",
            append_system=lambda system, extra: [system, *extra],
            log=lambda level, message: (
                logs.append((level, message)) if logs is not None else None
            ),
        )

    def test_text_only_projection_removes_tools_and_appends_prompt(self):
        logs = []
        api = self.api(logs=logs)
        body = {
            "text": "<command-name>/compact</command-name>",
            "system": "identity",
            "tools": [{"name": "Read"}],
            "tool_choice": {"type": "auto"},
        }
        output = api.text_only_body(body)
        self.assertNotIn("tools", output)
        self.assertNotIn("tool_choice", output)
        self.assertEqual(["identity", "compact only"], output["system"])
        self.assertTrue(logs)

    def test_adapter_exposes_chunk_and_reduce_projections(self):
        api = self.api()
        messages = [{"role": "user", "content": "hello"}]
        self.assertEqual(0, api.instruction_index(messages))
        self.assertEqual(1, len(api.split_messages(messages, 8192)))
        self.assertIn("Segment 1/1", api.chunk_prompt(messages, 0, 1, 1))
        reduced = api.reduce_prompt(
            ["summary"],
            "continue",
            budget_tokens=8192,
            source_message_count=1,
        )
        self.assertIn("summary", reduced)

    def test_policy_factory_is_resolved_per_call(self):
        marker = ["first"]
        api = ContextSummaryCompatibilityApi(
            policy_factory=lambda: self.policy(marker[0]),
            compact_system_prompt="compact",
            append_system=lambda system, extra: [system, *extra],
            log=lambda _level, _message: None,
        )
        self.assertEqual("first", api.message_text("first"))
        marker[0] = "second"
        self.assertEqual("second", api.message_text("second"))


if __name__ == "__main__":
    unittest.main()
