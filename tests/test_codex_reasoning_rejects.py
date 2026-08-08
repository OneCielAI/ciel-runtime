import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.codex_reasoning_rejects import (
    RejectedReasoningStore,
    drop_reasoning_matching_verdict,
    drop_rejected_reasoning,
    encrypted_content_digest,
    parse_missing_item_id,
    parse_unverifiable_encrypted_content,
    repair_missing_item,
)

# Verbatim upstream verdict from the reproduced failure (session 019fd648,
# replayed on an OpenAI Codex account after routed GitHub Copilot turns).
VERDICT_TEXT = (
    '{"detail": "invalid_request_error: The encrypted content oPYR...Lj8= '
    'could not be verified. Reason: Encrypted content could not be decrypted '
    'or parsed."}'
)


class ParseVerdictTests(unittest.TestCase):
    def test_parses_head_and_tail_from_upstream_error(self):
        self.assertEqual(("oPYR", "Lj8="), parse_unverifiable_encrypted_content(VERDICT_TEXT))

    def test_ignores_unrelated_errors(self):
        self.assertIsNone(parse_unverifiable_encrypted_content('{"detail": "model_capacity"}'))
        self.assertIsNone(parse_unverifiable_encrypted_content(""))


class DropMatchingVerdictTests(unittest.TestCase):
    def test_drops_only_the_named_ciphertext(self):
        named = "oPYRvsBFolJ6qKAqnqoy3TG8SRyvTwmLLj8="
        body = {
            "input": [
                {"type": "reasoning", "encrypted_content": named, "summary": []},
                {"type": "reasoning", "encrypted_content": "otherSealedContent="},
                {"type": "message", "role": "user", "content": "continue"},
            ]
        }

        projected, sealed = drop_reasoning_matching_verdict(body, "oPYR", "Lj8=")

        self.assertEqual(named, sealed)
        self.assertEqual(
            ["reasoning", "message"],
            [item["type"] for item in projected["input"]],
        )
        self.assertEqual(3, len(body["input"]))

    def test_returns_no_match_without_mutating(self):
        body = {"input": [{"type": "reasoning", "encrypted_content": "abc="}]}

        projected, sealed = drop_reasoning_matching_verdict(body, "oPYR", "Lj8=")

        self.assertIsNone(sealed)
        self.assertIs(body, projected)


