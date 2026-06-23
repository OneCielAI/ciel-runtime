import unittest
from unittest import mock

import claude_any


class FakeResponse:
    def __init__(self, items):
        self.items = list(items)
        self.closed = False

    def readline(self):
        if not self.items:
            return b""
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


class FakeHandler:
    headers = {}
    connection = None

    def __init__(self, wfile):
        self.wfile = wfile

    def send_response(self, _status):
        pass

    def send_header(self, _name, _value):
        pass

    def end_headers(self):
        pass


class BrokenWrite:
    def write(self, _data):
        raise BrokenPipeError("client closed")

    def flush(self):
        pass


class CaptureWrite:
    def __init__(self):
        self.data = bytearray()

    def write(self, data):
        self.data.extend(data)

    def flush(self):
        pass


class BrokenHeaderHandler(FakeHandler):
    def send_response(self, _status):
        raise ConnectionResetError("client reset")


class UpstreamCancelTests(unittest.TestCase):
    def test_stream_iterator_raises_when_client_disconnects_during_timeout(self):
        resp = FakeResponse([TimeoutError("timed out")])
        handler = FakeHandler(wfile=object())

        with mock.patch.object(claude_any, "router_client_connection_closed", side_effect=[False, True]):
            with self.assertRaises(claude_any.UpstreamClientDisconnected):
                list(claude_any.iter_upstream_lines_until_client_disconnect(handler, resp, 30.0))

    def test_stream_iterator_treats_upstream_timeout_as_terminal(self):
        resp = FakeResponse([TimeoutError("timed out"), b'{"message":{"content":"ok"},"done":false}\n'])
        handler = FakeHandler(wfile=object())

        with mock.patch.object(claude_any, "router_client_connection_closed", return_value=False):
            with mock.patch.object(claude_any.time, "sleep") as sleep_mock:
                with self.assertRaises(TimeoutError):
                    list(claude_any.iter_upstream_lines_until_client_disconnect(handler, resp, 30.0))

        sleep_mock.assert_not_called()
        self.assertEqual(resp.items, [b'{"message":{"content":"ok"},"done":false}\n'])

    def test_stream_iterator_does_not_spin_on_poisoned_timeout(self):
        resp = FakeResponse([OSError("cannot read from timed out object"), b"should-not-read\n"])
        handler = FakeHandler(wfile=object())

        with mock.patch.object(claude_any, "router_client_connection_closed", return_value=False):
            with mock.patch.object(claude_any.time, "sleep") as sleep_mock:
                with self.assertRaises(OSError):
                    list(claude_any.iter_upstream_lines_until_client_disconnect(handler, resp, 30.0))

        sleep_mock.assert_not_called()
        self.assertEqual(resp.items, [b"should-not-read\n"])

    def test_ollama_stream_closes_upstream_on_downstream_write_failure(self):
        resp = FakeResponse(
            [
                b'{"message":{"content":"hello"},"done":false}\n',
                b'{"message":{"content":""},"done":true}\n',
            ]
        )
        handler = FakeHandler(wfile=BrokenWrite())

        claude_any._ollama_stream_to_anthropic_sse(handler, resp, "gemma4:12b", idle_timeout=30.0)

        self.assertTrue(resp.closed)

    def test_ollama_stream_reports_upstream_timeout_as_sse_error(self):
        resp = FakeResponse([OSError("cannot read from timed out object")])
        wfile = CaptureWrite()
        handler = FakeHandler(wfile=wfile)

        with mock.patch.object(claude_any, "router_client_connection_closed", return_value=False):
            claude_any._ollama_stream_to_anthropic_sse(handler, resp, "gemma4:12b", idle_timeout=30.0)

        text = wfile.data.decode("utf-8")
        self.assertIn("Upstream stream error", text)
        self.assertIn("event: message_start", text)
        self.assertIn("event: content_block_start", text)
        self.assertIn("event: message_delta", text)
        self.assertIn("event: message_stop", text)
        self.assertTrue(resp.closed)

    def test_try_write_json_returns_false_when_client_is_gone(self):
        handler = BrokenHeaderHandler(wfile=object())

        with mock.patch.object(claude_any, "router_log"):
            ok = claude_any.try_write_json(handler, {"error": "x"}, 500)

        self.assertFalse(ok)

    def test_try_write_json_reraises_non_disconnect_errors(self):
        class BadHandler(FakeHandler):
            def send_response(self, _status):
                raise RuntimeError("programming error")

        with self.assertRaises(RuntimeError):
            claude_any.try_write_json(BadHandler(wfile=object()), {"error": "x"}, 500)


if __name__ == "__main__":
    unittest.main()
