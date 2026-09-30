import json
import unittest

from ciel_runtime_support import codex_completion_gate
from ciel_runtime_support.codex_turn_recovery import CODEX_COMPLETION_TOOL_NAME


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

    def test_tool_result_followed_by_no_reasoning_text_requires_check(self):
        output = [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "任意 응답 arbitrary"}],
            }
        ]
        observation = observe(completed_sse(output))
        body = {
            "tools": [{"type": "function", "name": "shell"}],
            "input": [
                {"type": "function_call_output", "call_id": "call_1", "output": "ok"}
            ],
        }

        self.assertFalse(observation.has_reasoning)
        self.assertTrue(
            codex_completion_gate.request_requires_completion_check(body, observation)
        )

    def test_text_only_final_is_checked_whatever_preceded_it(self):
        output = [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "任意 응답 arbitrary"}],
            }
        ]
        observation = observe(completed_sse(output))
        tools = [{"type": "function", "name": "shell"}]
        preceding = {
            "user message": {"type": "message", "role": "user", "content": "do it"},
            "compaction summary": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Another language model…"}],
            },
            "tool output": {"type": "custom_tool_call_output", "call_id": "c", "output": ""},
        }

        for name, item in preceding.items():
            with self.subTest(name):
                self.assertTrue(
                    codex_completion_gate.request_requires_completion_check(
                        {"tools": tools, "input": [item]}, observation
                    )
                )

    def test_request_that_cannot_act_is_never_checked(self):
        output = [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "summary"}],
            }
        ]
        observation = observe(completed_sse(output))
        tools = [{"type": "function", "name": "shell"}]

        for body in ({"input": []}, {"tools": [], "input": []}, {"tools": tools, "tool_choice": "none"}):
            with self.subTest(body=body):
                self.assertFalse(
                    codex_completion_gate.request_requires_completion_check(
                        body, observation
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

    def test_private_completion_tool_confirms_completion(self):
        output = [
            {
                "type": "function_call",
                "call_id": "call_complete",
                "name": CODEX_COMPLETION_TOOL_NAME,
                "arguments": "{}",
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
        self.assertEqual("required", projected["tool_choice"])
        self.assertEqual(CODEX_COMPLETION_TOOL_NAME, projected["tools"][-1]["name"])
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


    @staticmethod
    def code_mode_body():
        # codex-cli 0.159.2 with gpt-6-astra (tool_mode "code_mode_only"),
        # captured 2026-09-29: no top-level tools, the catalogue rides in an
        # additional_tools input item.
        return {
            "model": "gpt-6-astra",
            "store": False,
            "tool_choice": "auto",
            "input": [
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [
                        {
                            "type": "namespace",
                            "name": "functions",
                            "tools": [
                                {"type": "custom", "name": "exec"},
                                {"type": "function", "name": "wait", "parameters": {}},
                            ],
                        },
                        {
                            "type": "namespace",
                            "name": "clock",
                            "tools": [{"type": "function", "name": "sleep", "parameters": {}}],
                        },
                    ],
                },
                {"type": "message", "role": "user", "content": "work"},
            ],
        }

    def test_additional_tools_catalogue_is_checked(self):
        output = [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "work remains"}],
            }
        ]
        observation = observe(completed_sse(output))
        body = self.code_mode_body()

        self.assertTrue(codex_completion_gate.request_offers_tools(body))
        self.assertTrue(
            codex_completion_gate.request_requires_completion_check(body, observation)
        )
        empty = {"input": [{"type": "additional_tools", "tools": []}]}
        self.assertFalse(codex_completion_gate.request_offers_tools(empty))
        self.assertFalse(
            codex_completion_gate.request_requires_completion_check(
                {**body, "tool_choice": "none"}, observation
            )
        )

    def test_code_mode_check_adds_the_private_tool_to_the_functions_namespace(self):
        output = [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "candidate"}],
            }
        ]
        observation = observe(completed_sse(output))
        original = self.code_mode_body()

        projected = codex_completion_gate.completion_check_body(original, observation)

        self.assertNotIn("tools", projected)
        self.assertEqual("required", projected["tool_choice"])
        catalogue = projected["input"][0]
        self.assertEqual("additional_tools", catalogue["type"])
        functions, clock = catalogue["tools"]
        self.assertEqual(
            ["exec", "wait", CODEX_COMPLETION_TOOL_NAME],
            [tool["name"] for tool in functions["tools"]],
        )
        self.assertEqual(["sleep"], [tool["name"] for tool in clock["tools"]])
        self.assertEqual("candidate", projected["input"][-2]["content"][0]["text"])
        self.assertEqual("user", projected["input"][-1]["role"])
        self.assertEqual(2, len(original["input"][0]["tools"][0]["tools"]))

    def test_stored_code_mode_check_keeps_the_catalogue(self):
        output = [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "candidate"}],
            }
        ]
        observation = observe(completed_sse(output, response_id="resp_saved"))
        body = {**self.code_mode_body(), "store": True}

        projected = codex_completion_gate.completion_check_body(body, observation)

        self.assertEqual("resp_saved", projected["previous_response_id"])
        self.assertEqual(
            ["additional_tools", "message"],
            [item["type"] for item in projected["input"]],
        )
        self.assertIn(
            CODEX_COMPLETION_TOOL_NAME,
            [tool["name"] for tool in projected["input"][0]["tools"][0]["tools"]],
        )


if __name__ == "__main__":
    unittest.main()
