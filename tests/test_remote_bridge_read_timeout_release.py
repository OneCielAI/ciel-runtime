"""A started remote-bridge response is never cut by the pre-output arrival bound (R2-O2-F1).

lead-rulings-independent-review-sse-retry-20261113.md, "R2-O2-F1": the arrival
clip covers acceptance only. A remote-bridge chat or responses open runs with
replay off, so no retry loop restores the caller's read timeout; the passthrough
must restore it right after acceptance, before any body read.

Real loopback sockets. The route is the production one (the real
``RemoteBridgeRoutingService.resolve`` sets the bridge marker). The arrival scope
leaves ARRIVAL_LEFT seconds when the open happens, so the open's socket timeout
is clipped to that. The upstream accepts, then waits GAP seconds (more than the
arrival remainder, well inside the provider timeout) before the rest of the body.
The response must complete, and the accepted socket's timeout must be the
provider timeout. The pre-acceptance clip itself must still apply.

"R2-O2-F1 amendment": an HTTP error status with headers is a received response
too. A bridge error body delayed past the arrival remainder (within the provider
timeout) reaches the client complete and unchanged, with no retry.
"""
import http.server
import io
import threading
import time
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support import transient_retry as retry
from ciel_runtime_support.openai_chat_passthrough import OpenAIChatPassthrough, OpenAIChatPassthroughPorts
from ciel_runtime_support.provider_responses_passthrough import (
    ProviderResponsesPassthrough, ProviderResponsesPassthroughPorts,
)
from ciel_runtime_support.remote_bridge import REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, RemoteBridgeRoutingService
from test_transient_retry_preoutput_bound import Clock

ARRIVAL_LEFT, GAP, PROVIDER_TIMEOUT = 0.4, 1.0, 6.0
CHAT_HEAD = b'data: {"id":"c","choices":[{"delta":{"content":"he"}}]}\n\n'
CHAT_TAIL = b'data: {"id":"c","choices":[{"delta":{"content":"llo"}}]}\n\ndata: [DONE]\n\n'
RESP_HEAD = b'event: response.created\ndata: {"type":"response.created","response":{"id":"r"}}\n\n'
RESP_TAIL = (b'event: response.completed\n'
             b'data: {"type":"response.completed","response":{"id":"r","status":"completed"}}\n\n')
ERROR_HEAD = b'{"error":{"type":"overloaded_error",'
ERROR_TAIL = b'"message":"upstream busy"}}'


class Upstream(http.server.BaseHTTPRequestHandler):
    head, tail, status, ctype = b"", b"", 200, "text/event-stream"
    hits = None

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        if self.hits is not None:
            self.hits.append(self.path)
        self.close_connection = True
        self.send_response(self.status)
        self.send_header("content-type", self.ctype)
        self.send_header("content-length", str(len(self.head) + len(self.tail)))
        self.end_headers()
        self.wfile.write(self.head)
        self.wfile.flush()
        time.sleep(GAP)  # an inter-frame gap after acceptance
        try:
            self.wfile.write(self.tail)
        except OSError:
            pass

    def log_message(self, *_args):
        pass


def marked_route(path):
    service = RemoteBridgeRoutingService(normalize_provider=lambda name: name,
                                         parse_bool=lambda value, _default: bool(value), environ={})
    return service.resolve({"current_provider": "cielairouter", "providers": {"cielairouter": {
        "base_url": "http://unused.test", "current_model": "m"}}}, {}, {"model": "m", "stream": True}, path)


