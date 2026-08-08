"""The Codex-facing collection path cuts a loop early and asks again."""

import json
import os
import unittest
from unittest import mock

from ciel_runtime_support.ollama_stream_collection import collect_ollama_chat_stream
from ciel_runtime_support.ollama_thinking import INTERNAL_REASONING_EFFORT_KEY
from ciel_runtime_support.response_collection_context import (
    ResponseCollectionContext,
    ResponseCollectionStreamPorts,
)
from ciel_runtime_support.runaway_output_guard import NOTICE_MARKER

LOOP = (
    "먼저 관련 레포와 지표 계산/차트 데이터 경로를 찾겠습니다. "
    "responseZEC 4h/8h 인디케이터 누락 원인을 확인하겠습니다. "
)
OFFERED = 4000


def ndjson(*chunks):
    return [json.dumps(chunk, ensure_ascii=False).encode() + b"\n" for chunk in chunks]


def loop_lines(count=OFFERED, field="content"):
    lines = ndjson(*({"message": {field: LOOP}, "done": False} for _ in range(count)))
    lines.extend(ndjson({"message": {"content": ""}, "done": True, "done_reason": "stop"}))
    return lines


class CountingStream:
    def __init__(self, lines):
        self._lines = list(lines)
        self.consumed = 0
        self.closed = False

    def __iter__(self):
        for line in self._lines:
            self.consumed += 1
            yield line

    def close(self):
        self.closed = True


class OllamaStreamCollectionTests(unittest.TestCase):
    def test_loop_is_cut_off_mid_generation(self):
        stream = CountingStream(loop_lines())

        collection = collect_ollama_chat_stream(stream)

        self.assertIsNotNone(collection.verdict)
        self.assertLess(stream.consumed, 60)
        self.assertLess(len(collection.response["message"]["content"]), 4000)
        self.assertIn(LOOP, collection.response["message"]["content"])

    def test_repeated_thinking_is_cut_off_too(self):
        collection = collect_ollama_chat_stream(loop_lines(field="thinking"))

        self.assertIsNotNone(collection.verdict)
        self.assertIn(LOOP, collection.response["message"]["thinking"])

    def test_healthy_stream_assembles_the_whole_message(self):
        lines = ndjson(
            {"message": {"role": "assistant", "thinking": "checking"}, "done": False},
            {"message": {"content": "Hello "}, "done": False},
            {"message": {"content": "world"}, "done": False},
            {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "Read", "arguments": {"p": 1}}}],
                },
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 12,
                "eval_count": 34,
            },
        )

        collection = collect_ollama_chat_stream(lines)

        self.assertIsNone(collection.verdict)
        self.assertEqual("Hello world", collection.response["message"]["content"])
        self.assertEqual("checking", collection.response["message"]["thinking"])
        self.assertEqual(1, len(collection.response["message"]["tool_calls"]))
        self.assertEqual("stop", collection.response["done_reason"])
        self.assertEqual(12, collection.response["prompt_eval_count"])
        self.assertEqual(34, collection.response["eval_count"])

    def test_malformed_lines_are_skipped(self):
        lines = [b"\n", b"not json\n", *ndjson({"message": {"content": "ok"}, "done": True})]

        collection = collect_ollama_chat_stream(lines)

        self.assertEqual("ok", collection.response["message"]["content"])

class OpenedStreamCollectorTests(unittest.TestCase):
    """The context composes open + parse + close + log for every protocol."""

    def context(self, stream, logs):
        return ResponseCollectionContext(
            shared=None,
            anthropic=None,
            strategies=None,
            routing=None,
            stream=ResponseCollectionStreamPorts(
                open_stream=lambda *_a, **_k: stream,
                log=lambda level, message: logs.append((level, message)),
            ),
        )

    def test_stream_is_closed_and_the_verdict_is_logged(self):
        stream = CountingStream(loop_lines())
        logs = []
        collect = self.context(stream, logs).opened_stream_collector(
            collect_ollama_chat_stream, "ollama"
        )

        response = collect("url", {}, {}, 30.0, "ollama-cloud", {}, "deepseek-v4-flash:0731")

        self.assertTrue(stream.closed)
        self.assertIn(LOOP, response["message"]["content"])
        self.assertIn("ollama_collect_runaway_repetition", logs[-1][1])
        # The repeated block itself must be recoverable from the log.
        self.assertIn("unit=", logs[-1][1])

    def test_a_healthy_stream_logs_nothing(self):
        logs = []
        stream = CountingStream(ndjson({"message": {"content": "fine"}, "done": True}))
        collect = self.context(stream, logs).opened_stream_collector(
            collect_ollama_chat_stream, "ollama"
        )

        collect("url", {}, {}, 30.0, "ollama-cloud", {}, "m")

        self.assertEqual([], logs)

    def test_the_transport_escape_hatch_falls_back_to_the_blocking_post(self):
        context = self.context(CountingStream([]), [])

        self.assertIsNotNone(context.opened_stream_collector(collect_ollama_chat_stream, "ollama"))
        with mock.patch.dict(os.environ, {"CIEL_RUNTIME_COLLECT_STREAM": "off"}):
            self.assertIsNone(
                context.opened_stream_collector(collect_ollama_chat_stream, "ollama")
            )
            self.assertIsNone(context.anthropic_stream_collector())

    def test_no_open_stream_port_means_no_streaming(self):
        context = ResponseCollectionContext(
            shared=None, anthropic=None, strategies=None, routing=None
        )

        self.assertFalse(context.streaming_collection_enabled())
        self.assertIsNone(context.opened_stream_collector(collect_ollama_chat_stream, "ollama"))


