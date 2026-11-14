"""O2-F1: a remote-bridge single open's arrival clip ends at acceptance.

lead-rulings-independent-review-sse-retry-20261113.md, "R2-O2-F1" and its 21:22Z
amendment (continue-retry owns the upstream_retry ``/v1/messages`` path):
- a production-marked bridge request (RemoteBridgeRoutingService.resolve sets
  REMOTE_BRIDGE_CONFIG_MARKER) makes one open, no replay;
- that open's socket timeout is clipped to what is left of the 540 s
  router-arrival bound (pre-acceptance clip stays);
- once the upstream answered with headers, success or HTTP error, the body is
  read under the caller's provider timeout, so a body gap longer than the
  arrival remainder but within the provider timeout completes unchanged.

Real loopback server, real provider_network.provider_urlopen (the production
opener, including the U1 deadline handler), real clock. All three upstream_retry
entrypoints share ``_open_cielairouter_transient``: post_json_with_rate_retry
(non-stream ``/v1/messages``), open_provider_request_with_key_retry (direct
stream/non-stream) and open_openai_stream_with_rate_retry.
"""
import http.server
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

from ciel_runtime_support import provider_network as net
from ciel_runtime_support import transient_retry as retry
from ciel_runtime_support import upstream_retry
from ciel_runtime_support.remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER, RemoteBridgeRoutingService
from test_upstream_retry import services

LEFT = 0.5              # seconds of the arrival bound left when the open starts
GAP = 1.0               # body gap after the headers: beyond LEFT, within the provider timeout
PROVIDER_TIMEOUT = 5.0
OK_BODY = b'{"id":"ok","type":"message"}'
ERROR_BODY = (b'{"type":"error","error":{"type":"invalid_request_error","message":"'
              + b"x" * 2048 + b'"}}')


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


def serve(test, status, body):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.flush()   # headers now: the open is accepted
                time.sleep(GAP)      # then the body arrives after the arrival remainder
                self.wfile.write(body)
            except OSError:
                pass

    server = _Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    test.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(5)))
    return f"http://127.0.0.1:{server.server_port}"


def marked_config(base_url):
    """The production route: the real resolver adds the bridge marker."""
    resolver = RemoteBridgeRoutingService(normalize_provider=lambda name: name,
                                          parse_bool=lambda value, default: bool(value), environ={})
    config = {"current_provider": "cielairouter",
              "providers": {"cielairouter": {"base_url": base_url, "current_model": "m"}}}
    route = resolver.resolve(config, {}, {"model": "m", "messages": []}, "/v1/messages")
    return route.provider_config


class BridgeReadTimeoutTests(unittest.TestCase):
    def run_entrypoint(self, fn, status, body):
        base = serve(self, status, body)
        pcfg = marked_config(base)
        self.assertIs(True, pcfg.get(REMOTE_BRIDGE_CONFIG_MARKER))
        clips, read_timeouts = [], []

        def recording(response):
            if response is None:
                return response
            real_read = response.read

            def read(*args, **kwargs):
                sock = retry._socket(response)
                read_timeouts.append(sock.gettimeout() if sock is not None else None)
                return real_read(*args, **kwargs)

            response.read = read
            return response

        def dispatch(req, *, timeout, provider, pcfg):
            clips.append(timeout)
            try:
                return recording(net.provider_urlopen(req, timeout, provider, pcfg, lambda *_: None))
            except urllib.error.HTTPError as error:
                recording(error.fp)
                raise

        arrival = time.monotonic() - (retry.HARD_PREOUTPUT_SECONDS - LEFT)
        no_proxy = mock.patch.dict("os.environ", {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
        outcome = {}
        with no_proxy, mock.patch.object(retry, "RETRY_WINDOW_SECONDS", 420.0), \
                retry.request_scope(pcfg, "cielairouter", arrival_started=arrival):
            try:
                result = fn(base + "/v1/messages", {"model": "m", "messages": []}, {"authorization": "Bearer test"},
                            PROVIDER_TIMEOUT, "cielairouter", pcfg, "m", services=services(dispatch))
                if fn is not upstream_retry.post_json_with_rate_retry:
                    with result:
                        result = json.loads(result.read())
                outcome["result"] = result
            except urllib.error.HTTPError as error:
                outcome["error"] = error
        self.assertEqual(1, len(clips), "one open, no replay")
        self.assertLessEqual(clips[0], LEFT + 1e-6, "pre-acceptance clip to the arrival remainder stays")
        self.assertGreater(clips[0], 0.0)
        return outcome, read_timeouts

    def assert_answer_completes(self, fn):
        outcome, read_timeouts = self.run_entrypoint(fn, 200, OK_BODY)
        self.assertEqual(json.loads(OK_BODY), outcome.get("result"))
        self.assertTrue(read_timeouts)
        self.assertEqual(PROVIDER_TIMEOUT, read_timeouts[0], "body read under the provider timeout")

    def assert_error_body_completes(self, fn):
        outcome, read_timeouts = self.run_entrypoint(fn, 400, ERROR_BODY)
        error = outcome.get("error")
        self.assertIsNotNone(error, outcome)
        self.assertEqual(400, error.code)
        self.assertEqual(ERROR_BODY, getattr(error, "ciel_runtime_body", None), "the complete error body, unchanged")
        self.assertTrue(read_timeouts)
        self.assertEqual(PROVIDER_TIMEOUT, read_timeouts[0], "error body read under the provider timeout")

    def test_post_json_answer_body_after_the_arrival_remainder_completes(self):
        self.assert_answer_completes(upstream_retry.post_json_with_rate_retry)

    def test_direct_request_answer_body_after_the_arrival_remainder_completes(self):
        self.assert_answer_completes(upstream_retry.open_provider_request_with_key_retry)

    def test_openai_stream_answer_body_after_the_arrival_remainder_completes(self):
        self.assert_answer_completes(upstream_retry.open_openai_stream_with_rate_retry)

    def test_post_json_error_body_after_the_arrival_remainder_completes(self):
        self.assert_error_body_completes(upstream_retry.post_json_with_rate_retry)

    def test_direct_request_error_body_after_the_arrival_remainder_completes(self):
        self.assert_error_body_completes(upstream_retry.open_provider_request_with_key_retry)

    def test_openai_stream_error_body_after_the_arrival_remainder_completes(self):
        self.assert_error_body_completes(upstream_retry.open_openai_stream_with_rate_retry)


if __name__ == "__main__":
    unittest.main()