class BridgeReadTimeoutReleaseTests(unittest.TestCase):
    def setUp(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.accepted = []  # (open timeout, socket timeout when the body read starts)
        self.response = None

    def serve(self, head, tail, status=200, ctype="text/event-stream"):
        self.hits = []
        handler = type("U", (Upstream,), {"head": head, "tail": tail, "status": status, "ctype": ctype,
                                          "hits": self.hits})
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def urlopen(self, request, *, timeout, provider, pcfg):
        self.open_timeout = timeout
        self.response = self.opener.open(request, timeout=timeout)
        return self.response

    def copy_headers(self, *_args):
        # Called after acceptance and before the first body read.
        self.accepted.append((self.open_timeout, retry._socket(self.response).gettimeout()))

    def bridge_handler(self):
        handler = SimpleNamespace(headers={}, wfile=io.BytesIO(), send_response=mock.Mock(),
                                  end_headers=mock.Mock())
        setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)
        return handler

    def run_bridge(self, path, forward):
        route = marked_route(path)
        clock = Clock()
        arrival = clock.now
        clock.now += retry.HARD_PREOUTPUT_SECONDS - ARRIVAL_LEFT
        real = retry._default_budget

        def clocked(config, name, *, arrival_started=None):
            budget = real(config, name, arrival_started=arrival_started)
            if budget is not None:
                budget.clock, budget.rand, budget.started = clock, (lambda: 0.0), clock()
            return budget

        handler = self.bridge_handler()
        with mock.patch.object(retry, "RETRY_WINDOW_SECONDS", 420.0), \
                mock.patch.object(retry, "_default_budget", clocked), \
                retry.request_scope(route.provider_config, route.provider, arrival_started=arrival):
            forward(handler, route)
        return handler

    def assert_released(self):
        self.assertEqual(1, len(self.accepted), self.accepted)
        open_timeout, read_timeout = self.accepted[0]
        self.assertAlmostEqual(ARRIVAL_LEFT, open_timeout, places=6)  # the pre-acceptance clip stays
        self.assertEqual(PROVIDER_TIMEOUT, read_timeout)  # the started response reads under the caller's timeout

    def test_chat_bridge_stream_survives_a_gap_longer_than_the_arrival_remainder(self):
        base = self.serve(CHAT_HEAD, CHAT_TAIL)
        route = OpenAIChatPassthrough(OpenAIChatPassthroughPorts(
            normalize_model=lambda p, c, m: m, normalize_request=lambda p, c, b: b,
            upstream_base=lambda p, c: base, join_url=lambda b, p: b + p, headers=lambda *a: {},
            urlopen=self.urlopen, timeout_seconds=lambda c: PROVIDER_TIMEOUT,
            copy_response_headers=self.copy_headers))
        handler = self.run_bridge("/v1/chat/completions", lambda h, r: route.forward(
            h, r.provider, r.provider_config, {"model": "m", "messages": [], "stream": True}))
        self.assertEqual(CHAT_HEAD + CHAT_TAIL, handler.wfile.getvalue())
        self.assert_released()

    def test_responses_bridge_stream_survives_a_gap_longer_than_the_arrival_remainder(self):
        base = self.serve(RESP_HEAD, RESP_TAIL)
        route = ProviderResponsesPassthrough(ProviderResponsesPassthroughPorts(
            project_channel_context=lambda body: (body, {}), begin_channel_delivery=mock.Mock(),
            normalize_model=lambda p, c, m: m, normalize_request=lambda p, c, b: b,
            upstream_base=lambda p, c: base, join_url=lambda b, p: b + p, headers=lambda *a: {},
            urlopen=self.urlopen, timeout_seconds=lambda c: PROVIDER_TIMEOUT,
            copy_response_headers=self.copy_headers))
        handler = self.run_bridge("/v1/responses", lambda h, r: route.forward(
            h, r.provider, r.provider_config, {"model": "m", "input": [], "stream": True}))
        self.assertEqual(RESP_HEAD + RESP_TAIL, handler.wfile.getvalue())
        self.assert_released()

    def test_chat_bridge_error_body_survives_a_gap_longer_than_the_arrival_remainder(self):
        base = self.serve(ERROR_HEAD, ERROR_TAIL, status=503, ctype="application/json")
        route = OpenAIChatPassthrough(OpenAIChatPassthroughPorts(
            normalize_model=lambda p, c, m: m, normalize_request=lambda p, c, b: b,
            upstream_base=lambda p, c: base, join_url=lambda b, p: b + p, headers=lambda *a: {},
            urlopen=self.urlopen, timeout_seconds=lambda c: PROVIDER_TIMEOUT,
            copy_response_headers=lambda *a: None))
        handler = self.run_bridge("/v1/chat/completions", lambda h, r: route.forward(
            h, r.provider, r.provider_config, {"model": "m", "messages": [], "stream": True}))
        handler.send_response.assert_called_once_with(503)
        self.assertEqual(ERROR_HEAD + ERROR_TAIL, handler.wfile.getvalue())  # unchanged and complete
        self.assertEqual(1, len(self.hits))  # no retry, no replay
        self.assertAlmostEqual(ARRIVAL_LEFT, self.open_timeout, places=6)  # the pre-acceptance clip stays

    def test_responses_bridge_error_body_survives_a_gap_longer_than_the_arrival_remainder(self):
        base = self.serve(ERROR_HEAD, ERROR_TAIL, status=503, ctype="application/json")
        route = ProviderResponsesPassthrough(ProviderResponsesPassthroughPorts(
            project_channel_context=lambda body: (body, {}), begin_channel_delivery=mock.Mock(),
            normalize_model=lambda p, c, m: m, normalize_request=lambda p, c, b: b,
            upstream_base=lambda p, c: base, join_url=lambda b, p: b + p, headers=lambda *a: {},
            urlopen=self.urlopen, timeout_seconds=lambda c: PROVIDER_TIMEOUT,
            copy_response_headers=lambda *a: None))
        raised = []

        def forward(h, r):
            try:
                route.forward(h, r.provider, r.provider_config, {"model": "m", "input": [], "stream": True})
            except urllib.error.HTTPError as exc:  # the router writes this error to the client
                raised.append((exc.code, exc.read()))

        self.run_bridge("/v1/responses", forward)
        self.assertEqual([(503, ERROR_HEAD + ERROR_TAIL)], raised)  # unchanged and complete
        self.assertEqual(1, len(self.hits))  # no retry, no replay
        self.assertAlmostEqual(ARRIVAL_LEFT, self.open_timeout, places=6)


if __name__ == "__main__":
    unittest.main()