def looping_message():
    return {
        "role": "assistant",
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "확인하겠습니다.\n\n" + LOOP * 60}],
    }


def healthy_message():
    return {
        "role": "assistant",
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "Done, the indicator path is in charts.py."}],
    }


class CollectionRetryTests(unittest.TestCase):
    def context(self, logs):
        return ResponseCollectionContext(
            shared=None,
            anthropic=None,
            strategies=None,
            routing=None,
            stream=ResponseCollectionStreamPorts(
                log=lambda level, message: logs.append((level, message))
            ),
        )

    def collect(self, responses, logs=None, env=None):
        logs = logs if logs is not None else []
        seen = []

        def collector(_handler, _provider, _pcfg, body):
            seen.append(body)
            return responses[min(len(seen) - 1, len(responses) - 1)]

        with mock.patch.dict(os.environ, env or {}, clear=False):
            message = self.context(logs).collect_without_runaway(
                collector, None, "ollama-cloud", {}, {"messages": []}, "deepseek-v4-flash:0731"
            )
        return message, seen, logs

    def test_a_clean_first_attempt_is_returned_untouched(self):
        expected = healthy_message()

        message, seen, logs = self.collect([expected])

        self.assertIs(expected, message)
        self.assertEqual(1, len(seen))
        self.assertEqual([], logs)

    def test_a_looped_turn_is_retried_and_the_user_never_sees_it(self):
        message, seen, logs = self.collect([looping_message(), healthy_message()])

        self.assertEqual(2, len(seen))
        self.assertEqual("end_turn", message["stop_reason"])
        self.assertNotIn(NOTICE_MARKER, json.dumps(message, ensure_ascii=False))
        self.assertIn("attempt=1/3", logs[0][1])
        self.assertIn("retry=True", logs[0][1])

    def test_the_retry_lowers_reasoning_effort(self):
        _message, seen, _logs = self.collect([looping_message(), healthy_message()])

        self.assertNotIn("metadata", seen[0])
        self.assertEqual("high", seen[1]["metadata"][INTERNAL_REASONING_EFFORT_KEY])

    def test_the_ladder_escalates_to_no_thinking(self):
        _message, seen, _logs = self.collect(
            [looping_message(), looping_message(), healthy_message()]
        )

        self.assertEqual(3, len(seen))
        self.assertEqual("high", seen[1]["metadata"][INTERNAL_REASONING_EFFORT_KEY])
        self.assertEqual("low", seen[2]["metadata"][INTERNAL_REASONING_EFFORT_KEY])

    def test_a_loop_that_survives_every_attempt_is_trimmed_and_reported(self):
        message, seen, logs = self.collect([looping_message()])

        self.assertEqual(3, len(seen))
        self.assertEqual("max_tokens", message["stop_reason"])
        self.assertEqual("확인하겠습니다.\n\n" + LOOP, message["content"][0]["text"])
        self.assertIn(NOTICE_MARKER, message["content"][-1]["text"])
        self.assertIn("retry=False", logs[-1][1])

    def test_retry_count_is_configurable(self):
        _message, seen, _logs = self.collect(
            [looping_message()], env={"CIEL_RUNTIME_RUNAWAY_RETRIES": "0"}
        )

        self.assertEqual(1, len(seen))

    def test_disabling_continuation_disables_retrying(self):
        _message, seen, _logs = self.collect(
            [looping_message()], env={"CIEL_RUNTIME_RUNAWAY_CONTINUE": "off"}
        )

        self.assertEqual(1, len(seen))

    def test_the_kill_switch_stops_the_guard_entirely(self):
        expected = looping_message()

        message, seen, _logs = self.collect(
            [expected], env={"CIEL_RUNTIME_RUNAWAY_GUARD": "off"}
        )

        self.assertIs(expected, message)
        self.assertEqual(1, len(seen))


if __name__ == "__main__":
    unittest.main()
