import unittest

from ciel_runtime_support.native_context_recovery import (
    ContextOverflow,
    parse_context_overflow,
    recover_output_budget,
)


ERROR = (
    "API Error: 400 This model's maximum context length is 1048576 tokens. "
    "However, you requested 1049305 tokens (918233 in the messages, "
    "131072 in the completion). Please reduce the length."
)


class NativeContextRecoveryTests(unittest.TestCase):
    def test_parses_deepseek_context_error(self):
        self.assertEqual(
            ContextOverflow(1048576, 918233, 131072),
            parse_context_overflow(ERROR),
        )

    def test_reduces_only_output_budget_and_preserves_request(self):
        body = {
            "model": "deepseek-v4-flash[1m]",
            "messages": [{"role": "user", "content": "keep me"}],
            "max_tokens": 131072,
            "stream": True,
        }
        recovered = recover_output_budget(body, ERROR, reserve_tokens=8192)
        self.assertIsNotNone(recovered)
        self.assertEqual(122151, recovered["max_tokens"])
        self.assertEqual(body["messages"], recovered["messages"])
        self.assertEqual(131072, body["max_tokens"])

    def test_does_not_retry_when_prompt_itself_needs_compaction(self):
        raw = (
            "maximum context length is 1000 tokens; requested 1400 tokens "
            "(1100 in the messages, 300 in the completion)"
        )
        self.assertIsNone(
            recover_output_budget({"max_tokens": 300}, raw, reserve_tokens=10)
        )

    def test_ignores_unrelated_bad_request(self):
        self.assertIsNone(recover_output_budget({"max_tokens": 100}, "bad tool schema"))


if __name__ == "__main__":
    unittest.main()
