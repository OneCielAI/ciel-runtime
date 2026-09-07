import unittest

from ciel_runtime_support.context_summary_policy import (
    CODEX_CONTEXT_CHECKPOINT_PROMPT,
    ContextSummaryCompatibilityApi,
    ContextSummaryPolicy,
    is_codex_context_checkpoint_prompt,
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
            parse_bool=lambda value, default=False: default if value is None else bool(value),
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

    def test_guard_summary_is_stable_across_observed_wire_fit_budget_drift(self):
        policy = self.policy()
        omitted = [
            {"role": "user", "content": f"old message {index}"}
            for index in range(288)
        ]
        observed_budgets = [2_240_027, 2_240_028, 2_240_032, 2_240_035, 2_240_056]

        summaries = [policy.guard_summary(omitted, budget) for budget in observed_budgets]

        self.assertEqual(1, len(set(summaries)))
        self.assertNotIn("2240027", summaries[0])
        self.assertIn("provider context budget was exceeded", summaries[0])

    def test_guard_summary_budget_bucket_is_conservative_and_stable(self):
        policy = self.policy()

        self.assertEqual(2_236_416, policy.cache_stable_summary_budget(2_239_270))
        self.assertEqual(2_236_416, policy.cache_stable_summary_budget(2_240_100))
        self.assertLessEqual(
            policy.cache_stable_summary_budget(20_001),
            20_001,
        )

    def test_codex_0147_checkpoint_prompt_is_detected_from_latest_user(self):
        api = self.api(marker=CODEX_CONTEXT_CHECKPOINT_PROMPT)

        self.assertTrue(api.is_compact_request({"messages": []}))
        self.assertTrue(
            is_codex_context_checkpoint_prompt(
                CODEX_CONTEXT_CHECKPOINT_PROMPT.lower() + "\nfuture detail"
            )
        )
        self.assertFalse(
            is_codex_context_checkpoint_prompt(
                "Create a handoff summary for another LLM that will resume the task."
            )
        )

    def test_older_checkpoint_does_not_make_a_normal_latest_turn_compact(self):
        policy = self.policy(marker="continue the implementation")
        body = {
            "messages": [
                {"role": "user", "content": CODEX_CONTEXT_CHECKPOINT_PROMPT},
                {"role": "user", "content": "continue the implementation"},
            ]
        }

        self.assertFalse(policy.is_compact_request(body))

    def test_instruction_index_prefers_the_newest_compact_marker(self):
        policy = self.policy()
        messages = [
            {"role": "user", "content": "<command-name>/compact</command-name>"},
            {"role": "assistant", "content": "old summary"},
            {"role": "user", "content": CODEX_CONTEXT_CHECKPOINT_PROMPT},
        ]

        self.assertEqual(2, policy.instruction_index(messages))

    def test_reduce_prompt_is_runtime_neutral_and_preserves_codex_instruction(self):
        reduced = self.api().reduce_prompt(
            ["segment summary"],
            CODEX_CONTEXT_CHECKPOINT_PROMPT,
            budget_tokens=8192,
            source_message_count=10,
        )

        self.assertIn(CODEX_CONTEXT_CHECKPOINT_PROMPT, reduced)
        self.assertIn("Client compact instruction", reduced)
        self.assertNotIn("Claude Code", reduced)

    def test_policy_factory_is_resolved_per_call(self):
        marker = ["first"]
        api = ContextSummaryCompatibilityApi(
            policy_factory=lambda: self.policy(marker[0]),
            compact_system_prompt="compact",
            append_system=lambda system, extra: [system, *extra],
            log=lambda _level, _message: None,
            parse_bool=lambda value, default=False: default if value is None else bool(value),
        )
        self.assertEqual("first", api.message_text("first"))
        marker[0] = "second"
        self.assertEqual("second", api.message_text("second"))


if __name__ == "__main__":
    unittest.main()
