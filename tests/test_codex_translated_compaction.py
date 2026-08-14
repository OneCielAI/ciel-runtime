import unittest
from unittest import mock

import ciel_runtime
from ciel_runtime_support.context_summary_policy import (
    CODEX_CONTEXT_CHECKPOINT_PROMPT,
)
from ciel_runtime_support.protocols.openai_responses import (
    openai_responses_to_anthropic_messages,
)


def translated_checkpoint_body(history_messages: int) -> dict:
    items = [
        {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": f"history {index} " + ("x" * 20_000),
                }
            ],
        }
        for index in range(history_messages)
    ]
    items.append(
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": CODEX_CONTEXT_CHECKPOINT_PROMPT}
            ],
        }
    )
    return openai_responses_to_anthropic_messages(
        {"model": "k3", "input": items, "tools": [], "stream": True}, "k3"
    )


class CodexTranslatedCompactionTests(unittest.TestCase):
    def config(self, **overrides):
        config = {
            "current_model": "k3",
            "context_window": 1_048_576,
            "base_url": "https://api.kimi.com/coding",
        }
        config.update(overrides)
        return config

    def request(self, history_messages: int, **config_overrides):
        summaries = mock.Mock(side_effect=lambda *_args, **_kwargs: "segment summary")
        with mock.patch.object(
            ciel_runtime, "context_compact_request_summary", summaries
        ):
            request = ciel_runtime.openai_compatible_chat_request(
                "kimi",
                "k3",
                translated_checkpoint_body(history_messages),
                self.config(**config_overrides),
                stream=True,
            )
        return request, summaries

    def test_over_budget_checkpoint_uses_segmented_fallback_automatically(self):
        # Roughly the captured 1M-token request size after projection: each
        # message is capped at 20K chars by the existing prompt projector.
        request, summaries = self.request(230)

        self.assertGreater(summaries.call_count, 1)
        self.assertLessEqual(summaries.call_count, 8)
        final_prompt = str(request["messages"][-1]["content"])
        self.assertIn("[ciel-runtime segmented compact]", final_prompt)
        self.assertIn(CODEX_CONTEXT_CHECKPOINT_PROMPT, final_prompt)
        self.assertIn("Client compact instruction", final_prompt)
        self.assertNotIn("Claude Code compact instruction", final_prompt)

    def test_within_budget_checkpoint_does_not_start_auxiliary_summaries(self):
        request, summaries = self.request(1)

        summaries.assert_not_called()
        self.assertEqual(
            CODEX_CONTEXT_CHECKPOINT_PROMPT,
            request["messages"][-1]["content"],
        )

    def test_explicit_false_disables_automatic_segmented_fallback(self):
        request, summaries = self.request(230, context_compact_llm=False)

        summaries.assert_not_called()
        self.assertNotIn(
            "[ciel-runtime segmented compact]",
            str(request["messages"][-1]["content"]),
        )
        self.assertEqual(
            CODEX_CONTEXT_CHECKPOINT_PROMPT,
            request["messages"][-1]["content"],
        )

    def test_normal_turn_never_enables_auxiliary_segment_summaries(self):
        body = translated_checkpoint_body(230)
        body["messages"][-1]["content"] = [
            {"type": "text", "text": "Please continue the implementation."}
        ]
        summaries = mock.Mock(return_value="segment summary")

        with mock.patch.object(
            ciel_runtime, "context_compact_request_summary", summaries
        ):
            request = ciel_runtime.openai_compatible_chat_request(
                "kimi", "k3", body, self.config(), stream=True
            )

        summaries.assert_not_called()
        self.assertEqual(
            "Please continue the implementation.", request["messages"][-1]["content"]
        )

    def test_anthropic_wire_compacts_only_the_codex_checkpoint_exception(self):
        config = self.config(api_key="test-key")
        compact_body = translated_checkpoint_body(230)
        summaries = mock.Mock(side_effect=lambda *_args, **_kwargs: "segment summary")

        with mock.patch.object(
            ciel_runtime, "context_compact_request_summary", summaries
        ):
            compacted = ciel_runtime.cap_anthropic_body_for_provider(
                "anthropic", config, compact_body
            )

        self.assertGreater(summaries.call_count, 1)
        self.assertLessEqual(summaries.call_count, 8)
        final_prompt = ciel_runtime.anthropic_content_to_text(
            compacted["messages"][-1]["content"]
        )
        self.assertIn(CODEX_CONTEXT_CHECKPOINT_PROMPT, final_prompt)

        normal_body = translated_checkpoint_body(2)
        normal_body["messages"][-1]["content"] = [
            {"type": "text", "text": "ordinary turn"}
        ]
        with mock.patch.object(
            ciel_runtime, "context_compact_request_summary"
        ) as normal_summaries:
            unchanged = ciel_runtime.cap_anthropic_body_for_provider(
                "anthropic", config, normal_body
            )
        normal_summaries.assert_not_called()
        self.assertEqual(normal_body, unchanged)

    def test_anthropic_checkpoint_with_unknown_context_is_forwarded_unchanged(self):
        config = {
            "current_model": "custom-unknown-context",
            "api_key": "test-key",
            "base_url": "https://api.anthropic.com",
        }
        body = translated_checkpoint_body(230)

        with mock.patch.object(
            ciel_runtime, "context_compact_request_summary"
        ) as summaries:
            unchanged = ciel_runtime.cap_anthropic_body_for_provider(
                "anthropic", config, body
            )

        summaries.assert_not_called()
        self.assertEqual(body, unchanged)


if __name__ == "__main__":
    unittest.main()
