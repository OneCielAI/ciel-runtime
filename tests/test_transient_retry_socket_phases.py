"""U1: one upstream open must not outlive the 540 s arrival bound across socket phases.

Lead ruling (lead-rulings-independent-review-sse-retry-20261113.md, U1):
``bounded_timeout`` clips each socket operation, not a whole opener call. Settle
by test whether one open can end after the bound when most of the clipped timeout
is spent in connect and the response headers then arrive late.

The connect phase is a socket fake: ``socket.socket.connect`` waits
``connect_delay`` seconds (as a black-holed SYN retransmit would) and then connects
for real to a loopback server, which delays its response headers. Real urllib,
real socket timeouts, real clock; the arrival is placed so only ~2 s of the bound
remain.

U1 result and ruling (option 1): the open goes through the production opener
``provider_network.provider_urlopen``, whose deadline-aware handler bounds the
whole open by the arrival deadline that transient_retry publishes.
"""
import socket
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from ciel_runtime_support import provider_network as net
from ciel_runtime_support import transient_retry as retry

LEFT = 2.0          # seconds of the arrival bound left when the open starts
CONNECT = 1.5       # slow connect, inside the clipped timeout
HEADERS = 1.8       # header delay, inside a fresh per-recv timeout
TOLERANCE = 0.35    # scheduler slack on a loaded machine


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


class SocketPhaseBoundTests(unittest.TestCase):
    def serve(self, header_delay):
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                time.sleep(header_delay)
                try:
                    body = b'{"id":"late"}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

        server = _Server(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(5)))
        return server.server_port

    def slow_connect(self, delay):
        # Patched at socket.connect so it applies to the stdlib create_connection
        # and to provider_network's per-address deadline connect alike.
        real = socket.socket.connect

        def connect(sock, address):
            # Spend part of the clipped timeout before the TCP connect completes.
            timeout = sock.gettimeout()
            if timeout is not None and timeout < delay:
                time.sleep(timeout)
                raise TimeoutError("timed out")
            time.sleep(delay)
            return real(sock, address)

        return mock.patch.object(socket.socket, "connect", connect)

    def test_one_open_ends_by_the_arrival_bound(self):
        port = self.serve(HEADERS)
        now = time.monotonic()
        budget = retry.TransientRetryBudget(retry.WINDOW_RETRY_CEILING, 420.0, window_mode=True,
                                            arrival_started=now - (retry.HARD_PREOUTPUT_SECONDS - LEFT))
        request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/messages", data=b"{}")
        deadline = budget.arrival_started + retry.HARD_PREOUTPUT_SECONDS
        outcome = {}

        def open_once():
            # The production opener (provider_network.provider_urlopen), as the
            # cielairouter transports call it; loopback bypasses any env proxy.
            return net.provider_urlopen(request, retry.bounded_timeout({}, "cielairouter", 300.0),
                                        "cielairouter", {}, lambda *_: None)

        no_proxy = mock.patch.dict("os.environ", {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
        with no_proxy, self.slow_connect(CONNECT), retry.request_scope({}, budget=budget):
            try:
                response = retry.open_with_transient_retry(
                    open_once, provider="cielairouter", config={}, log=lambda *_: None, sleep=lambda _: None)
                outcome["status"] = "answered"
                response.close()
            except BaseException as error:  # noqa: BLE001 - the ending kind is part of the evidence
                outcome["status"] = type(error).__name__
        ended = time.monotonic()
        overrun = ended - deadline
        print(f"U1 open ended {overrun:+.2f}s relative to the arrival bound; outcome={outcome['status']}")
        self.assertLessEqual(overrun, TOLERANCE,
                             f"one open ended {overrun:.2f}s after the 540 s arrival bound ({outcome['status']})")


if __name__ == "__main__":
    unittest.main()
