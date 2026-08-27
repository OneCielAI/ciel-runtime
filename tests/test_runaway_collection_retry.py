"""The Codex-facing collection path cuts a loop early and asks again."""

import json
import os
import ssl
import unittest
from http.client import IncompleteRead
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support.ollama_stream_collection import collect_ollama_chat_stream
from ciel_runtime_support.ollama_thinking import INTERNAL_REASONING_EFFORT_KEY
from ciel_runtime_support.response_collection_context import (
    ResponseCollectionContext,
    ResponseCollectionStreamPorts,
)
from ciel_runtime_support.remote_bridge import (
    REMOTE_BRIDGE_CONFIG_MARKER,
    REMOTE_BRIDGE_CONTEXT_ATTRIBUTE,
)
from ciel_runtime_support.runaway_output_guard import NOTICE_MARKER
from ciel_runtime_support.sse_stream_collection import UpstreamSseError
from ciel_runtime_support.upstream_error_policy import UpstreamStreamReadError

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
    def test_strict_mode_rejects_clean_eof_before_done(self):
        with self.assertRaisesRegex(RuntimeError, "done=true"):
            collect_ollama_chat_stream(
                ndjson({"message": {"content": "partial"}, "done": False}),
                strict=True,
            )

    def test_strict_mode_rejects_malformed_json(self):
        with self.assertRaisesRegex(RuntimeError, "malformed JSON"):
            collect_ollama_chat_stream([b"{broken\n"], strict=True)

    def test_strict_mode_accepts_done_true(self):
        collected = collect_ollama_chat_stream(
            ndjson({"message": {"content": "ok"}, "done": True}),
            strict=True,
        )

        self.assertEqual("ok", collected.response["message"]["content"])

    def test_strict_mode_rejects_done_without_a_message(self):
        with self.assertRaisesRegex(RuntimeError, "no message"):
            collect_ollama_chat_stream(ndjson({"done": True}), strict=True)

    def test_strict_mode_rejects_malformed_tool_arguments(self):
        with self.assertRaisesRegex(RuntimeError, "malformed arguments"):
            collect_ollama_chat_stream(
                ndjson(
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "delete_file",
                                        "arguments": '{"path":',
                                    }
                                }
                            ]
                        },
                        "done": True,
                    }
                ),
                strict=True,
            )

    def test_strict_mode_rejects_invalid_utf8(self):
        with self.assertRaisesRegex(RuntimeError, "invalid UTF-8"):
            collect_ollama_chat_stream(
                [b'{"done":true,"message":{}}' + bytes([0xFF]) + b"\n"],
                strict=True,
            )

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

    def test_remote_bridge_collector_enables_strict_parsing(self):
        stream = CountingStream([])
        parse = mock.Mock(
            return_value=SimpleNamespace(response={"ok": True}, verdict=None, chunks=0)
        )
        collect = self.context(stream, []).opened_stream_collector(parse, "test")

        response = collect(
            "url",
            {},
            {},
            30.0,
            "provider",
            {REMOTE_BRIDGE_CONFIG_MARKER: True},
            "model",
        )

        self.assertEqual({"ok": True}, response)
        self.assertTrue(parse.call_args.kwargs["strict"])

    def test_the_transport_escape_hatch_falls_back_to_the_blocking_post(self):
        context = self.context(CountingStream([]), [])

        self.assertIsNotNone(context.opened_stream_collector(collect_ollama_chat_stream, "ollama"))
        with mock.patch.dict(os.environ, {"CIEL_RUNTIME_COLLECT_STREAM": "off"}):
            self.assertIsNone(
                context.opened_stream_collector(collect_ollama_chat_stream, "ollama")
            )
            self.assertIsNone(context.anthropic_stream_collector())
            self.assertIsNotNone(
                context.opened_stream_collector(
                    collect_ollama_chat_stream,
                    "ollama",
                    force=True,
                )
            )
            self.assertIsNotNone(context.anthropic_stream_collector(force=True))

    def test_no_open_stream_port_means_no_streaming(self):
        context = ResponseCollectionContext(
            shared=None, anthropic=None, strategies=None, routing=None
        )

        self.assertFalse(context.streaming_collection_enabled())
        self.assertIsNone(context.opened_stream_collector(collect_ollama_chat_stream, "ollama"))

    def test_kimi_capacity_sse_error_retries_up_to_a_healthy_response(self):
        streams = [CountingStream([]), CountingStream([]), CountingStream([])]
        logs = []
        waits = []
        calls = []

        def open_stream(*_args, **_kwargs):
            calls.append(True)
            return streams[len(calls) - 1]

        attempts = 0

        def parse(_response, _policy):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise UpstreamSseError(
                    "internal_server_error",
                    "We're currently experiencing high demand, which may cause temporary errors.",
                )
            return type("Collection", (), {"response": {"ok": True}, "verdict": None, "chunks": 1})()

        context = ResponseCollectionContext(
            shared=None,
            anthropic=None,
            strategies=None,
            routing=None,
            stream=ResponseCollectionStreamPorts(
                open_stream=open_stream,
                log=lambda level, message: logs.append((level, message)),
            ),
        )
        collect = context.opened_stream_collector(parse, "openai_chat")

        with mock.patch(
            "ciel_runtime_support.response_collection_context.time.sleep",
            side_effect=waits.append,
        ):
            response = collect("url", {}, {}, 30.0, "kimi", {"gateway_retries": 10}, "k3")

        self.assertEqual({"ok": True}, response)
        self.assertEqual(3, len(calls))
        self.assertEqual([2.0, 4.0], waits)
        self.assertTrue(all(stream.closed for stream in streams))
        self.assertIn("attempt=2/10", logs[-1][1])

    def test_remote_kimi_capacity_error_is_never_replayed(self):
        stream = CountingStream([])
        calls = []
        context = ResponseCollectionContext(
            shared=None,
            anthropic=None,
            strategies=None,
            routing=None,
            stream=ResponseCollectionStreamPorts(
                open_stream=lambda *_args, **_kwargs: calls.append(True) or stream,
            ),
        )

        def rejected(_response, _policy, *, strict=False):
            self.assertTrue(strict)
            raise UpstreamSseError(
                "internal_server_error",
                "We're currently experiencing high demand.",
            )

        collect = context.opened_stream_collector(rejected, "openai_chat")
        with mock.patch(
            "ciel_runtime_support.response_collection_context.time.sleep"
        ) as sleep:
            with self.assertRaises(UpstreamSseError):
                collect(
                    "url",
                    {},
                    {},
                    30.0,
                    "kimi",
                    {
                        "gateway_retries": 10,
                        REMOTE_BRIDGE_CONFIG_MARKER: True,
                    },
                    "k3",
                )

        self.assertEqual(1, len(calls))
        self.assertTrue(stream.closed)
        sleep.assert_not_called()

    def test_kimi_capacity_error_after_output_is_not_retried(self):
        stream = CountingStream([])
        calls = []
        context = ResponseCollectionContext(
            shared=None,
            anthropic=None,
            strategies=None,
            routing=None,
            stream=ResponseCollectionStreamPorts(
                open_stream=lambda *_a, **_k: calls.append(True) or stream,
            ),
        )
        collect = context.opened_stream_collector(
            lambda _response, _policy: (_ for _ in ()).throw(
                UpstreamSseError("internal_server_error", "high demand", output_started=True)
            ),
            "openai_chat",
        )

        with mock.patch(
            "ciel_runtime_support.response_collection_context.time.sleep",
            side_effect=lambda _seconds: self.fail("must not sleep"),
        ):
            with self.assertRaises(UpstreamSseError):
                collect("url", {}, {}, 30.0, "kimi", {"gateway_retries": 10}, "k3")

        self.assertEqual(1, len(calls))
        self.assertTrue(stream.closed)

    def test_kimi_tls_failure_while_reading_stream_is_retried(self):
        streams = [CountingStream([]), CountingStream([])]
        calls = []
        waits = []

        def open_stream(*_args, **_kwargs):
            calls.append(True)
            return streams[len(calls) - 1]

        attempts = 0

        def parse(_response, _policy):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ssl.SSLError(
                    "[SSL: SSLV3_ALERT_BAD_RECORD_MAC] sslv3 alert bad record mac"
                )
            return type(
                "Collection",
                (),
                {"response": {"ok": True}, "verdict": None, "chunks": 1},
            )()

        context = ResponseCollectionContext(
            shared=None,
            anthropic=None,
            strategies=None,
            routing=None,
            stream=ResponseCollectionStreamPorts(open_stream=open_stream),
        )
        collect = context.opened_stream_collector(parse, "openai_chat")

        with mock.patch(
            "ciel_runtime_support.response_collection_context.time.sleep",
            side_effect=waits.append,
        ):
            response = collect(
                "url", {}, {}, 30.0, "kimi", {"gateway_retries": 10}, "k3"
            )

        self.assertEqual({"ok": True}, response)
        self.assertEqual(2, len(calls))
        self.assertEqual([2.0], waits)
        self.assertTrue(all(stream.closed for stream in streams))

    def test_kimi_stream_transport_retries_are_capped_below_capacity_retries(self):
        streams = [CountingStream([]) for _ in range(4)]
        calls = []
        logs = []

        def open_stream(*_args, **_kwargs):
            calls.append(True)
            return streams[len(calls) - 1]

        context = ResponseCollectionContext(
            shared=None,
            anthropic=None,
            strategies=None,
            routing=None,
            stream=ResponseCollectionStreamPorts(
                open_stream=open_stream,
                log=lambda level, message: logs.append((level, message)),
            ),
        )
        collect = context.opened_stream_collector(
            lambda _response, _policy: (_ for _ in ()).throw(
                ConnectionResetError(10054, "connection forcibly closed by remote host")
            ),
            "openai_chat",
        )

        with mock.patch("ciel_runtime_support.response_collection_context.time.sleep"):
            with self.assertRaises(RuntimeError) as caught:
                collect("url", {}, {}, 30.0, "kimi", {"gateway_retries": 10}, "k3")

        self.assertEqual(4, len(calls), "one request plus at most three transport retries")
        self.assertTrue(all(stream.closed for stream in streams))
        self.assertIn("provider 'kimi'", str(caught.exception))
        self.assertIn("model=k3", str(caught.exception))
        self.assertIn("ConnectionResetError", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, ConnectionResetError)
        self.assertIn("openai_chat_kimi_stream_read_exhausted", logs[-1][1])

    def test_non_kimi_stream_reset_is_not_retried_and_preserves_provenance(self):
        stream = CountingStream([])
        calls = []
        context = ResponseCollectionContext(
            shared=None,
            anthropic=None,
            strategies=None,
            routing=None,
            stream=ResponseCollectionStreamPorts(
                open_stream=lambda *_args, **_kwargs: calls.append(True) or stream,
            ),
        )
        collect = context.opened_stream_collector(
            lambda _response, _policy: (_ for _ in ()).throw(
                ConnectionResetError(10054, "connection forcibly closed by remote host")
            ),
            "openai_chat",
        )

        with mock.patch(
            "ciel_runtime_support.response_collection_context.time.sleep"
        ) as sleep:
            with self.assertRaises(UpstreamStreamReadError) as caught:
                collect("url", {}, {}, 30.0, "openai", {}, "gpt-test")

        self.assertEqual(1, len(calls))
        sleep.assert_not_called()
        self.assertTrue(stream.closed)
        self.assertIn("provider 'openai'", str(caught.exception))
        self.assertIn("model=gpt-test", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, ConnectionResetError)

    def test_non_kimi_truncated_stream_retries_once_then_succeeds(self):
        streams = [CountingStream([]), CountingStream([])]
        calls = []
        logs = []

        def open_stream(*_args, **_kwargs):
            calls.append(True)
            return streams[len(calls) - 1]

        attempts = 0

        def parse(_response, _policy):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise IncompleteRead(b"partial-response")
            return type(
                "Collection",
                (),
                {"response": {"ok": True}, "verdict": None, "chunks": 1},
            )()

        context = ResponseCollectionContext(
            shared=None,
            anthropic=None,
            strategies=None,
            routing=None,
            stream=ResponseCollectionStreamPorts(
                open_stream=open_stream,
                log=lambda level, message: logs.append((level, message)),
            ),
        )
        collect = context.opened_stream_collector(parse, "openai_chat")

        response = collect("url", {}, {}, 30.0, "alitoken", {}, "qwen3.8-max")

        self.assertEqual({"ok": True}, response)
        self.assertEqual(2, len(calls))
        self.assertTrue(all(stream.closed for stream in streams))
        self.assertIn("openai_chat_stream_truncated_retry", logs[0][1])
        self.assertIn("bytes=16", logs[0][1])

    def test_remote_non_kimi_truncated_stream_is_never_replayed(self):
        stream = CountingStream([])
        calls = []
        context = ResponseCollectionContext(
            shared=None,
            anthropic=None,
            strategies=None,
            routing=None,
            stream=ResponseCollectionStreamPorts(
                open_stream=lambda *_args, **_kwargs: calls.append(True) or stream,
            ),
        )

        def truncated(_response, _policy, *, strict=False):
            self.assertTrue(strict)
            raise IncompleteRead(b"partial-response")

        collect = context.opened_stream_collector(truncated, "openai_chat")
        with self.assertRaises(UpstreamStreamReadError) as caught:
            collect(
                "url",
                {},
                {},
                30.0,
                "alitoken",
                {REMOTE_BRIDGE_CONFIG_MARKER: True},
                "qwen3.8-max",
            )

        self.assertEqual(1, len(calls))
        self.assertEqual(1, caught.exception.attempts)
        self.assertTrue(stream.closed)

    def test_non_kimi_truncated_stream_stops_after_one_retry(self):
        streams = [CountingStream([]), CountingStream([])]
        calls = []
        logs = []

        def open_stream(*_args, **_kwargs):
            calls.append(True)
            return streams[len(calls) - 1]

        context = ResponseCollectionContext(
            shared=None,
            anthropic=None,
            strategies=None,
            routing=None,
            stream=ResponseCollectionStreamPorts(
                open_stream=open_stream,
                log=lambda level, message: logs.append((level, message)),
            ),
        )
        collect = context.opened_stream_collector(
            lambda _response, _policy: (_ for _ in ()).throw(
                IncompleteRead(b"x" * 12970)
            ),
            "openai_chat",
        )

        with self.assertRaises(UpstreamStreamReadError) as caught:
            collect("url", {}, {}, 30.0, "alitoken", {}, "qwen3.8-max")

        self.assertEqual(2, len(calls))
        self.assertTrue(all(stream.closed for stream in streams))
        self.assertEqual(2, caught.exception.attempts)
        self.assertIn("after 12970 bytes", str(caught.exception))
        self.assertIn("provider 'alitoken'", str(caught.exception))
        self.assertIn("openai_chat_stream_read_exhausted", logs[-1][1])


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
        self.assertEqual("end_turn", message["stop_reason"])
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

    def test_remote_bridge_collection_bypasses_host_runaway_retries(self):
        expected = looping_message()
        routing = SimpleNamespace(
            resolve_model=lambda *_args: "remote-model",
            select_protocol=lambda *_args: "ollama_chat",
            provider_labels={},
        )
        context = ResponseCollectionContext(
            shared=None,
            anthropic=None,
            strategies=None,
            routing=routing,
        )
        handler = SimpleNamespace()
        setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)

        with (
            mock.patch.object(
                ResponseCollectionContext,
                "collect_ollama",
                return_value=expected,
            ) as collect_ollama,
            mock.patch.object(
                ResponseCollectionContext,
                "collect_without_runaway",
                side_effect=AssertionError("remote request entered host retry policy"),
            ) as guarded,
        ):
            message = context.collect(
                handler,
                "ollama-cloud",
                {},
                {"model": "remote-model", "messages": []},
            )

        self.assertIs(expected, message)
        collect_ollama.assert_called_once()
        guarded.assert_not_called()


if __name__ == "__main__":
    unittest.main()
