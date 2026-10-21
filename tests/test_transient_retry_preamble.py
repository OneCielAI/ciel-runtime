"""Initial SSE preflight, response contract and real HTTP hit counts."""

import io
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ciel_runtime_support import transient_retry as retry

ERROR = b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error","message":"busy"}}\n\n'
OUTPUT = b'event: content_block_delta\ndata: {"delta":{"text":"one"}}\n\n'


class Response(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.status = 200
        self.headers = {"Content-Type": "text/event-stream", "X-Test": "original"}


class PreambleTests(unittest.TestCase):
    def opening(self, results, limit=3, **kwargs):
        calls = []

        def once():
            calls.append(1)
            item = results.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        response = retry.open_with_transient_retry(
            once,
            provider="cielairouter",
            config={"gateway_retries": limit},
            log=lambda *_: None,
            sleep=lambda _: None,
            **kwargs,
        )
        return response, calls

    def test_initial_error_retried_before_output(self):
        first, second = Response(ERROR), Response(OUTPUT)
        response, calls = self.opening([first, second])
        self.assertTrue(first.closed)
        self.assertEqual(2, len(calls))
        self.assertEqual(OUTPUT, response.read())
        response.close()
        self.assertTrue(second.closed)

    def test_mixed_and_incomplete_and_output_first_never_replayed(self):
        for data in [
            ERROR + OUTPUT,
            OUTPUT + ERROR,
            ERROR[:-1],
            b"data: unknown\n\n" + ERROR,
        ]:
            with self.subTest(data=data):
                response, calls = self.opening([Response(data)])
                self.assertEqual([1], calls)
                self.assertEqual(data, response.read())
                response.close()

    def test_shared_attempt_cap_across_http_and_sse(self):
        http = urllib.error.HTTPError(
            "http://fixture", 503, "busy", {}, io.BytesIO(ERROR)
        )
        last = Response(ERROR)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.opening([http, last], limit=1)
        self.assertEqual(200, caught.exception.code)
        self.assertEqual("original", caught.exception.headers["X-Test"])
        self.assertTrue(retry.retry_handled(caught.exception))
        self.assertEqual(ERROR, caught.exception.read())
        self.assertTrue(last.closed)
        caught.exception.close()

    def test_wrapper_read_read1_readline_iteration_readinto_context(self):
        for method in [
            "read",
            "read1",
            "readline",
            "iteration",
            "readinto",
            "readlines",
        ]:
            source = Response(OUTPUT)
            response, _ = self.opening([source])
            with response as opened:
                self.assertIs(opened, response)
                self.assertEqual(200, opened.status)
                self.assertEqual(b"", opened.read(0))
                if method == "read":
                    result = opened.read(7) + opened.read()
                elif method == "read1":
                    result = opened.read1(7) + opened.read1()
                elif method == "readline":
                    result = opened.readline(7) + opened.readline() + opened.read()
                elif method == "iteration":
                    result = b"".join(opened)
                elif method == "readlines":
                    result = b"".join(opened.readlines())
                else:
                    buf = bytearray(7)
                    n = opened.readinto(buf)
                    result = bytes(buf[:n]) + opened.read()
                self.assertEqual(OUTPUT, result)
            self.assertTrue(source.closed)
            self.assertTrue(response.closed)

    def test_output_return_closes_retry_window(self):
        config = {}
        with retry.request_scope(config) as budget:
            response, _ = self.opening([Response(OUTPUT)])
            response.read1(1)
            self.assertTrue(budget.exposed)
            with self.assertRaisesRegex(RuntimeError, "committed or stopped"):
                self.opening([Response(OUTPUT)])
            response.close()

    def test_loopback_real_http503_sse200_then_success_exact_three_hits(self):
        hits = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                hits.append(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                code, body = (
                    (503, ERROR)
                    if len(hits) == 1
                    else (200, ERROR if len(hits) == 2 else OUTPUT)
                )
                self.send_response(code)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/messages",
                data=b"same-payload",
            )
            response = retry.open_with_transient_retry(
                lambda: opener.open(request, timeout=2),
                provider="cielairouter",
                config={},
                log=lambda *_: None,
                sleep=lambda _: None,
                request=request,
            )
            self.assertEqual(OUTPUT, response.read())
            response.close()
            self.assertEqual([b"same-payload"] * 3, hits)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_real_loopback_partial_chunk_header_and_delayed_first_token_are_bounded(
        self,
    ):
        for chunked in [False, True]:
            release = threading.Event()

            class Handler(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"

                def do_GET(self):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    if chunked:
                        self.send_header("Transfer-Encoding", "chunked")
                    else:
                        self.send_header("Content-Length", str(len(OUTPUT)))
                    self.end_headers()
                    if chunked:
                        self.wfile.write(b"3")  # incomplete chunk-size line
                        self.wfile.flush()
                    release.wait(2)
                    if chunked:
                        self.wfile.write(b"b\r\n" + OUTPUT + b"\r\n0\r\n\r\n")
                    else:
                        self.wfile.write(OUTPUT)

                def log_message(self, *_):
                    pass

            # OUTPUT is 59 bytes = 0x3b; verify this fixture assumption.
            self.assertEqual(59, len(OUTPUT))
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                started = time.monotonic()
                response = retry.open_with_transient_retry(
                    lambda: opener.open(
                        f"http://127.0.0.1:{server.server_port}/", timeout=2
                    ),
                    provider="cielairouter",
                    config={},
                    log=lambda *_: None,
                )
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 0.5)
                release.set()
                self.assertEqual(OUTPUT, response.read())
                response.close()
            finally:
                release.set()
                server.shutdown()
                server.server_close()
                thread.join(2)
