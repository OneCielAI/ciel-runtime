import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.codex_reasoning_rejects import (
    RejectedReasoningStore,
    drop_reasoning_matching_verdict,
    drop_rejected_reasoning,
    encrypted_content_digest,
    parse_unverifiable_encrypted_content,
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
        logs = []
        with tempfile.TemporaryDirectory() as state:
            blocker = Path(state) / "occupied"
            blocker.write_text("not a directory", encoding="utf-8")
            store = RejectedReasoningStore(
                blocker / "store.json",
                lambda level, message: logs.append((level, message)),
            )
            store.add("sealed")
        self.assertEqual(1, len(logs))
        self.assertEqual("WARN", logs[0][0])


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
