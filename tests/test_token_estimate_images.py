import copy
import json
import unittest
from unittest.mock import Mock

from ciel_runtime_support.context_summary_policy import ContextSummaryPolicy
from ciel_runtime_support.media_payload import opaque_media
from ciel_runtime_support.prompt_compaction import (
    PromptCompactionRuntime, PromptCompactionServices, PromptCompactionText,
    compact_anthropic_body_for_budget,
)
from ciel_runtime_support.token_estimate import estimate_tokens


class ImagePayloadTests(unittest.TestCase):
    def setUp(self):
        self.image = "A" * 12000
        self.message = {
            "role": "user",
            "content": [
                {"type": "text", "text": "keep this text"},
                {
                    "type": "tool_result",
                    "tool_use_id": "call-1",
                    "content": [{
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": self.image,
                        },
                    }],
                },
            ],
        }

    def test_projection_is_opaque_and_does_not_mutate_wire_message(self):
        before = copy.deepcopy(self.message)
        projected, observation = opaque_media(self.message)

        self.assertEqual(before, self.message)
        self.assertEqual((1, len(self.image)),
                         (observation.images, observation.encoded_bytes))
        self.assertNotIn(self.image, json.dumps(projected))
        self.assertIn("omitted image bytes", json.dumps(projected))
        self.assertIn(self.image, json.dumps(self.message))

    def test_estimator_keeps_ordinary_base64_looking_text(self):
        ordinary = {"role": "user", "content": self.image}
        self.assertEqual(estimate_tokens(ordinary),
                         estimate_tokens(copy.deepcopy(ordinary)))
        projected, observation = opaque_media(ordinary)
        self.assertIs(projected, ordinary)
        self.assertEqual((0, 0), (observation.images, observation.encoded_bytes))

    def test_tool_arguments_and_schema_examples_are_not_image_content(self):
        image = self.message["content"][1]["content"][0]
        ordinary = {"role": "assistant", "content": [
            {"type": "tool_use", "name": "save", "input": {"content": [image]}}
        ], "tools": [{"input_schema": {"example": image}}]}
        projected, observation = opaque_media(ordinary)
        self.assertIs(projected, ordinary)
        self.assertEqual(0, observation.images)

    def test_responses_and_ollama_payloads_are_opaque(self):
        for value in (
            {"input": [{"role": "user", "content": [{"type": "input_image",
                "image_url": "data:image/png;base64," + self.image}]}]},
            {"messages": [{"role": "user", "content": "see image", "images": [self.image]}]},
        ):
            before = copy.deepcopy(value)
            projected, observation = opaque_media(value)
            self.assertEqual(1, observation.images)
            self.assertEqual(len(self.image), observation.encoded_bytes)
            self.assertNotIn(self.image, json.dumps(projected))
            self.assertEqual(before, value)

    def test_content_data_without_an_image_type_stays_unchanged(self):
        value = {"role": "user", "content": [{"data": self.image}]}
        projected, observation = opaque_media(value)
        self.assertIs(value, projected)
        self.assertEqual(0, observation.images)

    def test_textual_map_projection_preserves_order_and_hides_image_bytes(self):
        policy = ContextSummaryPolicy(
            estimate_tokens, lambda value: int(value or 0), lambda value: "",
            lambda value: "", lambda value: "",
        )
        serialized = policy.compact_message(self.message, 7)

        self.assertLess(serialized.index("keep this text"),
                        serialized.index("omitted image bytes"))
        self.assertNotIn(self.image, serialized)
        self.assertIn("image/png", serialized)
        self.assertIn("encoded_bytes=12000", serialized)

    def test_native_checkpoint_that_only_exceeds_window_by_image_is_forwarded(self):
        body = {"messages": [self.message], "system": "rules"}
        def estimate(value):
            return max(1, len(json.dumps(value, ensure_ascii=False)) // 4)
        auxiliary = Mock(side_effect=AssertionError("must forward native checkpoint"))
        text = PromptCompactionText(*(Mock() for _ in range(6)))
        runtime = PromptCompactionRuntime(estimate, auxiliary, Mock(), Mock())
        services = PromptCompactionServices(text, runtime)
        text_only, _ = opaque_media(body)
        window = estimate(text_only)
        self.assertGreater(estimate(body), window)

        result = compact_anthropic_body_for_budget(
            body, window, provider="cielairouter", model="model",
            pcfg={"context_window": window,
                  "_context_compact_native_over_window": True},
            full_compact_request=True, services=services,
        )

        self.assertIs(result, body)
        auxiliary.assert_not_called()


if __name__ == "__main__":
    unittest.main()
