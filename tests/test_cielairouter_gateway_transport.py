import http.client
import io
import json
import ssl
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

    def test_large_explicit_terminals_before_and_after_data(self):
        for protocol, names in [('responses', ['response.completed', 'response.incomplete', 'response.failed']),
                                ('anthropic', ['message_stop'])]:
            for name in names:
                for after in (False, True):
                    for separator in (b'\n', b'\r\n', b'\r'):
                        with self.subTest(protocol=protocol, name=name, after=after, separator=separator):
                            event = b'event: ' + name.encode() + separator
                            data = b'data: ' + json.dumps({'padding': 'x' * 131072, 'type': name}).encode() + separator
                            body = (data + event if after else event + data) + separator
                            response = self.make(body, protocol, max_sse_buffer=128)
                            output = bytearray()
                            while part := response.read(37):
                                output.extend(part)
                                self.assertLessEqual(len(response._parser.line) + len(response._parser.data), 128)
                            self.assertEqual(bytes(output), body)
                            self.assertEqual(len(self.results), 1)
                            self.assertTrue(self.results[0].success)
                            self.assertEqual(self.results[0].terminal, name)

    def test_comments_do_not_exhaust_event_budget_and_done_survives(self):
        for protocol, terminal in [('chat', b'data: [DONE]\n\n'),
                                   ('responses', frame('response.completed'))]:
            body = b':' + b'x' * 200000 + b'\r\n' + terminal
            response = self.make(body, protocol, max_sse_buffer=128)
            output = bytearray()
            while part := response.read(1):
                output.extend(part)
            self.assertEqual(bytes(output), body)
            self.assertTrue(self.results[0].success)

    def test_oversized_json_only_is_honest_unknown_not_failure_evidence(self):
        cases = [('responses', {'type': 'response.completed'}),
                 ('responses', {'type': 'response.incomplete'}),
                 ('responses', {'type': 'response.failed'}),
                 ('anthropic', {'type': 'message_stop'}),
                 ('chat', {'choices': [{'finish_reason': 'stop'}]}),
                 ('responses', {'type': 'response.output_text.delta'})]
        for protocol, fields in cases:
            with self.subTest(protocol=protocol, fields=fields):
                body = b'data: ' + json.dumps({'padding': 'x' * 900000, **fields}).encode() + b'\n\n'
                response = self.make(body, protocol)
                self.assertEqual(response.read(), body)
                self.assertEqual(self.results[0].reason, 'observation_incomplete')
                self.assertIsNone(response.terminal_observed)
                self.assertFalse(self.results[0].success)

    def test_nested_and_string_types_do_not_forge_terminals(self):
        for fields in [{'nested': {'type': 'response.completed'}},
                       {'text': '"type":"response.completed"'},
                       {'type': 'response.output_text.delta'}]:
            response = self.make(b'data: ' + json.dumps(fields).encode() + b'\n\n', 'responses')
            response.read()
            self.assertEqual(self.results[0].reason, 'eof_before_terminal')
        response = self.make(b'event: response.completed' + b'x' * 1000 + b'\n\n', 'responses', max_sse_buffer=128)
        response.read()
        self.assertEqual(self.results[0].reason, 'observation_incomplete')
        self.assertIsNone(response.terminal_observed)

    def test_large_nonterminal_then_terminal_and_partial_terminal(self):
        for protocol, terminal in [('responses', frame('response.completed')),
                                   ('anthropic', frame('message_stop')),
                                   ('chat', b'data: {"choices":[{"finish_reason":"stop"}]}\n\n'),
                                   ('chat', b'data: [DONE]\n\n')]:
            body = b'data: ' + b'x' * 200000 + b'\n\n' + terminal
            response = self.make(body, protocol, max_sse_buffer=128)
            self.assertEqual(response.read(), body)
            self.assertTrue(self.results[0].success)
        response = self.make(b'event: response.completed\ndata: ' + b'x' * 200000, 'responses')
        response.read()
        self.assertEqual(self.results[0].reason, 'observation_incomplete')
        self.assertIsNone(response.terminal_observed)

    def test_small_multi_data_json_and_legitimate_truncation(self):
        response = self.make(b'data: {"type":\r\ndata: "response.completed"}\r\n\r\n', 'responses')
        response.read()
        self.assertTrue(self.results[0].success)
        for body in [b'data: {}\n\n', b'event: response.completed\ndata: {}\n', b'data: {"type":"response.completed"}']:
            response = self.make(body, 'responses')
            response.read()
            self.assertEqual(self.results[0].reason, 'eof_before_terminal')

    def test_incomplete_read_partial_is_observed_before_scoring_without_mutation(self):
        for protocol, terminal in [('responses', frame('response.completed')),
                                   ('anthropic', frame('message_stop')),
                                   ('chat', b'data: [DONE]\n\n')]:
            for cut in (0, 1, len(terminal) // 2):
                for complete in (True, False):
                    with self.subTest(protocol=protocol, cut=cut, complete=complete):
                        prefix = terminal[:cut]
                        partial = terminal[cut:] if complete else terminal[cut:-1]
                        error = http.client.IncompleteRead(partial, 123)
                        response = self.make(prefix, protocol)
                        self.assertEqual(response.read(len(prefix)), prefix)
                        def fail(*args):
                            raise error
                        response._response.read = fail
                        with self.assertRaises(http.client.IncompleteRead) as raised:
                            response.read(65536)
                        self.assertIs(raised.exception, error)
                        self.assertIs(error.partial, partial)
                        self.assertEqual(error.expected, 123)
                        self.assertEqual(len(self.results), 1)
                        self.assertEqual(self.results[0].success, complete)
                        self.assertEqual(self.results[0].reason, 'terminal_event' if complete else 'eof')
                        response.close()
                        self.assertEqual(len(self.results), 1)

    def test_error_events_are_metadata_only_unknown_not_transport_success(self):
        for protocol in ('anthropic', 'responses', 'chat'):
            bodies = [frame('error'), b'event: error\ndata: {}\n\n']
            if protocol == 'chat':
                bodies.append(b'data: {"error":{"message":"private-error-message","code":502}}\n\n')
            for body in bodies:
                with self.subTest(protocol=protocol, body=body):
                    response = self.make(body, protocol)
                    self.assertEqual(response.read(), body)
                    self.assertEqual(len(self.results), 1)
                    self.assertFalse(self.results[0].success)
                    self.assertEqual(self.results[0].reason, 'upstream_error_event')
                    self.assertIsNone(response.terminal_observed)
                    self.assertNotIn('private-error-message', repr(self.results))
                    response.close()
                    self.assertEqual(len(self.results), 1)

    def test_error_event_requires_blank_dispatch_and_top_level_fields(self):
        for protocol in ('anthropic', 'responses', 'chat'):
            for body in [frame('error')[:-1], frame('error')[:-2],
                         b'data: {"nested":{"type":"error","error":{"code":502}}}\n\n',
                         b'data: {"text":"error","type":"delta"}\n\n',
                         b'data: "error"\n\n', b'data: {"error":null}\n\n']:
                with self.subTest(protocol=protocol, body=body):
                    response = self.make(body, protocol)
                    self.assertEqual(response.read(), body)
                    self.assertEqual(self.results[0].reason, 'eof_before_terminal')
                    self.assertFalse(self.results[0].success)

    def test_oversized_explicit_error_does_not_fabricate_success(self):
        for protocol in ('anthropic', 'responses', 'chat'):
            for after in (False, True):
                event = b'event: error\n'
                data = b'data: ' + b'x' * 200000 + b'\n'
                body = (data + event if after else event + data) + b'\n'
                response = self.make(body, protocol, max_sse_buffer=128)
                actual = bytearray()
                while part := response.read(37):
                    actual.extend(part)
                    self.assertLessEqual(len(response._parser.line) + len(response._parser.data), 128)
                self.assertEqual(bytes(actual), body)
                self.assertEqual(len(self.results), 1)
                self.assertEqual(self.results[0].reason, 'upstream_error_event')
                self.assertFalse(self.results[0].success)
                self.assertIsNone(response.terminal_observed)

    def test_dispatched_error_in_partial_preserves_exception_and_reports_once(self):
        error = http.client.IncompleteRead(frame('error'))
        response = self.make(protocol='responses')
        def fail(*args):
            raise error
        response._response.read = fail
        with self.assertRaises(http.client.IncompleteRead) as raised:
            response.read()
        self.assertIs(raised.exception, error)
        self.assertEqual(len(self.results), 1)
        self.assertEqual(self.results[0].reason, 'upstream_error_event')
        self.assertFalse(self.results[0].success)

    def test_partial_observation_is_bounded_and_callback_stays_once(self):
        partial = b'data: ' + b'x' * 200000 + b'\n\n' + frame('response.completed')
        error = http.client.IncompleteRead(partial)
        response = self.make(protocol='responses', max_sse_buffer=128)
        def fail(*args):
            raise error
        response._response.read = fail
        for _ in range(2):
            with self.assertRaises(http.client.IncompleteRead) as raised:
                response.read()
            self.assertIs(raised.exception, error)
            self.assertIs(error.partial, partial)
            self.assertLessEqual(len(response._parser.line) + len(response._parser.data), 128)
        self.assertEqual(len(self.results), 1)
        self.assertTrue(self.results[0].success)
        response.close()
        self.assertEqual(len(self.results), 1)

    def test_dispatched_error_wins_over_later_success_in_same_read(self):
        for protocol, terminal in [('responses', frame('response.completed')),
                                   ('anthropic', frame('message_stop')),
                                   ('chat', b'data: [DONE]\n\n')]:
            body = frame('error') + terminal
            response = self.make(body, protocol)
            self.assertEqual(response.read(), body)
            self.assertEqual(len(self.results), 1)
            self.assertEqual(self.results[0].reason, 'upstream_error_event')
            self.assertFalse(self.results[0].success)

    def test_ssl_eof_bare_and_wrapped_open_and_read_identity(self):
        for wrapped in (False, True):
            eof = ssl.SSLEOFError(8, 'private-eof-message')
            error = urllib.error.URLError(eof) if wrapped else eof
            results = []
            self.assertIs(observe_gateway_error(error, on_result=results.append), error)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].reason, 'eof')
            response = self.make(protocol='responses')
            def fail(*args):
                raise error
            response._response.read = fail
            with self.assertRaises(type(error)) as raised:
                response.read()
            self.assertIs(raised.exception, error)
            if wrapped:
                self.assertIs(raised.exception.reason, eof)
            self.assertEqual(len(self.results), 1)
            self.assertEqual(self.results[0].reason, 'eof')
            self.assertNotIn('private-eof-message', repr(self.results))

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

    def test_explicit_terminal_after_oversized_multiline_data(self):
        body = b'data: x\n' * 50000 + b'event: message_stop\n\n'
        results = []
        response = wrap_gateway_response(R(body), 'anthropic', on_result=results.append, max_sse_buffer=128)
        self.assertEqual(response.read(), body)
        self.assertTrue(results[0].success)
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
