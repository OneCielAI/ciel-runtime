"""Loopback-only native Responses timeout route regressions; no runtime imports."""
import dataclasses
import http.client
import http.server
import io
import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest import mock

TIMEOUT_SSE = (b'event: error\n'
    b'data: {"type":"error","error":{"type":"api_error",'
    b'"message":"TimeoutError: The read operation timed out"}}\n\n')


class TimeoutRouteTests(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory(prefix="timeout-route-")
        self.addCleanup(home.cleanup)
        env = {k: v for k, v in os.environ.items() if not k.startswith("CIEL_RUNTIME_")}
        for k in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
            env[k] = home.name
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            env["CIEL_RUNTIME_ROUTER_PORT"] = str(sock.getsockname()[1])
        env.update(CIEL_RUNTIME_CONFIG_DIR=home.name, CIEL_RUNTIME_TEST_ISOLATED="1")
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        from ciel_runtime_support import transient_retry, provider_responses_passthrough
        from test_provider_responses_gateway_replay_guard import passthrough, COMPLETE, body
        from test_cielairouter_transient_integration import ERROR
        self.retry, self.factory = transient_retry, passthrough
        self.complete, self.body, self.unavailable = COMPLETE, body, ERROR
        self.waits = []
        real_open = transient_retry.open_with_transient_retry
        patch = mock.patch.object(provider_responses_passthrough, "open_with_transient_retry",
            side_effect=lambda *a, **kw: real_open(*a, sleep=self.waits.append, **kw))
        patch.start()
        self.addCleanup(patch.stop)

    def server(self, handler):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def fixture(self, script, retries=None, wrapped=False, open_timeout=0.15):
        state = SimpleNamespace(hits=[], gates=[], errors=[], budgets=[], snapshots=[], logs=[])
        owner = self
        class Upstream(http.server.BaseHTTPRequestHandler):
            def do_POST(h):
                raw = h.rfile.read(int(h.headers.get("content-length") or 0))
                index = len(state.hits)
                state.hits.append((h.path, raw))
                gate = threading.Event()
                state.gates.append(gate)
                action = script[min(index, len(script)-1)]
                h.close_connection = True
                if action == "timeout":
                    gate.wait(open_timeout + 3)  # No status line: opener must hit a real socket timeout.
                    return
                if action == "reset":
                    return
                payload = TIMEOUT_SSE if action == "504" else owner.unavailable if action == "503" else owner.complete
                h.send_response(int(action) if action in ("503", "504") else 200)
                h.send_header("content-type", "text/event-stream")
                h.send_header("content-length", str(len(payload)))
                h.end_headers()
                h.wfile.flush()
                if action == "body_timeout":
                    gate.wait(3)
                    return
                try:
                    h.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *_args):
                pass
        upstream = self.server(Upstream)
        self.addCleanup(lambda: [g.set() for g in state.gates])
        state.config = {"base_url": "http://127.0.0.1:" + str(upstream.server_address[1]),
                        "responses_stream_truncation_retries": 0}
        if retries is not None:
            state.config["gateway_retries"] = retries
        state.handler = SimpleNamespace(headers={}, wfile=io.BytesIO(),
            send_response=mock.Mock(), send_header=mock.Mock(), end_headers=mock.Mock())
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def dispatch(req, *, timeout, **_kw):
            state.budgets.append(self.retry.budget_for(state.config, "cielairouter"))
            h = state.handler
            if isinstance(h.send_response, mock.Mock):
                state.snapshots.append((h.send_response.call_count, h.end_headers.call_count, h.wfile.getvalue()))
            try:
                return opener.open(req, timeout=timeout)
            except TimeoutError as exc:
                state.errors.append(exc)
                state.gates[-1].set()
                if wrapped:
                    raise urllib.error.URLError(exc) from exc
                raise
        base = self.factory(dispatch, state.logs)
        state.reselect = mock.Mock(side_effect=lambda c: dict(c))
        state.route = type(base)(dataclasses.replace(base._ports,
            timeout_seconds=lambda _c: open_timeout, reselect=state.reselect))
        return state

    def forward(self, s):
        return s.route.forward(s.handler, "cielairouter", s.config, self.body())

    def check(self, s, count, minimum_remaining=40):
        self.assertEqual(count, len(s.hits))
        self.assertEqual(["/v1/responses"]*count, [path for path, _ in s.hits])
        self.assertEqual([s.hits[0][1]]*count, [raw for _, raw in s.hits])
        self.assertEqual([(0, 0, b"")]*len(s.snapshots), s.snapshots)
        self.assertEqual(count-1, len(self.waits))
        self.assertEqual(count-1, s.reselect.call_count)
        if s.budgets[0] is not None:
            self.assertTrue(all(b is s.budgets[0] for b in s.budgets))
            self.assertGreater(s.budgets[0].remaining(), minimum_remaining)

    def test_header_timeout_then_success_direct_and_wrapped(self):
        for wrapped in (False, True):
            with self.subTest(wrapped=wrapped):
                self.waits.clear()
                s = self.fixture(["timeout", "complete"], wrapped=wrapped)
                self.assertEqual({"delivery": True}, self.forward(s))
                self.check(s, 2)
                self.assertEqual(1, len(s.errors))
                self.assertIs(type(s.errors[0]), TimeoutError)
                self.assertIn("timed out", str(s.errors[0]))
                self.assertEqual(self.complete, s.handler.wfile.getvalue())
                s.handler.send_response.assert_called_once_with(200)
                s.handler.end_headers.assert_called_once_with()

    def test_default_scope_can_retry_after_open_outlasts_old_aggregate_limit(self):
        # No injected budget: the configured per-open timeout exceeds the old
        # aggregate deadline. Scale the old threshold, not the actual policy.
        self.waits.clear()
        s = self.fixture(["timeout", "complete"], 1)
        with mock.patch.object(self.retry, "DEFAULT_SECONDS", 0.05):
            self.forward(s)
        self.check(s, 2, minimum_remaining=0)
        self.assertGreater(s.budgets[0].deadline_seconds, 0.30)
        self.assertEqual(self.complete, s.handler.wfile.getvalue())
        s.handler.send_response.assert_called_once_with(200)

    def test_timeout_default_cap_and_zero(self):
        for retries, count in ((None, 4), (99, 11), (0, 1)):
            with self.subTest(retries=retries):
                self.waits.clear()
                s = self.fixture(["timeout"], retries)
                with self.assertRaises(TimeoutError):
                    self.forward(s)
                self.check(s, count)
                self.assertEqual(count, len(s.errors))
                s.handler.send_response.assert_not_called()
                s.handler.send_header.assert_not_called()
                s.handler.end_headers.assert_not_called()
                self.assertEqual(b"", s.handler.wfile.getvalue())

    def test_exact_http504_sse_success_exhausted_and_zero(self):
        for retries, success, count in ((2, True, 2), (2, False, 3), (0, False, 1)):
            with self.subTest(retries=retries, success=success):
                self.waits.clear()
                s = self.fixture(["504", "complete"] if success else ["504"], retries)
                if success:
                    self.forward(s)
                    s.handler.send_response.assert_called_once_with(200)
                    self.assertEqual(self.complete, s.handler.wfile.getvalue())
                else:
                    with self.assertRaises(urllib.error.HTTPError) as raised:
                        self.forward(s)
                    self.assertEqual(504, raised.exception.code)
                    self.assertEqual(TIMEOUT_SSE, raised.exception.read())
                    s.handler.send_response.assert_not_called()
                    s.handler.end_headers.assert_not_called()
                    self.assertEqual(b"", s.handler.wfile.getvalue())
                self.check(s, count)

    def test_timeout_and_http_rejections_spend_same_budget(self):
        s = self.fixture(["timeout", "503", "504", "complete"], 2)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.forward(s)
        self.assertEqual(504, raised.exception.code)
        self.assertEqual(TIMEOUT_SSE, raised.exception.read())
        self.check(s, 3)
        s.handler.send_response.assert_not_called()
        self.assertEqual(b"", s.handler.wfile.getvalue())

    def test_accepted_body_timeout_does_not_replay(self):
        from ciel_runtime_support.upstream_error_policy import UpstreamStreamReadError
        s = self.fixture(["body_timeout", "complete"], 3)
        with self.assertRaises(UpstreamStreamReadError) as raised:
            self.forward(s)
        self.assertIsInstance(raised.exception.error, TimeoutError)
        self.assertTrue(raised.exception.downstream_started)
        self.check(s, 1)
        s.handler.send_response.assert_called_once_with(200)

    def test_non_timeout_preheader_disconnect_does_not_replay(self):
        s = self.fixture(["reset", "complete"], 3)
        with self.assertRaises(http.client.RemoteDisconnected):
            self.forward(s)
        self.check(s, 1)
        s.handler.send_response.assert_not_called()

    def test_real_downstream_native_route_success_or_http504_sse(self):
        from ciel_runtime_support.openai_responses_router import _handle_provider_responses_route
        from ciel_runtime_support.openai_responses_stream import (
            OpenAIResponsesStreamServices, write_openai_responses_error)
        for failure in ("timeout", "504"):
            for retries, success, count in ((2, True, 2), (2, False, 3), (0, False, 1)):
                with self.subTest(failure=failure, retries=retries, success=success):
                    self.waits.clear()
                    s = self.fixture([failure, "complete"] if success else [failure], retries)
                    statuses, errors = [], []
                    stream_services = OpenAIResponsesStreamServices(mock.Mock(), mock.Mock())
                    def write_error(h, message, **kw):
                        errors.append((message, kw))
                        write_openai_responses_error(h, message, services=stream_services, **kw)
                    services = SimpleNamespace(
                        core=SimpleNamespace(event_bus=SimpleNamespace(publish=mock.Mock()),
                            request_id=lambda: "timeout-test", input_as_list=lambda v: v,
                            is_client_disconnect=lambda e: False, log=mock.Mock()),
                        routing=SimpleNamespace(dump_request=mock.Mock(),
                            forward_provider_responses=s.route.forward),
                        delivery=SimpleNamespace(mark_success=mock.Mock(), mark_failed=mock.Mock(),
                            commit=mock.Mock()),
                        output=SimpleNamespace(write_error=write_error,
                            upstream_error_message=lambda exc, raw: raw))
                    class Downstream(http.server.BaseHTTPRequestHandler):
                        def send_response(h, code, message=None):
                            statuses.append(code)
                            super().send_response(code, message)
                        def do_POST(h):
                            payload = json.loads(h.rfile.read(int(h.headers.get("content-length") or 0)))
                            h.close_connection = True
                            s.handler = h
                            _handle_provider_responses_route(h, "cielairouter", s.config, payload, services)
                        def log_message(self, *_args):
                            pass
                    server = self.server(Downstream)
                    client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=8)
                    try:
                        client.request("POST", "/v1/responses", json.dumps(self.body()),
                                       {"Content-Type": "application/json"})
                        response = client.getresponse()
                        raw = response.read()
                    finally:
                        client.close()
                    self.check(s, count)
                    self.assertEqual([200 if success else 504], statuses)
                    self.assertEqual(statuses[0], response.status)
                    print(f"responses_loopback failure={failure} retries={retries} "
                          f"success={success} upstream_hits={len(s.hits)} "
                          f"http_status={response.status} status_blocks={len(statuses)} "
                          f"sse_error_frames={sum(line == b'event: error' for line in raw.splitlines())} bytes={len(raw)}")
                    if success:
                        self.assertEqual(self.complete, raw)
                        self.assertEqual([], errors)
                        services.delivery.mark_success.assert_called_once()
                        services.delivery.mark_failed.assert_not_called()
                    else:
                        self.assertIn("text/event-stream", response.getheader("content-type"))
                        self.assertEqual(1, raw.count(b"event: error\n"))
                        self.assertNotIn(b"response.created", raw)
                        self.assertNotIn(b"response.completed", raw)
                        event = json.loads(raw.split(b"data: ", 1)[1])
                        self.assertEqual("error", event["type"])
                        self.assertEqual("api_error" if failure == "timeout" else "overloaded_error",
                                         event["error"]["type"])
                        self.assertIn("TimeoutError", event["error"]["message"])
                        if failure == "504":
                            self.assertIn("The read operation timed out", event["error"]["message"])
                        services.delivery.mark_failed.assert_called_once()
                        services.delivery.mark_success.assert_not_called()
                        services.delivery.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
