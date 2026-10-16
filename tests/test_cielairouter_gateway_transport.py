import io
import json
import unittest
import urllib.error

from ciel_runtime_support.cielairouter_gateway_transport import observe_gateway_error, wrap_gateway_response


def frame(kind):
    return (f"event: {kind}\ndata: " + json.dumps({"type": kind}) + "\n\n").encode()


class R(io.BytesIO):
    status = 200
    headers = {}

    def getcode(self):
        return self.status


class TestTransport(unittest.TestCase):
    def make(self, data=b"", protocol="anthropic", **kw):
        self.results = []
        self.raw = R(data)
        return wrap_gateway_response(self.raw, protocol, on_result=self.results.append, **kw)

    def test_read_and_readline_iteration_preserve(self):
        data = frame("message_stop")
        response = self.make(data)
        self.assertEqual(response.read(3) + response.readline() + b"".join(response), data)
        self.assertTrue(self.results[0].success)
        self.assertIs(response.headers, self.raw.headers)

    def test_context_manager_and_original_exception(self):
        response = self.make(b"partial")
        error = BrokenPipeError("downstream")
        with self.assertRaises(BrokenPipeError) as raised:
            with response:
                response.read(1)
                raise error
        self.assertIs(raised.exception, error)
        self.assertEqual(self.results, [])

    def test_protocol_terminals(self):
        cases = [
            ("anthropic", ["message_stop"]),
            ("responses", ["response.completed", "response.incomplete", "response.failed"]),
        ]
        for protocol, kinds in cases:
            for kind in kinds:
                with self.subTest(protocol=protocol, kind=kind):
                    response = self.make(frame(kind), protocol)
                    self.assertEqual(response.read(), frame(kind))
                    self.assertTrue(self.results[0].success)
        for body in [b"data: [DONE]\n\n", b'data: {"choices":[{"finish_reason":"stop"}]}\n\n']:
            response = self.make(body, "chat")
            response.read()
            self.assertTrue(self.results[0].success)

    def test_truncated_graceful_eof_and_split_terminal(self):
        response = self.make(b"data: {}\n\n")
        self.assertEqual(response.read(), b"data: {}\n\n")
        self.assertEqual(self.results[0].reason, "eof_before_terminal")
        data = frame("message_stop")
        response = self.make(data)
        self.assertEqual(b"".join(response), data)
        self.assertTrue(self.results[0].success)

    def test_huge_split_chunks_bounded_and_preserved(self):
        data = b"data: " + b"x" * 200000 + b"\n\n" + frame("message_stop")
        response = self.make(data, max_sse_buffer=128)
        output = b""
        while part := response.read(37):
            output += part
            self.assertLessEqual(len(response._parser.line) + len(response._parser.data), 128)
        self.assertEqual(output, data)
        self.assertTrue(self.results[0].success)

    def test_close_early_and_client_abort_are_not_failure(self):
        response = self.make(b"partial")
        response.close()
        self.assertEqual(self.results, [])
        response = self.make(b"partial", client_disconnected=lambda: True)
        response.read()
        self.assertEqual(self.results, [])

    def test_timeout_reset_eof_preserve_exception(self):
        for error, reason in [
            (TimeoutError("x"), "timeout"),
            (ConnectionResetError(), "connection_reset"),
            (EOFError(), "eof"),
        ]:
            response = self.make()

            def fail(*_args, error=error):
                raise error

            response._response.read = fail
            with self.assertRaises(type(error)) as raised:
                response.read()
            self.assertIs(raised.exception, error)
            self.assertEqual(self.results[0].reason, reason)

    def test_callback_does_not_replace_exception_or_log_body(self):
        error = TimeoutError("secret")

        def callback(_result):
            raise RuntimeError

        def fail():
            raise error

        response = wrap_gateway_response(R(), "anthropic", on_result=callback)
        response._response.read = fail
        with self.assertRaises(TimeoutError) as raised:
            response.read()
        self.assertIs(raised.exception, error)

    def test_http_error_helper_only_5xx_without_reading(self):
        for code in (400, 429, 500, 503):
            results = []
            error = urllib.error.HTTPError("url", code, "private", {}, io.BytesIO(b"secret"))
            self.assertIs(observe_gateway_error(error, on_result=results.append), error)
            self.assertEqual(len(results), int(code >= 500))
            self.assertEqual(error.read(), b"secret")

    def test_nonstream_and_zero_read(self):
        response = self.make(b"body", stream=False)
        self.assertEqual(response.read(0), b"")
        self.assertEqual(response.read(), b"body")
        self.assertEqual(self.results[0].reason, "http_complete")

