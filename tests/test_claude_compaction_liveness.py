"""Real downstream sockets for semantic preparation liveness."""
import http.client
import http.server
import io
import json
import socket
import threading
import time
import unittest
import urllib.error
from unittest import mock
from dataclasses import replace

from ciel_runtime_support import claude_router as route
from ciel_runtime_support import transient_retry
from ciel_runtime_support.context_compaction import ContextCompactionFailed
from test_claude_transient_context_recovery import route_services


class CompactionLivenessTests(unittest.TestCase):
    def serve(self, cap, *, upstream_error=None, stream=True, provider="cielairouter", upstream_json=False, remote=False, expired_budget=False):
        opened = []
        finished = threading.Event()
        errors = []

        def open_request(*args, **kwargs):
            opened.append(1)
            if expired_budget:
                transient_retry.bounded_timeout(args[5], args[4], 2)
            if upstream_error is not None:
                raise upstream_error
            response = io.BytesIO(b'event: message_stop\ndata: {"type":"message_stop"}\n\n')
            response.status = 200
            response.headers = {"content-type": "application/json" if upstream_json else "text/event-stream"}
            return response

        patch = mock.patch.dict("os.environ", {"CIEL_RUNTIME_ANTHROPIC_STREAM_KEEPALIVE_SECONDS": "0.02"})
        patch.start()
        self.addCleanup(patch.stop)
        services = route_services("http://fixture.invalid", open_request)
        services = replace(services, normalization=replace(services.normalization, cap_body=cap))
        def relay(handler, response, *args, **kwargs):
            handler.wfile.write(response.read())
            handler.wfile.flush()
        services = replace(services, response=replace(services.response, rebatch_sse=relay))

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                if remote:
                    from ciel_runtime_support.remote_bridge import REMOTE_BRIDGE_CONTEXT_ATTRIBUTE
                    setattr(self, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)
                try:
                    config = {"stream_enabled": stream, "gateway_retries": 3}
                    if expired_budget:
                        budget = transient_retry.TransientRetryBudget(3, 60, clock=lambda: 0)
                        budget.started = -120
                        with transient_retry.request_scope(config, provider, budget=budget):
                            route.handle_claude_messages_post(services, self, {}, provider,
                                config, "/v1/messages", body)
                    else:
                        route.handle_claude_messages_post(services, self, {}, provider,
                            config, "/v1/messages", body)
                except Exception as exc:
                    errors.append(exc)
                finally:
                    finished.set()
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address, opened, finished, errors

    @staticmethod
    def request(connection, stream=True):
        connection.request("POST", "/v1/messages", json.dumps({"model": "model",
            "messages": [{"role": "user", "content": "Summarize the conversation so far."}],
            "stream": stream, "max_tokens": 100}), {"content-type": "application/json"})

    @staticmethod
    def start(config):
        config["_context_compact_on_semantic_start"]()

    def test_baseline_silent_preparation_exceeds_header_timeout(self):
        def cap(_provider, _config, body):
            time.sleep(0.2)
            return body
        address, _, finished, _ = self.serve(cap)
        conn = http.client.HTTPConnection(*address, timeout=0.04)
        self.addCleanup(conn.close)
        self.request(conn)
        with self.assertRaises((TimeoutError, socket.timeout)):
            conn.getresponse()
        conn.close()
        self.assertTrue(finished.wait(2))

    def test_semantic_start_flushes_headers_before_delayed_generation(self):
        def cap(_provider, config, body):
            self.start(config)
            time.sleep(0.2)
            return body
        address, opened, finished, errors = self.serve(cap)
        conn = http.client.HTTPConnection(*address, timeout=0.08)
        self.addCleanup(conn.close)
        self.request(conn)
        response = conn.getresponse()
        self.assertEqual(200, response.status)
        first = response.readline()
        self.assertEqual(b"event: ping\n", first)
        response.fp.raw._sock.settimeout(2)
        raw = first + response.read()
        self.assertIn(b"message_stop", raw)
        self.assertNotIn(b"HTTP/1.", raw)
        self.assertEqual(1, len(opened))
        self.assertTrue(finished.wait(2))
        self.assertEqual([], errors)

    def test_periodic_ping_keeps_short_body_idle_timeout_alive(self):
        def cap(_provider, config, body):
            self.start(config)
            time.sleep(1.2)
            return body
        address, _, _, errors = self.serve(cap)
        conn = http.client.HTTPConnection(*address, timeout=0.5)
        self.addCleanup(conn.close)
        self.request(conn)
        response = conn.getresponse()
        raw = response.read()
        self.assertGreater(raw.count(b"event: ping"), 2)
        self.assertIn(b"message_stop", raw)
        self.assertEqual([], errors)

    def test_semantic_preparation_does_not_expire_final_open_budget(self):
        def cap(_provider, config, body):
            self.start(config)
            return body
        address, opened, _, errors = self.serve(cap, expired_budget=True)
        conn = http.client.HTTPConnection(*address, timeout=2)
        self.addCleanup(conn.close)
        self.request(conn)
        raw = conn.getresponse().read()
        self.assertIn(b"message_stop", raw)
        self.assertNotIn(b"event: error", raw)
        self.assertEqual(1, len(opened))
        self.assertEqual([], errors)

    def test_real_semantic_work_starts_stream_before_map_finishes(self):
        from test_context_compaction import ContextCompactionTests
        case = ContextCompactionTests()
        semantic_services = case.services()
        def generate(*args, **kwargs):
            time.sleep(0.2)
            return "real mapped summary"
        semantic_services.workflow.request_summary.side_effect = generate
        def cap(_provider, config, body):
            case.compact(semantic_services, config)
            return body
        address, opened, _, errors = self.serve(cap)
        conn = http.client.HTTPConnection(*address, timeout=0.08)
        self.addCleanup(conn.close)
        self.request(conn)
        response = conn.getresponse()
        self.assertEqual(b"event: ping\n", response.readline())
        response.fp.raw._sock.settimeout(2)
        raw = response.read()
        self.assertIn(b"message_stop", raw)
        self.assertEqual(1, semantic_services.workflow.request_summary.call_count)
        self.assertEqual(1, len(opened))
        self.assertEqual([], errors)

    def test_post_commit_http_error_is_sse_not_second_headers(self):
        def cap(_provider, config, body):
            self.start(config)
            return body
        failure = urllib.error.HTTPError("http://fixture.invalid", 400, "bad tools", {},
            io.BytesIO(b'{"type":"error","error":{"type":"invalid_request_error","message":"bad tools"}}'))
        address, _, _, errors = self.serve(cap, upstream_error=failure)
        conn = http.client.HTTPConnection(*address, timeout=2)
        self.addCleanup(conn.close)
        self.request(conn)
        response = conn.getresponse()
        raw = response.read()
        self.assertEqual(200, response.status)
        self.assertEqual(1, raw.count(b"event: error"))
        self.assertNotIn(b"HTTP/1.", raw)
        self.assertNotIn(b"message_stop", raw)
        self.assertEqual([], errors)

    def test_post_commit_semantic_failure_is_not_success(self):
        def cap(_provider, config, _body):
            self.start(config)
            raise ContextCompactionFailed("deadline_exceeded")
        address, opened, _, errors = self.serve(cap)
        conn = http.client.HTTPConnection(*address, timeout=2)
        self.addCleanup(conn.close)
        self.request(conn)
        raw = conn.getresponse().read()
        self.assertEqual(1, raw.count(b"event: error"))
        self.assertNotIn(b"message_stop", raw)
        self.assertEqual([], opened)
        self.assertEqual([], errors)

    def test_unexpected_json_after_commit_fails_as_stream_error(self):
        def cap(_provider, config, body):
            self.start(config)
            return body
        address, _, _, errors = self.serve(cap, upstream_json=True)
        conn = http.client.HTTPConnection(*address, timeout=2)
        self.addCleanup(conn.close)
        self.request(conn)
        raw = conn.getresponse().read()
        self.assertEqual(1, raw.count(b"event: error"))
        self.assertNotIn(b"message_stop", raw)
        self.assertEqual([], errors)

    def test_native_anthropic_does_not_install_semantic_start(self):
        configs = []
        def cap(_provider, config, body):
            configs.append(config)
            return body
        address, _, _, _ = self.serve(cap, provider="anthropic")
        conn = http.client.HTTPConnection(*address, timeout=2)
        self.addCleanup(conn.close)
        self.request(conn)
        conn.getresponse().read()
        self.assertNotIn("_context_compact_on_semantic_start", configs[0])

    def test_ordinary_stream_does_not_open_early_response(self):
        def cap(_provider, _config, body):
            return body
        address, _, _, errors = self.serve(cap)
        conn = http.client.HTTPConnection(*address, timeout=2)
        self.addCleanup(conn.close)
        self.request(conn)
        raw = conn.getresponse().read()
        self.assertNotIn(b"event: ping", raw)
        self.assertIn(b"message_stop", raw)
        self.assertEqual([], errors)

    def test_nonstream_does_not_install_semantic_start(self):
        configs = []
        def cap(_provider, config, body):
            configs.append(config)
            return body
        address, _, _, _ = self.serve(cap, stream=False)
        conn = http.client.HTTPConnection(*address, timeout=2)
        self.addCleanup(conn.close)
        self.request(conn, stream=False)
        conn.getresponse().read()
        self.assertNotIn("_context_compact_on_semantic_start", configs[0])

    def test_remote_bridge_does_not_install_semantic_start(self):
        configs = []
        def cap(_provider, config, body):
            configs.append(config)
            return body
        address, _, _, _ = self.serve(cap, remote=True)
        conn = http.client.HTTPConnection(*address, timeout=2)
        self.addCleanup(conn.close)
        self.request(conn)
        conn.getresponse().read()
        self.assertNotIn("_context_compact_on_semantic_start", configs[0])

    def test_disconnect_cancels_before_final_upstream(self):
        entered = threading.Event()
        cancelled = threading.Event()
        def cap(_provider, config, body):
            self.start(config)
            entered.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if config["_context_compact_cancelled"]():
                    cancelled.set()
                    raise ContextCompactionFailed("client_cancelled")
                time.sleep(0.01)
            return body
        address, opened, finished, _ = self.serve(cap)
        conn = http.client.HTTPConnection(*address, timeout=2)
        self.request(conn)
        response = conn.getresponse()
        response.readline()
        self.assertTrue(entered.wait(1))
        response.close()
        conn.close()
        self.assertTrue(cancelled.wait(3))
        self.assertTrue(finished.wait(2))
        self.assertEqual([], opened)


