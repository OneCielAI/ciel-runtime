import io
import unittest

from ciel_runtime_support.openai_responses_stream import (
    OpenAIResponsesStreamServices,
    write_openai_responses,
    write_openai_responses_error,
)


class _Handler:
    def __init__(self):
        self.status = None
        self.headers = []
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers.append((name, value))

    def end_headers(self):
        pass


class OpenAIResponsesStreamTests(unittest.TestCase):
    def services(self, writes=None):
        return OpenAIResponsesStreamServices(
            to_response=lambda message, source_body=None: message,
            write_json=lambda *args: (writes if writes is not None else []).append(args),
        )

    def test_stream_emits_required_lifecycle_in_order(self):
        handler = _Handler()
        response = {
            "id": "resp_1",
            "output": [
                {
                    "id": "msg_1",
                    "type": "message",
                    "content": [{"type": "output_text", "text": "hello"}],
                }
            ],
        }
        write_openai_responses(handler, response, None, stream=True, services=self.services())
        text = handler.wfile.getvalue().decode()

        events = [line.removeprefix("event: ") for line in text.splitlines() if line.startswith("event: ")]
        self.assertEqual(
            [
                "response.created",
                "response.output_item.added",
                "response.content_part.added",
                "response.output_text.delta",
                "response.output_text.done",
                "response.content_part.done",
                "response.output_item.done",
                "response.completed",
            ],
            events,
        )

    def test_non_stream_response_uses_json_transport(self):
        writes = []
        handler = _Handler()
        response = {"id": "resp_1", "output": []}
        write_openai_responses(handler, response, None, stream=False, services=self.services(writes))
        self.assertEqual((handler, response), writes[0])

    def test_stream_error_uses_requested_status(self):
        handler = _Handler()
        write_openai_responses_error(
            handler,
            "failed",
            stream=True,
            status=429,
            services=self.services(),
        )
        self.assertEqual(429, handler.status)
        self.assertIn("event: error", handler.wfile.getvalue().decode())


if __name__ == "__main__":
    unittest.main()