class MissingItemVerdictTests(unittest.TestCase):
    # Verbatim upstream verdict from session 019fd648 after the model changed:
    # the reasoning item had been minted by the router while a foreign provider
    # answered a Codex client, so no OpenAI backend had ever stored it.
    MISSING_TEXT = (
        '{"type": "error", "error": {"type": "invalid_request_error", "message": '
        "\"Item with id 'rs_e50d87c9_0' not found. Items are not persisted when "
        "`store` is set to false. Try again with `store` set to true, or remove "
        'this item from your input."}}'
    )

    def test_parses_the_named_item_id(self):
        self.assertEqual("rs_e50d87c9_0", parse_missing_item_id(self.MISSING_TEXT))

    def test_ignores_unrelated_errors(self):
        self.assertIsNone(parse_missing_item_id('{"detail": "model_capacity"}'))
        self.assertIsNone(parse_missing_item_id(""))

    def test_drops_the_named_summary_only_reasoning_item(self):
        body = {
            "input": [
                {"type": "message", "id": "msg_keep", "role": "user"},
                {
                    "type": "reasoning",
                    "id": "rs_e50d87c9_0",
                    "summary": [{"type": "summary_text", "text": "hidden"}],
                    "encrypted_content": None,
                },
            ]
        }

        repaired, outcome = repair_missing_item(body, "rs_e50d87c9_0")

        self.assertEqual("dropped", outcome)
        self.assertEqual(["msg_keep"], [item["id"] for item in repaired["input"]])
        self.assertEqual(2, len(body["input"]))

    def test_keeps_a_tool_call_and_omits_only_its_unknown_id(self):
        body = {
            "input": [
                {
                    "type": "function_call",
                    "id": "fc_c55b5972_1",
                    "name": "shell",
                    "arguments": "{}",
                    "call_id": "call_1",
                },
                {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
            ]
        }

        repaired, outcome = repair_missing_item(body, "fc_c55b5972_1")

        self.assertEqual("id_omitted", outcome)
        self.assertNotIn("id", repaired["input"][0])
        self.assertEqual("call_1", repaired["input"][0]["call_id"])
        self.assertEqual("shell", repaired["input"][0]["name"])
        self.assertEqual("fc_c55b5972_1", body["input"][0]["id"])

    def test_sealed_reasoning_keeps_its_content_when_the_id_is_unknown(self):
        body = {
            "input": [
                {"type": "reasoning", "id": "rs_unknown", "encrypted_content": "sealed="}
            ]
        }

        repaired, outcome = repair_missing_item(body, "rs_unknown")

        self.assertEqual("id_omitted", outcome)
        self.assertEqual(
            {"type": "reasoning", "encrypted_content": "sealed="},
            repaired["input"][0],
        )

    def test_an_absent_id_cannot_be_repaired(self):
        body = {"input": [{"type": "message", "id": "msg_other"}]}

        repaired, outcome = repair_missing_item(body, "rs_e50d87c9_0")

        self.assertIsNone(outcome)
        self.assertIs(body, repaired)


class RejectedStoreTests(unittest.TestCase):
    def test_round_trips_verdicts_across_instances(self):
        with tempfile.TemporaryDirectory() as state:
            path = Path(state) / "codex-rejected-reasoning.json"
            store = RejectedReasoningStore(path, lambda _level, _message: None)
            self.assertFalse(store.contains("sealed-one"))

            store.add("sealed-one")

            reloaded = RejectedReasoningStore(path, lambda _level, _message: None)
            self.assertTrue(reloaded.contains("sealed-one"))
            self.assertFalse(reloaded.contains("sealed-two"))
            recorded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                [encrypted_content_digest("sealed-one")], recorded["sha256"]
            )

    def test_write_failure_only_logs(self):
        # A path beneath a regular file cannot be created or written. Reading
        # it raises FileNotFoundError on Windows but NotADirectoryError on
        # POSIX, so the load step may or may not add its own warning — assert
        # the write failure is logged and nothing raises, not an exact count.
        logs = []
        with tempfile.TemporaryDirectory() as state:
            blocker = Path(state) / "occupied"
            blocker.write_text("not a directory", encoding="utf-8")
            store = RejectedReasoningStore(
                blocker / "store.json",
                lambda level, message: logs.append((level, message)),
            )
            store.add("sealed")
        self.assertTrue(logs)
        self.assertTrue(all(level == "WARN" for level, _message in logs))
        self.assertIn("rejected_reasoning_store_write_failed", logs[-1][1])


class PrefilterTests(unittest.TestCase):
    def test_drops_previously_rejected_ciphertexts(self):
        body = {
            "input": [
                {"type": "reasoning", "encrypted_content": "rejected="},
                {"type": "reasoning", "encrypted_content": "accepted="},
                {"type": "message", "role": "user", "content": "hello"},
            ]
        }

        projected, dropped = drop_rejected_reasoning(
            body, lambda sealed: sealed == "rejected="
        )

        self.assertEqual(1, dropped)
        self.assertEqual(
            ["reasoning", "message"],
            [item["type"] for item in projected["input"]],
        )
        self.assertEqual("accepted=", projected["input"][0]["encrypted_content"])

    def test_clean_body_is_returned_unchanged(self):
        body = {"input": [{"type": "message", "role": "user", "content": "hi"}]}

        projected, dropped = drop_rejected_reasoning(body, lambda _sealed: True)

        self.assertEqual(0, dropped)
        self.assertIs(body, projected)


if __name__ == "__main__":
    unittest.main()
