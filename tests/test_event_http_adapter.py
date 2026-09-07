import io
import base64
import hashlib
import json
import unittest
from unittest import mock

from ciel_runtime_support.router_http import EventHttpAdapter, EventHttpPorts


class EventHttpAdapterTests(unittest.TestCase):
    def test_recent_endpoint_projects_filters_and_cursor(self):
        recent = mock.Mock(return_value=[{"id": 4, "message": "ready"}])
        writes = []
        adapter = self._adapter(recent=recent, write_json=lambda handler, body: writes.append((handler, body)))
        handler = object()

        self.assertTrue(
            adapter.handle_get(
                handler,
                "/ca/events/recent",
                {"limit": ["5"], "after": ["3"], "level": ["warn"]},
            )
        )
        recent.assert_called_once_with(limit=5, min_id=3, level="warn", category=None)
        self.assertEqual(4, writes[0][1]["events"][0]["id"])

    def test_stream_writes_initial_event_then_treats_disconnect_as_success(self):
        class Handler:
            def __init__(self):
                self.wfile = io.BytesIO()
                self.status = None
                self.headers = []

            def send_response(self, status):
                self.status = status

            def send_header(self, key, value):
                self.headers.append((key, value))

            def end_headers(self):
                pass

        handler = Handler()
        adapter = self._adapter(
            recent=lambda **_kwargs: [{"id": 1, "message": "ready"}],
            wait_after=mock.Mock(side_effect=BrokenPipeError()),
        )
        self.assertTrue(adapter.handle_get(handler, "/ca/events/stream", {}))
        self.assertEqual(200, handler.status)
        self.assertIn(b'"id": 1', handler.wfile.getvalue())

    def test_websocket_upgrade_streams_filtered_json_text_frame(self):
        class Handler:
            def __init__(self):
                self.wfile = io.BytesIO()
                self.status = None
                self.response_headers = []
                self.close_connection = False
                self.headers = {
                    "upgrade": "websocket",
                    "sec-websocket-version": "13",
                    "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ==",
                }

            def send_response(self, status):
                self.status = status

            def send_header(self, key, value):
                self.response_headers.append((key.lower(), value))

            def end_headers(self):
                pass

        recent = mock.Mock(
            return_value=[{"id": 7, "level": "info", "category": "tool.call", "message": "Bash"}]
        )
        handler = Handler()
        adapter = self._adapter(recent=recent, wait_after=mock.Mock(side_effect=BrokenPipeError()))

        self.assertTrue(
            adapter.handle_get(handler, "/ca/events/ws", {"category": ["tool.call"]})
        )

        self.assertEqual(101, handler.status)
        expected_accept = base64.b64encode(
            hashlib.sha1(
                b"dGhlIHNhbXBsZSBub25jZQ==258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            ).digest()
        ).decode("ascii")
        self.assertIn(("sec-websocket-accept", expected_accept), handler.response_headers)
        frame = handler.wfile.getvalue()
        self.assertEqual(0x81, frame[0])
        size = frame[1] & 0x7F
        payload = json.loads(frame[2 : 2 + size])
        self.assertEqual("tool.call", payload["category"])
        recent.assert_called_once_with(
            limit=200,
            min_id=0,
            level=None,
            category="tool.call",
        )

    @staticmethod
    def _adapter(*, recent=lambda **_kwargs: [], wait_after=lambda *_args, **_kwargs: [], write_json=lambda *_args: None):
        return EventHttpAdapter(
            EventHttpPorts(
                recent=recent,
                wait_after=wait_after,
                render_html=lambda: "events",
                write_text=lambda *_args, **_kwargs: None,
                write_json=write_json,
                log=lambda *_args: None,
            )
        )


if __name__ == "__main__":
    unittest.main()
