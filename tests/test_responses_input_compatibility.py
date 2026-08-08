import unittest

from ciel_runtime_support.responses_input_compatibility import (
    repair_replayed_response_items,
)


class ResponsesInputCompatibilityTests(unittest.TestCase):
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

    def test_drops_foreign_reasoning_item_carrying_sealed_content(self):
        body = {
            "input": [
                {"type": "message", "id": "msg_user", "role": "user"},
                {
                    "type": "reasoning",
                    "id": "msg_wrong",
                    "encrypted_content": "ciphertext",
                },
            ]
        }

        repaired = repair_replayed_response_items(body)

        self.assertEqual(
            ["msg_user"],
            [item["id"] for item in repaired["input"]],
        )
        self.assertEqual("msg_wrong", body["input"][1]["id"])

    def test_drops_reasoning_item_whose_id_is_an_opaque_provider_blob(self):
        # Observed verbatim in a recorded session: the GitHub Copilot OAuth
        # provider issues reasoning items whose ``id`` is a long base64 blob
        # rather than any ``<prefix>_`` form, alongside sealed content that only
        # that provider can decrypt.  Replaying it on an OpenAI Codex account
        # failed with "The encrypted content oPYR...Lj8= could not be verified".
        blob_id = "nKPwaSTbj05qDK4mFh8kbydE2R7pv3DwxXBK2ctm7O3+RFL2Hgw"
        sealed = "oPYRvsBFolJ6qKAqnqoy3TG8SRyvTwmLLj8="
        body = {
            "input": [
                {
                    "type": "reasoning",
                    "id": blob_id,
                    "encrypted_content": sealed,
                    "summary": [],
                },
                {
                    "type": "function_call",
                    "id": "fc_valid",
                    "name": "shell_command",
                    "arguments": "{}",
                    "call_id": "call_kept",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_kept",
                    "output": "ok",
                },
                {"type": "message", "role": "user", "content": "continue"},
            ]
        }

        repaired = repair_replayed_response_items(body)

        serialized = str(repaired["input"])
        self.assertNotIn(sealed, serialized)
        self.assertNotIn(blob_id, serialized)
        self.assertEqual(
            ["function_call", "function_call_output", "message"],
            [item["type"] for item in repaired["input"]],
        )
        self.assertEqual("call_kept", repaired["input"][0]["call_id"])
        self.assertEqual("call_kept", repaired["input"][1]["call_id"])
        self.assertEqual(blob_id, body["input"][0]["id"])

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

    def test_omits_foreign_ids_on_item_types_without_a_named_prefix(self):
        # `web_search_call` and `tool_search_call` are absent from the prefix
        # map, and the recorded session carries both with opaque provider IDs.
        # Naming types one at a time only moves the rejection to the next
        # unlisted type, so an unnamed type still must not replay a foreign ID.
        body = {
            "input": [
                {"type": "web_search_call", "id": "zV0zU+ryiYL8sNshQzds5MTC"},
                {"type": "tool_search_call", "id": "9KW9oBKs0r6mmXGJZbfnAR+D"},
                {"type": "tool_search_output", "id": "tso_native"},
            ]
        }

        repaired = repair_replayed_response_items(body)

        self.assertNotIn("id", repaired["input"][0])
        self.assertNotIn("id", repaired["input"][1])
        self.assertEqual("tso_native", repaired["input"][2]["id"])
        self.assertEqual(
            ["web_search_call", "tool_search_call", "tool_search_output"],
            [item["type"] for item in repaired["input"]],
        )
        self.assertEqual("zV0zU+ryiYL8sNshQzds5MTC", body["input"][0]["id"])

    def test_drops_reasoning_with_opaque_id_even_without_a_named_prefix(self):
        body = {
            "input": [
                {
                    "type": "reasoning",
                    "id": "PSXs7lW68Z4lNB2fl1sOS0FE8/NiJw8VbdORl35x5oal",
                    "encrypted_content": "sealed-elsewhere",
                },
                {"type": "message", "id": "msg_keep", "role": "user"},
            ]
        }

        repaired = repair_replayed_response_items(body)

        self.assertEqual(["msg_keep"], [item["id"] for item in repaired["input"]])

    def test_preserves_valid_ids_for_each_observed_response_item_type(self):
        body = {
            "input": [
                {"type": "message", "id": "msg_valid"},
                {"type": "reasoning", "id": "rs_valid"},
                {"type": "function_call", "id": "fc_valid"},
                {"type": "function_call_output", "id": "fco_valid"},
                {"type": "custom_tool_call", "id": "ctc_valid"},
                {"type": "custom_tool_call_output", "id": "ctco_valid"},
            ]
        }

        self.assertIs(body, repair_replayed_response_items(body))


if __name__ == "__main__":
    unittest.main()
