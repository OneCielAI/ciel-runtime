import io
import json
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
    def test_upstream_401_is_dependency_failure_not_client_login_failure(self):
        for stream in (True, False):
            with self.subTest(stream=stream):
                writes = []
                handler = _Handler()
                write_openai_responses_error(
                    handler, "invalid credentials", stream=stream, status=401,
                    error_type="authentication_error", upstream_provider="test-provider",
                    services=self.services(writes),
                )
                _, payload, status = writes[0]
                self.assertEqual(424, status)
                self.assertEqual(401, payload["error"]["upstream_status"])
                self.assertEqual("test-provider", payload["error"]["upstream_provider"])
                self.assertEqual("upstream_authentication_error", payload["error"]["code"])
                self.assertIn("invalid credentials", payload["error"]["message"])
                self.assertEqual(b"", handler.wfile.getvalue())

    def test_native_auth_401_is_not_rewritten(self):
        handler = _Handler()
        write_openai_responses_error(handler, "login required", stream=True, status=401,
                                     services=self.services())
        self.assertEqual(401, handler.status)

    def test_started_upstream_auth_failure_preserves_stream_and_metadata(self):
        handler = _Handler()
        handler.status = 200
        write_openai_responses_error(
            handler, "invalid credentials", stream=True, status=401,
            upstream_provider="test-provider", response_started=True,
            response_id="resp_test", services=self.services(),
        )
        self.assertEqual(200, handler.status)
        self.assertEqual([], handler.headers)
        data = next(line[6:] for line in handler.wfile.getvalue().decode().splitlines()
                    if line.startswith("data: "))
        payload = json.loads(data)
        self.assertEqual("response.failed", payload["type"])
        self.assertEqual(401, payload["response"]["error"]["upstream_status"])

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

    def test_incomplete_stream_uses_matching_terminal_event(self):
        handler = _Handler()
        response = {
            "id": "resp_limited",
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [],
        }

        write_openai_responses(
            handler,
            response,
            None,
            stream=True,
            services=self.services(),
        )
        text = handler.wfile.getvalue().decode()

        self.assertIn("event: response.incomplete", text)
        self.assertNotIn("event: response.completed", text)

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

    def test_non_stream_error_preserves_specific_error_type(self):
        writes = []
        handler = _Handler()
        write_openai_responses_error(
            handler,
            "too large",
            stream=False,
            status=413,
            error_type="request_too_large",
            services=self.services(writes),
        )

        written_handler, payload, status = writes[0]
        self.assertIs(handler, written_handler)
        self.assertEqual(413, status)
        self.assertEqual("request_too_large", payload["error"]["type"])

    def test_started_stream_error_emits_response_failed_without_second_headers(self):
        handler = _Handler()
        handler.status = 200

        write_openai_responses_error(
            handler,
            "upstream response ended early",
            stream=True,
            status=502,
            error_type="upstream_stream_truncated",
            response_started=True,
            response_id="resp_upstream",
            services=self.services(),
        )

        text = handler.wfile.getvalue().decode()
        self.assertEqual(200, handler.status)
        self.assertEqual([], handler.headers)
        self.assertIn("event: response.failed", text)
        self.assertIn('"id": "resp_upstream"', text)
        self.assertIn('"code": "upstream_stream_truncated"', text)


if __name__ == "__main__":
    unittest.main()
