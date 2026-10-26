"""An accepted response is read under the caller's timeout, not the retry deadline.

Loopback fixtures only: the retry deadline is shortened to 0.1 s so a stream
that completes 0.3 s after acceptance proves the clipped socket timeout does
not outlive acceptance (the 60 s production deadline cut longer streams).
"""
import io
import json
import socket
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

from ciel_runtime_support import transient_retry as retry
from ciel_runtime_support.provider_network import provider_urlopen
from ciel_runtime_support.provider_responses_passthrough import (
    ProviderResponsesPassthrough,
    ProviderResponsesPassthroughPorts,
)

DELAY = 0.3


def _frame(kind, **extra):
    return f"event: {kind}\ndata: {json.dumps(dict(type=kind, **extra))}\n\n".encode()


CREATED = _frame("response.created", response={"id": "resp_fixture"})
COMPLETED = _frame("response.completed", response={"id": "resp_fixture", "status": "completed", "output": []})


class _SlowStream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests = 0

    def do_POST(self):
        type(self).requests += 1
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for index, payload in enumerate((CREATED, COMPLETED)):
                if index:
                    time.sleep(DELAY)
                self.wfile.write(b"%x\r\n%s\r\n" % (len(payload), payload))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except OSError:
            pass

    def log_message(self, *_args):
        pass


class AcceptedReadTimeoutTests(unittest.TestCase):
    def setUp(self):
        _SlowStream.requests = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowStream)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def test_open_with_transient_retry_restores_caller_timeout(self):
        config = {"gateway_retries": 3}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(self.base + "/v1/responses", data=b"{}")
        clipped = []

        def open_once():
            timeout = retry.bounded_timeout(config, "cielairouter", 1.0)
            clipped.append(timeout)
            return opener.open(request, timeout=timeout)

        with retry.request_scope(config, budget=retry.TransientRetryBudget(3, 0.1)):
            response = retry.open_with_transient_retry(open_once, provider="cielairouter",
                                                       config=config, log=lambda *_: None)
            with response:
                self.assertEqual(1.0, retry._socket(response).gettimeout())
                body = response.read()
        self.assertLess(clipped[0], 0.11)
        self.assertEqual(CREATED + COMPLETED, body)
        self.assertEqual(1, _SlowStream.requests)

    def test_none_requested_timeout_is_restored_and_invalid_keeps_the_clip(self):
        for requested, expected in ((None, None), ("bad", 0.1), (0, 0.1)):
            with self.subTest(requested=requested):
                budget = retry.TransientRetryBudget(3, 60)
                budget.requested_timeout = requested
                sock = socket.socket()
                try:
                    sock.settimeout(0.1)
                    response = SimpleNamespace(fp=SimpleNamespace(raw=SimpleNamespace(_sock=sock)))
                    retry.release_read_timeout(response, budget)
                    self.assertEqual(expected, sock.gettimeout())
                finally:
                    sock.close()
        self.assertIsNone(retry.release_read_timeout(None, retry.TransientRetryBudget(3, 60)))

    def _passthrough(self, logs):
        return ProviderResponsesPassthrough(ProviderResponsesPassthroughPorts(
            project_channel_context=lambda body: (body, {}),
            begin_channel_delivery=lambda *_: None,
            normalize_model=lambda _p, _c, model: model,
            normalize_request=lambda _p, _c, body: body,
            upstream_base=lambda _p, config: config["base_url"],
            join_url=lambda base, path: base + path,
            headers=lambda *_: {"Content-Type": "application/json"},
            urlopen=lambda req, *, timeout, provider, pcfg: provider_urlopen(
                req, timeout, provider, pcfg, lambda *_: None),
            timeout_seconds=lambda config: config["request_timeout"],
            copy_response_headers=lambda *_: None,
            log=lambda level, message: logs.append((level, message)),
        ))

    def test_cielairouter_responses_stream_outlives_the_retry_deadline(self):
        for provider in ("cielairouter", "generic"):
            with self.subTest(provider=provider):
                _SlowStream.requests = 0
                logs, statuses = [], []
                config = {"base_url": self.base, "gateway_retries": 3, "request_timeout": 1.0, "ip_family": "auto"}
                downstream = SimpleNamespace(headers={}, wfile=io.BytesIO(),
                                             send_response=statuses.append, end_headers=lambda: None)
                body = {"model": "fixture-model", "stream": True,
                        "input": [{"role": "user", "content": "synthetic read-timeout probe"}]}
                with retry.request_scope(config, provider, budget=retry.TransientRetryBudget(3, 0.1)):
                    self._passthrough(logs).forward(downstream, provider, config, body)
                self.assertEqual([200], statuses)
                self.assertEqual(CREATED + COMPLETED, downstream.wfile.getvalue())
                self.assertEqual(1, _SlowStream.requests)
                self.assertFalse([message for level, message in logs if "truncated" in message])


if __name__ == "__main__":
    unittest.main()
