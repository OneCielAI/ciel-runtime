import unittest

from ciel_runtime_support.protocols.openai_responses import (
    anthropic_message_to_openai_response,
)
from ciel_runtime_support.responses_input_compatibility import (
    drop_rejected_tool_pair,
    is_router_synthesized_item_id,
    parse_missing_tool_output_call_id,
    repair_replayed_response_items,
)


class ResponsesInputCompatibilityTests(unittest.TestCase):
    def test_parses_the_call_id_from_a_missing_custom_tool_output_error(self):
        error = (
            "invalid_request_error: No tool output found for custom tool call "
            "call_aul297D4jWf5nUXpEmbine7E."
        )

        self.assertEqual(
            "call_aul297D4jWf5nUXpEmbine7E",
            parse_missing_tool_output_call_id(error),
        )

    def test_drops_only_the_tool_pair_named_by_the_upstream(self):
        rejected = "call_aul297D4jWf5nUXpEmbine7E"
        body = {
            "input": [
                {"type": "message", "role": "user"},
                {
                    "type": "custom_tool_call",
                    "call_id": rejected,
                    "name": "apply_patch",
                    "input": "patch",
                },
                {
                    "type": "custom_tool_call_output",
                    "call_id": rejected,
                    "output": "done",
                },
                {
                    "type": "function_call",
                    "call_id": "call_preserved",
                    "name": "shell",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_preserved",
                    "output": "ok",
                },
            ]
        }

        repaired, removed = drop_rejected_tool_pair(
            body,
            f"No tool output found for custom tool call {rejected}.",
        )

        self.assertEqual(2, removed)
        self.assertEqual(
            ["message", "function_call", "function_call_output"],
            [item["type"] for item in repaired["input"]],
        )
        self.assertEqual(5, len(body["input"]))

    def test_does_not_modify_history_for_an_unrecognized_error(self):
        body = {"input": [{"type": "message", "role": "user"}]}

        repaired, removed = drop_rejected_tool_pair(body, "Unknown model")

        self.assertIs(body, repaired)
        self.assertEqual(0, removed)

    def test_drops_foreign_summary_only_reasoning_item(self):
        body = {
            "input": [
                {"type": "message", "id": "msg_user", "role": "user"},
                {
                    "type": "reasoning",
                    "id": "msg_wrong",
                    "summary": [{"type": "summary_text", "text": "hidden"}],
                    "encrypted_content": None,
                },
                {"type": "message", "id": "msg_answer", "role": "assistant"},
            ]
        }

        repaired = repair_replayed_response_items(body)

        self.assertEqual(
            ["msg_user", "msg_answer"],
            [item["id"] for item in repaired["input"]],
        )
        self.assertEqual(3, len(body["input"]))

    def test_preserves_valid_openai_reasoning_item(self):
        body = {
            "input": [
                {
                    "type": "reasoning",
                    "id": "rs_valid",
                    "encrypted_content": "ciphertext",
                }
            ]
        }

        self.assertIs(body, repair_replayed_response_items(body))

    def test_omits_foreign_id_when_encrypted_content_can_be_replayed(self):
        body = {
            "input": [
                {
                    "type": "reasoning",
                    "id": "msg_wrong",
                    "encrypted_content": "ciphertext",
                }
            ]
        }

        repaired = repair_replayed_response_items(body)

        self.assertEqual(
            {"type": "reasoning", "encrypted_content": "ciphertext"},
            repaired["input"][0],
        )
        self.assertEqual("msg_wrong", body["input"][0]["id"])

    def test_omits_foreign_function_call_id_without_breaking_call_pair(self):
        body = {
            "input": [
                {
                    "type": "function_call",
                    "id": "msg_f47c5769-b157-4107-ac7b-d0527e028c6f",
                    "name": "shell_command",
                    "arguments": '{"command":"Get-ChildItem"}',
                    "call_id": "call_09149e78911e4e609b960a10",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_09149e78911e4e609b960a10",
                    "output": "completed",
                },
            ]
        }

        repaired = repair_replayed_response_items(body)

        self.assertNotIn("id", repaired["input"][0])
        self.assertEqual(
            "call_09149e78911e4e609b960a10",
            repaired["input"][0]["call_id"],
        )
        self.assertEqual(
            "call_09149e78911e4e609b960a10",
            repaired["input"][1]["call_id"],
        )
        self.assertEqual("msg_f47c5769-b157-4107-ac7b-d0527e028c6f", body["input"][0]["id"])

    def test_omits_foreign_custom_tool_call_id_without_breaking_call_pair(self):
        body = {
            "input": [
                {
                    "type": "custom_tool_call",
                    "id": "msg_c76ffd2d-dcfb-45f0-bbe8-cf3d8f531c80",
                    "name": "apply_patch",
                    "input": "*** Begin Patch",
                    "call_id": "call_60aee6f24e0f440381c86e96",
                },
                {
                    "type": "custom_tool_call_output",
                    "id": "ctco_valid",
                    "call_id": "call_60aee6f24e0f440381c86e96",
                    "output": "Done!",
                },
            ]
        }

        repaired = repair_replayed_response_items(body)

        self.assertNotIn("id", repaired["input"][0])
        self.assertEqual("ctco_valid", repaired["input"][1]["id"])
        self.assertEqual(
            repaired["input"][0]["call_id"],
            repaired["input"][1]["call_id"],
        )

    def test_preserves_valid_ids_for_each_observed_response_item_type(self):
        body = {
            "input": [
                {"type": "message", "id": "msg_valid"},
                {"type": "reasoning", "id": "rs_valid"},
                {"type": "function_call", "id": "fc_valid", "call_id": "call_1"},
                {
                    "type": "function_call_output",
                    "id": "fco_valid",
                    "call_id": "call_1",
                },
                {
                    "type": "custom_tool_call",
                    "id": "ctc_valid",
                    "call_id": "call_2",
                },
                {
                    "type": "custom_tool_call_output",
                    "id": "ctco_valid",
                    "call_id": "call_2",
                },
            ]
        }

        self.assertIs(body, repair_replayed_response_items(body))

    def test_drops_orphan_custom_tool_calls_and_outputs(self):
        body = {
            "input": [
                {"type": "message", "id": "msg_before", "role": "assistant"},
                {
                    "type": "custom_tool_call",
                    "id": "ctc_orphan",
                    "call_id": "call_TqE66SGyCQxJYqXPdlJBpM98",
                    "name": "apply_patch",
                    "input": "patch",
                },
                {
                    "type": "custom_tool_call_output",
                    "id": "ctco_orphan",
                    "call_id": "call_output_without_call",
                    "output": "done",
                },
                {"type": "message", "id": "msg_after", "role": "user"},
            ]
        }

        repaired = repair_replayed_response_items(body)

        self.assertEqual(
            ["msg_before", "msg_after"],
            [item["id"] for item in repaired["input"]],
        )
        self.assertEqual(4, len(body["input"]))

    def test_keeps_complete_function_and_custom_tool_pairs(self):
        body = {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_function",
                    "name": "shell",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_function",
                    "output": "ok",
                },
                {
                    "type": "custom_tool_call",
                    "call_id": "call_custom",
                    "name": "apply_patch",
                    "input": "patch",
                },
                {
                    "type": "custom_tool_call_output",
                    "call_id": "call_custom",
                    "output": "done",
                },
            ]
        }

        self.assertIs(body, repair_replayed_response_items(body))

    def test_drops_paired_replayed_call_with_invalid_tool_name(self):
        prefix = [
            {"type": "message", "role": "user", "content": f"history {index}"}
            for index in range(696)
        ]
        body = {
            "input": prefix
            + [
                {
                    "type": "function_call",
                    "call_id": "call_invalid_parallel",
                    "name": "multi_tool_use.parallel",
                    "arguments": '{"tool_calls":[]}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_invalid_parallel",
                    "output": "unsupported call: multi_tool_use.parallel",
                },
                {
                    "type": "function_call",
                    "call_id": "call_valid_parallel",
                    "name": "multi_tool_use_parallel",
                    "arguments": '{"tool_calls":[]}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_valid_parallel",
                    "output": "completed",
                },
            ]
        }

        self.assertEqual("multi_tool_use.parallel", body["input"][696]["name"])

        repaired = repair_replayed_response_items(body)

        self.assertEqual(698, len(repaired["input"]))
        self.assertEqual(
            ["call_valid_parallel", "call_valid_parallel"],
            [item["call_id"] for item in repaired["input"][-2:]],
        )
        self.assertEqual("multi_tool_use.parallel", body["input"][696]["name"])

    def test_drops_cross_type_call_and_output_with_the_same_id(self):
        body = {
            "input": [
                {
                    "type": "custom_tool_call",
                    "call_id": "call_shared",
                    "name": "apply_patch",
                    "input": "patch",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_shared",
                    "output": "wrong output type",
                },
                {"type": "message", "id": "msg_after", "role": "user"},
            ]
        }

        repaired = repair_replayed_response_items(body)

        self.assertEqual(["message"], [item["type"] for item in repaired["input"]])


