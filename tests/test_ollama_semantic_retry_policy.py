import copy
import unittest

from ciel_runtime_support.ollama_semantic_retry import (
    REASON_EMPTY_RESPONSE,
    REASON_REASONING_ONLY,
    SEMANTIC_RETRIES_CONFIG_KEY,
    SEMANTIC_RETRY_INSTRUCTION,
    collected_retry_reason,
    configured_retries,
    exhausted_notice,
    replay_body,
    retry_reason,
)
from ciel_runtime_support.remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER

KEY = SEMANTIC_RETRIES_CONFIG_KEY


def reason(**overrides):
    values = {
        "terminal_received": True,
        "done_reason": "stop",
        "text": "",
        "thinking": "",
        "tool_intent": False,
        "invalid": False,
        "runaway": False,
    }
    values.update(overrides)
    return retry_reason(**values)


def collected(message=None, **envelope):
    data = {"message": {"role": "assistant", "content": ""} if message is None else message,
            "done": True, "done_reason": "stop"}
    data.update(envelope)
    return data


class ConfiguredRetriesTests(unittest.TestCase):
    def test_default_is_one(self):
        self.assertEqual("empty_response_retries", SEMANTIC_RETRIES_CONFIG_KEY)
        self.assertEqual(1, configured_retries({}))
        self.assertEqual(1, configured_retries({KEY: None}))
        self.assertEqual(1, configured_retries(None))

    def test_values_are_clamped_to_zero_through_two(self):
        for value, expected in ((0, 0), (-3, 0), (1, 1), (2, 2), (99, 2), (10**400, 2)):
            with self.subTest(value=value):
                self.assertEqual(expected, configured_retries({KEY: value}))

    def test_non_integer_values_use_the_default(self):
        for value in (True, False, 2.5, 0.0, "2", "bad", [], {}, float("nan")):
            with self.subTest(value=value):
                self.assertEqual(1, configured_retries({KEY: value}))

    def test_remote_bridge_config_never_replays(self):
        self.assertEqual(
            0,
            configured_retries({KEY: 2, REMOTE_BRIDGE_CONFIG_MARKER: True}),
        )


class RetryReasonTests(unittest.TestCase):
    def test_clean_empty_stop_is_empty_response(self):
        self.assertEqual(REASON_EMPTY_RESPONSE, reason())
        self.assertEqual(REASON_EMPTY_RESPONSE, reason(text=" \n\t", thinking="  "))

    def test_clean_reasoning_only_stop_is_reasoning_only(self):
        self.assertEqual(REASON_REASONING_ONLY, reason(thinking="private thought"))

    def test_done_reason_is_normalized(self):
        self.assertEqual(REASON_EMPTY_RESPONSE, reason(done_reason=" STOP "))

    def test_anything_but_a_valid_stop_terminal_never_replays(self):
        cases = {
            "visible text": {"text": "answer"},
            "tool intent": {"tool_intent": True},
            "invalid payload": {"invalid": True},
            "runaway stop": {"runaway": True},
            "no terminal": {"terminal_received": False},
            "length stop": {"done_reason": "length", "thinking": "thought"},
            "missing done reason": {"done_reason": ""},
            "load": {"done_reason": "load"},
        }
        for label, overrides in cases.items():
            with self.subTest(case=label):
                self.assertEqual("", reason(**overrides))


class CollectedRetryReasonTests(unittest.TestCase):
    def test_empty_and_reasoning_only_responses_qualify(self):
        self.assertEqual(REASON_EMPTY_RESPONSE, collected_retry_reason(collected()))
        self.assertEqual(
            REASON_REASONING_ONLY,
            collected_retry_reason(collected({"content": "", "thinking": "thought"})),
        )

    def test_hidden_thinking_markup_is_not_visible_text(self):
        self.assertEqual(
            REASON_EMPTY_RESPONSE,
            collected_retry_reason(collected({"content": "<think>hidden</think>"})),
        )

    def test_tool_intent_excludes_replay_even_when_malformed(self):
        for calls in (
            [{"function": {"name": "Read", "arguments": {}}}],
            [{"function": {}}],
            ["not-a-call"],
            "not-a-list",
        ):
            with self.subTest(calls=calls):
                self.assertEqual(
                    "",
                    collected_retry_reason(collected({"content": "", "tool_calls": calls})),
                )

    def test_empty_tool_call_list_is_not_tool_intent(self):
        self.assertEqual(
            REASON_EMPTY_RESPONSE,
            collected_retry_reason(collected({"content": "", "tool_calls": []})),
        )

    def test_text_errors_and_incomplete_envelopes_never_replay(self):
        cases = [
            collected({"content": "answer"}),
            collected(error="upstream failed"),
            collected(done=False),
            collected(done_reason="length"),
            {"done": True, "done_reason": "stop"},
            collected("not-a-message"),
            collected(prompt_eval_count="not-a-number"),
            [],
            None,
        ]
        for data in cases:
            with self.subTest(data=data):
                self.assertEqual("", collected_retry_reason(data))


class ReplayBodyTests(unittest.TestCase):
    def wire_body(self):
        return {
            "model": "glm-5.3",
            "stream": True,
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "hello"},
            ],
            "tools": [{"type": "function", "function": {"name": "Read"}}],
            "options": {"num_ctx": 65536, "num_predict": 4096},
            "think": "high",
        }

    def test_appends_one_instruction_and_keeps_everything_else(self):
        body = self.wire_body()
        original = copy.deepcopy(body)
        replayed = replay_body(body)

        self.assertEqual(original, body)
        self.assertEqual(original["messages"], replayed["messages"][:-1])
        self.assertEqual(
            {"role": "user", "content": SEMANTIC_RETRY_INSTRUCTION},
            replayed["messages"][-1],
        )
        for key in ("model", "stream", "tools", "options", "think"):
            self.assertEqual(original[key], replayed[key])

    def test_replay_is_independent_of_the_input(self):
        body = self.wire_body()
        replayed = replay_body(body)
        replayed["options"]["num_ctx"] = 1
        replayed["messages"][0]["content"] = "changed"

        self.assertEqual(65536, body["options"]["num_ctx"])
        self.assertEqual("system", body["messages"][0]["content"])

    def test_replays_do_not_accumulate(self):
        body = self.wire_body()
        once = replay_body(body)
        self.assertEqual(once, replay_body(once))
        self.assertEqual(once, replay_body(body))
        self.assertEqual(
            1,
            sum(m.get("content") == SEMANTIC_RETRY_INSTRUCTION for m in replay_body(once)["messages"]),
        )

    def test_missing_messages_yield_only_the_instruction(self):
        self.assertEqual(
            [{"role": "user", "content": SEMANTIC_RETRY_INSTRUCTION}],
            replay_body({"model": "m"})["messages"],
        )


class ExhaustedNoticeTests(unittest.TestCase):
    def test_notice_is_runtime_authored_and_counts_attempts(self):
        self.assertTrue(exhausted_notice(3).startswith("[ciel-runtime] "))
        self.assertIn("after 3 attempts", exhausted_notice(3))
        self.assertIn("after 1 attempt;", exhausted_notice(1))

    def test_invalid_counts_are_reported_as_one_attempt(self):
        self.assertIn("after 1 attempt;", exhausted_notice(0))
        self.assertIn("after 1 attempt;", exhausted_notice("bad"))


if __name__ == "__main__":
    unittest.main()
