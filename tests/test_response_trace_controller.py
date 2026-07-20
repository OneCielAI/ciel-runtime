import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.request_trace import (
    RequestTracePolicy,
    RequestTraceProjection,
    RequestTraceServices,
    ResponseTraceController,
)


class ResponseTraceControllerTests(unittest.TestCase):
    def services(self) -> RequestTraceServices:
        return RequestTraceServices(
            policy=RequestTracePolicy(
                enabled=lambda: False,
                request_path=Path("request.jsonl"),
                response_path=Path("response.jsonl"),
                request_max_bytes=1_000,
                response_max_bytes=1_000,
                response_text_limit=100,
            ),
            projection=RequestTraceProjection(
                content_to_text=str,
                thinking_block_count=lambda _body: 0,
                tool_continuation_block_count=lambda _body: 0,
            ),
            log=lambda _level, _message: None,
        )

    def test_usage_is_recorded_and_published_before_trace(self):
        record = mock.Mock()
        publish = mock.Mock()
        controller = ResponseTraceController(
            record_usage=record,
            publish_event=publish,
            services=self.services,
            log=lambda _level, _message: None,
        )

        controller.write("provider", "model", "text", [], "stop", 5, 3)

        event = record.call_args.args[0]
        self.assertEqual(("provider", "model"), (event.provider, event.model))
        self.assertEqual(5, event.input_tokens)
        publish.assert_called_once()

    def test_usage_failure_does_not_block_trace_path(self):
        messages: list[str] = []
        controller = ResponseTraceController(
            record_usage=mock.Mock(side_effect=RuntimeError("failed")),
            publish_event=mock.Mock(),
            services=self.services,
            log=lambda _level, message: messages.append(message),
        )

        controller.write("provider", "model", "", [], None, 0, 0)

        self.assertTrue(any("usage_event_record_failed" in item for item in messages))


if __name__ == "__main__":
    unittest.main()
