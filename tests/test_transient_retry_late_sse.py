import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ciel_runtime_support import transient_retry as retry

NL = bytes([10])
ERROR = NL.join([b'event: error', b'data: {"type":"error","error":{"type":"api_error","message":"Model timed out (HTTP 504)"}}', b'', b''])
OUTPUT = NL.join([b'event: message_start', b'data: {"type":"message_start","message":{"id":"msg_ok"}}', b'', b''])
PING = NL.join([b': alive', b'', b'event: ping', b'data: {"type":"ping"}', b'', b''])


class LateSSETests(unittest.TestCase):
    def serve(self, bodies, config=None):
        hits = []
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def do_POST(self):
                hits.append(self.rfile.read(int(self.headers.get("Content-Length",0))))
                body = bodies[min(len(hits)-1,len(bodies)-1)]
                self.send_response(200)
                self.send_header("Content-Type","text/event-stream")
                self.send_header("Content-Length",str(len(body)))
                self.end_headers()
                time.sleep(.25)
                self.wfile.write(body)
                self.wfile.flush()
            def log_message(self,*_): pass
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req=urllib.request.Request(f"http://127.0.0.1:{server.server_port}/v1/messages",data=b"same-payload")
        try:
            response=retry.open_with_transient_retry(lambda:op.open(req,timeout=3),provider="cielairouter",
                config=config or {},log=lambda *_:None,sleep=lambda _:None,request=req)
            with response:
                result=response.read()
            return result,hits
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_delayed_error_only_retries_before_any_message_start(self):
        result,hits=self.serve([PING+ERROR,OUTPUT])
        self.assertEqual(result,OUTPUT)
        self.assertEqual(hits,[b"same-payload"]*2)

    def test_after_message_start_never_replays(self):
        result,hits=self.serve([OUTPUT+ERROR,OUTPUT])
        self.assertEqual(result,OUTPUT+ERROR)
        self.assertEqual(len(hits),1)

    def test_terminal_auth_error_never_replays(self):
        auth=ERROR.replace(b"api_error",b"authentication_error").replace(b"Model timed out (HTTP 504)",b"invalid API key")
        result,hits=self.serve([auth,OUTPUT])
        self.assertEqual(result,auth)
        self.assertEqual(len(hits),1)

    def test_exhaustion_stays_bounded_and_relays_final_error(self):
        result,hits=self.serve([ERROR],{"gateway_retries":1})
        self.assertEqual(result,ERROR)
        self.assertEqual(len(hits),2)

    def test_mixed_error_and_generation_never_replays(self):
        result, hits = self.serve([ERROR + OUTPUT, OUTPUT])
        self.assertEqual(result, ERROR + OUTPUT)
        self.assertEqual(len(hits), 1)

    def test_unknown_and_partial_error_never_replay(self):
        for raw in (b"data: unknown" + NL + NL + ERROR, ERROR[:-1]):
            result, hits = self.serve([raw, OUTPUT])
            self.assertEqual(result, raw)
            self.assertEqual(len(hits), 1)

    def test_crlf_and_comments_before_error(self):
        raw = (PING + ERROR).replace(NL, bytes([13, 10]))
        result, hits = self.serve([raw, OUTPUT])
        self.assertEqual(result, OUTPUT)
        self.assertEqual(len(hits), 2)

    def test_comment_byte_bound_closes_retry_window(self):
        raw = b":" + b"x" * retry.MAX_ERROR_BYTES + NL + NL + ERROR
        result, hits = self.serve([raw, OUTPUT])
        self.assertEqual(result, raw)
        self.assertEqual(len(hits), 1)

    def open_raw(self, bodies, open_hook=None, sleep=None, budget=None):
        hits = []
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def do_POST(self):
                hits.append(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                body = bodies[min(len(hits) - 1, len(bodies) - 1)]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                time.sleep(.25)
                self.wfile.write(body)
                self.wfile.flush()
            def log_message(self, *_): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/v1/messages", data=b"same-payload")
        def open_once():
            timeout = retry.bounded_timeout({}, "cielairouter", 300)
            response = op.open(req, timeout=min(timeout, 3))
            return open_hook(response) if open_hook else response
        with retry.request_scope({}, "cielairouter", budget=budget):
            response = retry.open_with_transient_retry(open_once, provider="cielairouter", config={},
                log=lambda *_: None, sleep=sleep or (lambda _: None), request=req)
        return response, hits

    def test_close_during_retry_wait_never_reopens(self):
        holder = []
        response, hits = self.open_raw([ERROR, OUTPUT], sleep=lambda _: holder[0].close())
        holder.append(response)
        with self.assertRaises(retry.UpstreamClientDisconnected):
            response.read()
        self.assertEqual(len(hits), 1)

    def test_close_during_reopen_closes_the_new_response(self):
        holder, reopened = [], []
        def hook(raw):
            if holder:
                holder[0].close()
                reopened.append(raw)
            return raw
        response, hits = self.open_raw([ERROR, OUTPUT], open_hook=hook)
        holder.append(response)
        with self.assertRaises(retry.UpstreamClientDisconnected):
            response.read()
        self.assertEqual(len(hits), 2)
        self.assertTrue(reopened[0].isclosed())

    def test_error_after_five_minutes_retries_within_sized_deadline(self):
        now = [1000.0]
        budget = retry.TransientRetryBudget(retry.DEFAULT_RETRIES, retry.DEFAULT_SECONDS,
                                            clock=lambda: now[0], timeout_pending=True)
        response, hits = self.open_raw([PING + ERROR, OUTPUT], budget=budget)
        self.assertGreater(budget.deadline_seconds, 1200)
        now[0] += 330  # live: HTTP 200 after 166 s, SSE error 161 s later
        with response:
            self.assertEqual(response.read(), OUTPUT)
        self.assertEqual(hits, [b"same-payload"] * 2)

    def test_error_after_deadline_relays_without_reopen(self):
        now = [1000.0]
        budget = retry.TransientRetryBudget(retry.DEFAULT_RETRIES, retry.DEFAULT_SECONDS,
                                            clock=lambda: now[0], timeout_pending=True)
        response, hits = self.open_raw([ERROR, OUTPUT], budget=budget)
        now[0] += budget.deadline_seconds + 1
        with response:
            self.assertEqual(response.read(), ERROR)
        self.assertEqual(len(hits), 1)


class _Scripted:
    """Socketless upstream whose successive reads return bytes or raise."""
    headers = {"content-type": "text/event-stream"}
    status = 200

    def __init__(self, steps):
        self.steps = list(steps)
        self.closed = False

    def read(self, _size=-1):
        if not self.steps:
            return b""
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    def close(self):
        self.closed = True


class LateSSECommitDiagnosticsTests(unittest.TestCase):
    def probe(self, raw, content_type="text/event-stream"):
        logs, opens = [], []
        def open_once():
            response = _Scripted([raw])
            response.headers = {"content-type": content_type}
            opens.append(response)
            return response
        with retry.request_scope({}, "cielairouter"):
            response = retry.open_with_transient_retry(open_once, provider="cielairouter", config={},
                log=lambda level, message: logs.append((level, message)), sleep=lambda _: None)
        with response:
            result = response.read()
        self.assertEqual(result, raw)
        self.assertEqual(len(opens), 1)
        return "\n".join(message for _, message in logs)

    def test_unrecognized_error_shapes_are_observable_without_relaxing_replay(self):
        for raw in (
            ERROR.replace(b'"message":', b'"code":504,"message":'),
            ERROR.replace(b'"type":"error",', b'"type":"error","status":504,'),
        ):
            with self.subTest(raw=raw):
                logs = self.probe(raw)
                self.assertIn("state=armed", logs)
                self.assertIn("reason=unrecognized_frame", logs)
                self.assertNotIn("Model timed out", logs)
                if b'"code":504' in raw:
                    self.assertIn('"code":"number"', logs)
                self.assertNotIn('"code":504', logs)

    def test_mixed_stream_and_terminal_error_diagnostics_do_not_log_payload(self):
        cases = (
            (ERROR + OUTPUT, "multiple_frames"),
            (ERROR + b"data: [DONE]" + NL + NL, "multiple_frames"),
            (b'data: {"type":"ping"}' + NL + NL + ERROR, "multiple_frames"),
            (ERROR + b"data: pending", "trailing_bytes"),
            (ERROR.replace(b"api_error", b"authentication_error").replace(
                b"Model timed out (HTTP 504)", b"PRIVATE-CREDENTIAL invalid API key"), "terminal_verdict"),
            (b":" + b"x" * retry.MAX_ERROR_BYTES, "byte_bound"),
            (ERROR[:-1], "eof"),
        )
        for raw, reason in cases:
            with self.subTest(reason=reason):
                logs = self.probe(raw)
                self.assertIn("reason=" + reason, logs)
                self.assertNotIn("PRIVATE-CREDENTIAL", logs)
                self.assertNotIn("Model timed out", logs)

    def test_wrong_or_missing_content_type_is_observable(self):
        for content_type in ("", "application/json", "PRIVATE-HEADER"):
            with self.subTest(content_type=content_type):
                logs = self.probe(ERROR, content_type)
                self.assertIn("state=not_armed reason=not_event_stream", logs)
                self.assertNotIn("PRIVATE-HEADER", logs)


class LateSSEReadFailureTests(unittest.TestCase):
    def open(self, steps):
        upstream = _Scripted(steps)
        with retry.request_scope({}, "cielairouter"):
            response = retry.open_with_transient_retry(lambda: upstream, provider="cielairouter", config={},
                                                       log=lambda *_: None, sleep=lambda _: None)
        self.assertIsInstance(response, retry._PreOutputSSE)
        return response, upstream

    def test_sized_read_delivers_generation_before_upstream_failure(self):
        response, _ = self.open([OUTPUT, ConnectionResetError(10054, "upstream reset")])
        self.assertEqual(response.read(65536), OUTPUT)
        with self.assertRaises(ConnectionResetError):
            response.read(65536)

    def test_partial_frame_is_delivered_before_upstream_failure(self):
        response, upstream = self.open([OUTPUT[:9], ConnectionResetError(10054, "upstream reset")])
        self.assertEqual(response.read(), OUTPUT[:9])
        with self.assertRaises(ConnectionResetError):
            response.read()
        response.close()
        self.assertTrue(upstream.closed)

    def test_failure_without_held_bytes_raises_unchanged(self):
        response, _ = self.open([ConnectionResetError(10054, "upstream reset")])
        with self.assertRaises(ConnectionResetError):
            response.read(65536)