class ExtendedTransportTests(unittest.TestCase):
    def test_real_urlopen_stream_eof_and_timeout(self):
        import threading
        import urllib.request
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        release = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                if self.path == '/timeout':
                    release.wait(2)
                else:
                    self.wfile.write(frame('message_stop') if self.path == '/ok' else b'data: {}\n\n')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for path, reason in [('/ok', 'terminal_event'), ('/short', 'eof_before_terminal'), ('/timeout', 'timeout')]:
                results = []
                url = 'http://127.0.0.1:%d%s' % (server.server_port, path)
                with wrap_gateway_response(urllib.request.urlopen(url, timeout=.15), 'anthropic', on_result=results.append) as response:
                    if path == '/timeout':
                        with self.assertRaises(TimeoutError):
                            response.read()
                    else:
                        self.assertEqual(response.read(), frame('message_stop') if path == '/ok' else b'data: {}\n\n')
                self.assertEqual(results[0].reason, reason)
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_readinto_read1_readlines(self):
        results = []
        body = frame('message_stop')
        response = wrap_gateway_response(R(body), 'anthropic', on_result=results.append)
        actual = response.read1(3)
        buffer = bytearray(5)
        count = response.readinto(buffer)
        actual += bytes(buffer[:count]) + b''.join(response.readlines())
        self.assertEqual(actual, body)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)

    def test_oversized_single_chunk_cannot_forge_terminal(self):
        body = b'data: x\n' * 50000 + b'event: message_stop\n\n'
        results = []
        response = wrap_gateway_response(R(body), 'anthropic', on_result=results.append, max_sse_buffer=128)
        self.assertEqual(response.read(), body)
        self.assertFalse(results[0].success)
        results.clear()
        response = wrap_gateway_response(R(body + frame('message_stop')), 'anthropic', on_result=results.append, max_sse_buffer=128)
        response.read()
        self.assertTrue(results[0].success)

    def test_crlf_and_cr_split_one_byte(self):
        for separator in (b'\r\n', b'\r'):
            results = []
            body = b'data: {"type":"message_stop"}' + separator * 2
            response = wrap_gateway_response(R(body), 'anthropic', on_result=results.append)
            actual = bytearray()
            while part := response.read(1):
                actual.extend(part)
            self.assertEqual(actual, body)
            self.assertTrue(results[0].success)

    def test_partial_terminal_frame_is_not_accepted(self):
        results = []
        response = wrap_gateway_response(R(b'data: {"type":"message_stop"}'), 'anthropic', on_result=results.append)
        response.read()
        self.assertFalse(results[0].success)

    def test_http_5xx_response_once_and_4xx_not_scored(self):
        for code in (200, 401, 503):
            results = []
            raw = R(b'body')
            raw.status = code
            response = wrap_gateway_response(raw, 'chat', on_result=results.append, stream=False)
            self.assertEqual(response.read(), b'body')
            response.close()
            self.assertEqual(len(results), 0 if code == 401 else 1)
            if code == 503:
                self.assertEqual(results[0].reason, 'http_5xx')

    def test_urlerror_timeout_and_incomplete_read_identity(self):
        import http.client
        for error, reason in [(urllib.error.URLError(TimeoutError()), 'timeout'), (http.client.IncompleteRead(b'secret'), 'eof')]:
            results = []
            response = wrap_gateway_response(R(), 'responses', on_result=results.append)
            def fail(*args):
                raise error
            response._response.readline = fail
            with self.assertRaises(type(error)) as raised:
                next(response)
            self.assertIs(raised.exception, error)
            self.assertEqual(results[0].reason, reason)
            self.assertNotIn('secret', repr(results))


if __name__ == "__main__":
    unittest.main()