class SemanticAdmissionTests(unittest.TestCase):
    def test_start_hook_precedes_generation_after_preflight(self):
        from test_context_compaction import ContextCompactionTests
        case = ContextCompactionTests()
        services = case.services()
        events = []
        def generate(*args, **kwargs):
            events.append("generate")
            return "summary"
        services.workflow.request_summary.side_effect = generate
        case.compact(services, {"_context_compact_on_semantic_start": lambda: events.append("started")})
        self.assertEqual(["started", "generate"], events)

    def test_invalid_preflight_does_not_start_response(self):
        from test_context_compaction import ContextCompactionTests
        case = ContextCompactionTests()
        services = case.services(available=False)
        events = []
        with self.assertRaises(ContextCompactionFailed):
            case.compact(services, {"_context_compact_on_semantic_start": lambda: events.append("started")})
        self.assertEqual([], events)

    def test_cancelled_transport_failure_does_not_retry(self):
        from test_context_compaction import ContextCompactionTests
        case = ContextCompactionTests()
        services = case.services()
        cancelled = threading.Event()
        def generate(*args, **kwargs):
            cancelled.set()
            raise TimeoutError("abandoned request")
        services.workflow.request_summary.side_effect = generate
        with self.assertRaisesRegex(ContextCompactionFailed, "client_cancelled"):
            case.compact(services, {"_context_compact_cancelled": cancelled.is_set})
        self.assertEqual(1, services.workflow.request_summary.call_count)
