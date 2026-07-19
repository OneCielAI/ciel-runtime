import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.request_trace import (
    RequestTracePolicy,
    RequestTraceProjection,
    RequestTraceServices,
    dump_request_for_trace,
    dump_response_for_trace,
    summarize_messages_for_trace,
)


class RequestTraceTests(unittest.TestCase):
    def projection(self):
        return RequestTraceProjection(
            content_to_text=lambda value: str(value),
            thinking_block_count=lambda _body: 1,
            tool_continuation_block_count=lambda _body: 2,
        )

    def services(self, root: Path, log=None):
        return RequestTraceServices(
            policy=RequestTracePolicy(
                enabled=lambda: True,
                request_path=root / "requests.jsonl",
                response_path=root / "responses.jsonl",
                request_max_bytes=100_000,
                response_max_bytes=100_000,
                response_text_limit=5,
            ),
            projection=self.projection(),
            log=log or mock.Mock(),
            timestamp=lambda: "2026-07-18T00:00:00",
        )

    def test_message_summary_projects_tool_blocks(self):
        summary = summarize_messages_for_trace(
            [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "call", "name": "Read", "input": {"path": "x"}},
                        {"type": "thinking", "thinking": "private", "signature": "sig"},
                    ],
                }
            ],
            self.projection(),
        )

        self.assertEqual("call", summary[0]["content"][0]["id"])
        self.assertEqual(7, summary[0]["content"][1]["thinking_len"])

    def test_request_and_response_dumps_write_bounded_json_lines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            services = self.services(Path(temp_dir))
            dump_request_for_trace("provider", "/v1/messages", {"messages": []}, services)
            dump_response_for_trace(
                "provider",
                "model",
                "abcdefgh",
                [],
                "end_turn",
                1,
                2,
                services,
            )

            request = json.loads(services.policy.request_path.read_text(encoding="utf-8"))
            response = json.loads(services.policy.response_path.read_text(encoding="utf-8"))
            self.assertEqual(1, request["thinking_blocks"])
            self.assertIn("truncated 3 chars", response["text"])

    def test_dump_failure_is_observable(self):
        log = mock.Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            services = self.services(root, log)
            services.policy.request_path.mkdir()
            dump_request_for_trace("provider", "/v1/messages", {}, services)

        self.assertIn("request_trace_dump_failed", log.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