class RouterSynthesizedItemIdTests(unittest.TestCase):
    # IDs sampled from the rollout of session 019fd648. The router's own were
    # the only ones the OpenAI backend rejected as never stored.
    ROUTER_MINTED = ("rs_e50d87c9_0", "rs_fb40f0bb_0", "fc_c55b5972_1", "msg_c55b5972_0")
    UPSTREAM_ISSUED = (
        "rs_0b9eabb999711d12016a755d56dddc81978d609657b795ba6b",
        "fc_0b9eabb999711d12016a755e3465a88197b1c4c29d90ff0789",
        "msg_0a1842bd470bc478016a755d3deedc8197a20573726d28fed7",
        "fco_019fd660-738b-7bc1-a03d-7dc676d58d15",
        "ctco_019fd67b-18f4-7d43-9bc1-73ed7afc0acc",
        "msg_019fd64e-1892-7f33-99c0-9e1c5087a5be",
    )

    def test_recognizes_only_locally_minted_ids(self):
        for item_id in self.ROUTER_MINTED:
            self.assertTrue(is_router_synthesized_item_id(item_id), item_id)
        for item_id in self.UPSTREAM_ISSUED:
            self.assertFalse(is_router_synthesized_item_id(item_id), item_id)

    def test_projection_mints_ids_the_repair_recognizes(self):
        projected = anthropic_message_to_openai_response(
            {
                "model": "deepseek-v4-flash",
                "content": [
                    {"type": "thinking", "thinking": "considering"},
                    {"type": "text", "text": "answer"},
                    {"type": "tool_use", "id": "call_1", "name": "Read", "input": {}},
                ],
            }
        )

        ids = [item["id"] for item in projected["output"]]
        self.assertEqual(3, len(ids))
        for item_id in ids:
            self.assertTrue(is_router_synthesized_item_id(item_id), item_id)

    def test_drops_a_replayed_router_minted_reasoning_summary(self):
        body = {
            "input": [
                {"type": "message", "id": "msg_019fd64e-1892-7f33-99c0-9e1c5087a5be"},
                {
                    "type": "reasoning",
                    "id": "rs_e50d87c9_0",
                    "summary": [{"type": "summary_text", "text": "hidden"}],
                    "encrypted_content": None,
                },
            ]
        }

        repaired = repair_replayed_response_items(body)

        self.assertEqual(
            ["msg_019fd64e-1892-7f33-99c0-9e1c5087a5be"],
            [item["id"] for item in repaired["input"]],
        )

    def test_drops_router_minted_tool_call_when_output_is_missing(self):
        body = {
            "input": [
                {
                    "type": "function_call",
                    "id": "fc_c55b5972_1",
                    "name": "shell",
                    "arguments": "{}",
                    "call_id": "call_1",
                },
                {"type": "message", "id": "msg_c55b5972_0", "role": "assistant"},
            ]
        }

        repaired = repair_replayed_response_items(body)

        self.assertEqual(1, len(repaired["input"]))
        self.assertNotIn("id", repaired["input"][0])
        self.assertEqual("assistant", repaired["input"][0]["role"])

    def test_upstream_issued_ids_survive_untouched(self):
        body = {"input": [{"type": "reasoning", "id": self.UPSTREAM_ISSUED[0]}]}

        self.assertIs(body, repair_replayed_response_items(body))


if __name__ == "__main__":
    unittest.main()
