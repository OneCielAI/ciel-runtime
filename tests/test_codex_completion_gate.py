import json
import unittest

from ciel_runtime_support import codex_completion_gate
from ciel_runtime_support.codex_turn_recovery import CODEX_COMPLETION_CONFIRMED


def event(event_type, payload):
    return (
        f"event: {event_type}\n"
        f"data: {json.dumps(payload)}\n\n"
    ).encode("utf-8")


def completed_sse(output, response_id="resp_1"):
    chunks = []
    for index, item in enumerate(output):
        chunks.append(
            event(
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "output_index": index,
                    "item": item,
                },
            )
        )
    chunks.append(
        event(
            "response.completed",
            {
                "type": "response.completed",
                "response": {
                    "id": response_id,
                    "status": "completed",
                    "output": output,
                },
            },
        )
    )
    return b"".join(chunks)


def observe(payload):
    observation = codex_completion_gate.ResponsesCompletionObservation()
    midpoint = len(payload) // 2
    observation.feed(payload[:midpoint])
    observation.feed(payload[midpoint:])
    observation.finish()
    return observation


class ResponsesCompletionObservationTests(unittest.TestCase):
    def test_reasoning_text_without_action_requires_check_regardless_of_words(self):
        output = [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "任意 응답 arbitrary"}],
            },
        ]
        observation = observe(completed_sse(output))

        self.assertTrue(
            codex_completion_gate.request_requires_completion_check(
                {"tools": [{"type": "function", "name": "shell"}]}, observation
            )
        )

    def test_protocol_action_skips_check_without_tool_name_lists(self):
        output = [
            {"type": "reasoning", "summary": []},
            {"type": "future_action_type", "id": "action_1"},
        ]
        observation = observe(completed_sse(output))

        self.assertTrue(observation.has_action)
        self.assertFalse(
            codex_completion_gate.request_requires_completion_check(
                {"tools": [{"type": "function", "name": "shell"}]}, observation
            )
        )

    def test_exact_private_token_confirms_completion(self):
        output = [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": CODEX_COMPLETION_CONFIRMED}
                ],
            }
        ]
        self.assertTrue(observe(completed_sse(output)).completion_confirmed)

    def test_stateless_check_replays_output_and_keeps_stable_prefix(self):
        output = [
            {"type": "reasoning", "encrypted_content": "sealed"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "candidate"}],
            },
        ]
        observation = observe(completed_sse(output))
        original = {
            "instructions": "stable",
            "store": False,
            "input": [{"type": "message", "role": "user", "content": "work"}],
        }

        projected = codex_completion_gate.completion_check_body(
            original, observation
        )

        self.assertEqual("stable", projected["instructions"])
        self.assertEqual("sealed", projected["input"][-3]["encrypted_content"])
        self.assertEqual("user", projected["input"][-1]["role"])
        self.assertIn(CODEX_COMPLETION_CONFIRMED, projected["input"][-1]["content"][0]["text"])
        self.assertEqual(1, len(original["input"]))

    def test_stored_check_uses_previous_response_id(self):
        output = [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "candidate"}],
            },
        ]
        observation = observe(completed_sse(output, response_id="resp_saved"))

        projected = codex_completion_gate.completion_check_body(
            {"store": True, "input": "work"}, observation
        )

        self.assertEqual("resp_saved", projected["previous_response_id"])
        self.assertEqual(1, len(projected["input"]))


if __name__ == "__main__":
    unittest.main()
