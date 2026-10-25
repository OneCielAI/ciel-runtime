import json
import unittest
from unittest import mock

import ciel_runtime
from test_ollama_provider_options import _FakeOllamaStreamResponse, _FakeSSEHandler


def response(*messages, reason="stop", tokens=5, terminal=True):
    chunks = [{"message": m, "done": False} for m in messages]
    if terminal:
        chunks.append({"message": {"content": ""}, "done": True,
                       "done_reason": reason, "prompt_eval_count": 10, "eval_count": tokens})
    return _FakeOllamaStreamResponse([(json.dumps(c) + chr(10)).encode() for c in chunks])


def events(handler):
    return [json.loads(line[6:]) for line in handler.wfile.data.decode().splitlines()
            if line.startswith("data: ")]


class OllamaSemanticRetryStreamTests(unittest.TestCase):
    def run_stream(self, first, *retries, budget=1):
        handler = _FakeSSEHandler()
        reopen = mock.Mock(side_effect=list(retries))
        body = {"messages": [{"role": "user", "content": "hello"}],
                "tools": [{"name": "Read", "input_schema": {"type": "object",
                          "properties": {"file_path": {"type": "string"}}}}]}
        with mock.patch.object(ciel_runtime, "write_router_activity") as activity,              mock.patch.object(ciel_runtime, "mark_pending_channel_delivery_success") as success,              mock.patch.object(ciel_runtime, "mark_pending_channel_delivery_failed") as failure:
            ciel_runtime._ollama_stream_to_anthropic_sse(
                handler, first, "glm-5.3-flash", provider="ollama-cloud",
                source_body=body, semantic_retries=budget, reopen_semantic=reopen)
        return handler, reopen, activity, success, failure

    def assert_envelope(self, handler):
        es = events(handler)
        self.assertEqual(1, sum(e["type"] == "message_start" for e in es))
        self.assertEqual(1, sum(e["type"] == "message_stop" for e in es))
        opened = set()
        seen = set()
        for e in es:
            if e["type"] == "content_block_start":
                self.assertFalse(opened)
                self.assertNotIn(e["index"], seen)
                seen.add(e["index"])
                opened.add(e["index"])
            elif e["type"] == "content_block_stop":
                self.assertIn(e["index"], opened)
                opened.remove(e["index"])
        self.assertFalse(opened)
        return es

    def test_empty_then_answer(self):
        a, b = response(), response({"content": "answer"}, tokens=7)
        h, reopen, _, success, failure = self.run_stream(a, b)
        self.assertEqual(1, reopen.call_count)
        es = self.assert_envelope(h)
        output = h.wfile.data.decode()
        self.assertIn("answer", output)
        self.assertNotIn("Please retry", output)
        self.assertEqual(12, next(e for e in es if e["type"] == "message_delta")["usage"]["output_tokens"])
        self.assertTrue(a.closed and b.closed)
        success.assert_called_once()
        failure.assert_not_called()

    def test_reasoning_then_answer_keeps_blocks_separate(self):
        h, reopen, _, _, failure = self.run_stream(
            response({"thinking": "first thought"}),
            response({"thinking": "second thought"}, {"content": "answer"}))
        self.assert_envelope(h)
        self.assertEqual(1, reopen.call_count)
        self.assertNotIn("without a final answer", h.wfile.data.decode())
        failure.assert_not_called()

    def test_reasoning_then_tool(self):
        tool = {"function": {"name": "Read", "arguments": {"file_path": "/tmp/x"}}}
        first = response({"thinking": "first thought"})
        second = response({"tool_calls": [tool]})
        h, reopen, _, _, failure = self.run_stream(first, second)
        self.assert_envelope(h)
        self.assertEqual(1, reopen.call_count)
        self.assertIn('"stop_reason": "tool_use"', h.wfile.data.decode())
        failure.assert_not_called()

    def test_whitespace_and_hidden_markup_retry(self):
        for message in [{"content": "  " + chr(10)}, {"content": "<think>hidden</think>"}]:
            with self.subTest(message=message):
                h, reopen, _, _, _ = self.run_stream(response(message), response({"content": "ok"}))
                self.assertEqual(1, reopen.call_count)
                self.assert_envelope(h)
                self.assertIn("ok", h.wfile.data.decode())

    def test_repeated_failures_are_bounded_and_not_success(self):
        h, reopen, activity, success, failure = self.run_stream(response(), response(), response(), budget=2)
        self.assertEqual(2, reopen.call_count)
        self.assert_envelope(h)
        # Delivery stays confirmed so the channel cursor commits once; the
        # exhaustion is recorded as an error activity, never as success.
        success.assert_called_once()
        failure.assert_not_called()
        kinds = [c.args[0] for c in activity.call_args_list]
        self.assertIn("error", kinds)
        self.assertNotIn("success", kinds)
        output = h.wfile.data.decode()
        self.assertEqual(1, output.count("automatic retries are exhausted"))
        self.assertIn('"stop_reason": "end_turn"', output)

    def test_no_retry_on_answer_tool_length_or_incomplete(self):
        cases = [response({"content": "ok"}),
                 response({"tool_calls": [{"function": {}}]}),
                 response({"thinking": "thought"}, reason="length"),
                 response({"thinking": "thought"}, terminal=False)]
        for first in cases:
            with self.subTest(first=first):
                _, reopen, _, _, _ = self.run_stream(first)
                reopen.assert_not_called()

    def test_invalid_payload_and_error_prevent_retry(self):
        for line in [b"not-json" + bytes([10]), b'{"error":"failed"}' + bytes([10])]:
            first = response()
            first.lines.insert(0, line)
            _, reopen, _, _, _ = self.run_stream(first)
            reopen.assert_not_called()

    def test_open_failure_is_a_single_terminal_response(self):
        h, reopen, _, success, failure = self.run_stream(response(), OSError("no connection"))
        self.assert_envelope(h)
        self.assertEqual(1, reopen.call_count)
        success.assert_called_once()
        failure.assert_not_called()
        self.assertEqual(1, h.wfile.data.decode().count("automatic retries are exhausted"))

    def test_non_object_json_line_does_not_crash_classification(self):
        first = response()
        first.lines.insert(0, b"[]" + bytes([10]))
        h, reopen, _, _, _ = self.run_stream(first)
        reopen.assert_not_called()
        self.assert_envelope(h)

    def test_disabled_has_no_extra_request(self):
        _, reopen, _, _, _ = self.run_stream(response({"thinking": "thought"}), budget=0)
        reopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
