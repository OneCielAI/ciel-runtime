import unittest

from ciel_runtime_support.responses_input_compatibility import (
    repair_replayed_reasoning_items,
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

        repaired = repair_replayed_reasoning_items(body)

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

        self.assertIs(body, repair_replayed_reasoning_items(body))

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

        repaired = repair_replayed_reasoning_items(body)

        self.assertEqual(
            {"type": "reasoning", "encrypted_content": "ciphertext"},
            repaired["input"][0],
        )
        self.assertEqual("msg_wrong", body["input"][0]["id"])


if __name__ == "__main__":
    unittest.main()
