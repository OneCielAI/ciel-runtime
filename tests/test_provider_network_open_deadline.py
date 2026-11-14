"""U1: a deadline-aware provider_urlopen keeps one open within the arrival bound.

Lead ruling U1, option 1 (lead-rulings-independent-review-sse-retry-20261113.md):
1. active only while transient_retry publishes an open deadline (contextvar); with no
   deadline provider_urlopen calls exactly what it calls today;
2. TLS context, proxy handling, the gateway no-redirect handler, the IP-family policy
   and observed_gateway_open stay identical; only timeouts and the header-wait deadline
   change;
3. the timeout is recomputed from the remaining deadline before connect, after the TLS
   wrap and before getresponse; the header wait is bounded at the deadline by a socket
   shutdown timer that is cancelled when getresponse returns, before any body read;
4. a deadline hit raises a typed TimeoutError, so transient_retry ends with its honest
   preoutput_bound stop and opens nothing after the bound.
"""
import http.client
import socket
import ssl
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from ciel_runtime_support import provider_network as net
from ciel_runtime_support import transient_retry as retry

TOLERANCE = 0.35  # scheduler slack on a loaded machine


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


def serve(test, handler):
    server = _Server(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    test.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(5)))
    return server


def _captured_timers(timers):
    """threading.Timer stand-in that never fires by itself; the test runs it."""
    class CapturedTimer:
        def __init__(self, interval, function, *args, **kwargs):
            self.interval, self.function, self.daemon = interval, function, False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass  # a callback already past Timer.run's check is not stopped

    return mock.patch.object(net.threading, "Timer", CapturedTimer)


def delayed(header_delay, body=b'{"id":"ok"}', chunks=None, trickle=0.0, body_delay=0.0):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            time.sleep(header_delay)
            try:
                if chunks:
                    for chunk in chunks:  # trickled status line and headers
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        time.sleep(trickle)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body_delay:
                    self.wfile.flush()
                    time.sleep(body_delay)  # headers first; the body arrives later
                self.wfile.write(body)
            except OSError:
                pass
    return Handler


def slow_connect(delay):
    """Each TCP connect attempt takes ``delay`` s (a black-holed SYN retransmit).

    Patched at socket.connect, so it applies to the stdlib create_connection and
    to the deadline-aware per-address connect alike; it honours the socket's
    timeout the way a real connect does.
    """
    real = socket.socket.connect

    def connect(sock, address):
        timeout = sock.gettimeout()
        if timeout is not None and timeout < delay:
            time.sleep(timeout)
            raise TimeoutError("timed out")
        time.sleep(delay)
        return real(sock, address)

    return mock.patch.object(socket.socket, "connect", connect)


def no_proxy():
    return mock.patch.dict("os.environ", {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})


class OpenDeadlineTests(unittest.TestCase):
    def request(self, port):
        return urllib.request.Request(f"http://127.0.0.1:{port}/v1/messages", data=b"{}", method="POST")

    def test_u1_slow_connect_then_late_headers_ends_by_the_deadline(self):
        server = serve(self, delayed(1.8))
        deadline = time.monotonic() + 2.0
        with no_proxy(), slow_connect(1.5), net.open_deadline(deadline), self.assertRaises(TimeoutError):
            net.provider_urlopen(self.request(server.server_port), 2.0, "cielairouter", {}, lambda *_: None)
        self.assertLessEqual(time.monotonic() - deadline, TOLERANCE)

    def test_trickled_headers_end_at_the_deadline(self):
        parts = [b"HTTP/1.1 200 OK\r\n"] + [b"X-Trickle: " + str(i).encode() + b"\r\n" for i in range(40)]
        server = serve(self, delayed(0.0, chunks=parts, trickle=0.1))
        deadline = time.monotonic() + 1.0
        with no_proxy(), net.open_deadline(deadline), self.assertRaises(TimeoutError):
            net.provider_urlopen(self.request(server.server_port), 30.0, "cielairouter", {}, lambda *_: None)
        self.assertLessEqual(time.monotonic() - deadline, TOLERANCE)

    def test_headers_in_time_return_and_the_body_reads_after_the_deadline(self):
        # The header wait is bounded; the body is not (the timer is cancelled at getresponse).
        server = serve(self, delayed(0.1, body=b"x" * 10))
        deadline = time.monotonic() + 0.6
        with no_proxy(), net.open_deadline(deadline):
            response = net.provider_urlopen(self.request(server.server_port), 5.0, "cielairouter", {}, lambda *_: None)
        time.sleep(0.8)  # well past the deadline
        with response:
            self.assertEqual(b"x" * 10, response.read())

    def test_no_deadline_keeps_the_identical_call(self):
        calls = []
        with mock.patch.object(net.urllib.request, "urlopen", lambda req, timeout: calls.append(("urlopen", timeout)) or "r"), \
                mock.patch.object(net.urllib.request, "build_opener", side_effect=AssertionError("no opener build")):
            self.assertEqual("r", net.provider_urlopen(self.request(1), 7.0, "cielairouter", {}, lambda *_: None))
        self.assertEqual([("urlopen", 7.0)], calls)
        handlers = []
        with mock.patch.object(net.urllib.request, "build_opener",
                               lambda *h: handlers.extend(h) or mock.Mock(open=lambda req, timeout: "g")), \
                mock.patch("ciel_runtime_support.cielairouter_gateway_overlay.observed_gateway_open",
                           lambda request, config, open_request: open_request()):
            self.assertEqual("g", net.provider_urlopen(self.request(1), 7.0, "cielairouter",
                                                       {"ciel_gateway_route": "t"}, lambda *_: None))
        self.assertEqual([net._GatewayNoRedirect], [type(h) for h in handlers])

    def test_gateway_redirect_is_still_refused_under_a_deadline(self):
        class Redirect(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(307)
                self.send_header("Location", "http://127.0.0.1:1/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()

        server = serve(self, Redirect)
        with no_proxy(), net.open_deadline(time.monotonic() + 5.0), \
                mock.patch("ciel_runtime_support.cielairouter_gateway_overlay.observed_gateway_open",
                           lambda request, config, open_request: open_request()), \
                self.assertRaises(urllib.error.HTTPError) as caught:
            net.provider_urlopen(self.request(server.server_port), 5.0, "cielairouter",
                                 {"ciel_gateway_route": "t"}, lambda *_: None)
        self.assertEqual(307, caught.exception.code)
        self.assertIn("gateway redirect refused", str(caught.exception.msg))
        caught.exception.close()

    def test_trickling_https_proxy_connect_ends_as_typed_timeout(self):
        """CONNECT headers cannot trickle past the whole-open deadline."""
        class Proxy(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_CONNECT(self):
                payload = b"HTTP/1.1 200 Connection Established\r\nX-Proxy: trickle\r\n\r\n"
                try:
                    for byte in payload:
                        self.connection.sendall(bytes([byte]))
                        time.sleep(0.04)
                except OSError:
                    pass

        proxy = serve(self, Proxy)
        # Only the local proxy is contacted; deadline.invalid is only the CONNECT target.
        request = urllib.request.Request("https://deadline.invalid/v1/messages", data=b"{}", method="POST")
        environment = {"HTTPS_PROXY": f"http://127.0.0.1:{proxy.server_port}", "NO_PROXY": ""}
        deadline = time.monotonic() + 0.6
        with mock.patch.dict("os.environ", environment), net.open_deadline(deadline), \
                self.assertRaises(urllib.error.URLError) as caught:
            net.provider_urlopen(request, 5.0, "cielairouter", {}, lambda *_: None)
        self.assertIsInstance(caught.exception.reason, TimeoutError)
        self.assertLessEqual(time.monotonic() - deadline, TOLERANCE)

    def test_trickling_tls_handshake_ends_as_typed_timeout(self):
        """A TLS peer trickling one handshake record is covered by the same timer."""
        ready = threading.Event()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]

        def trickle():
            ready.set()
            conn, _ = listener.accept()
            with conn:
                try:
                    conn.recv(4096)  # ClientHello
                    conn.sendall(b"\x16\x03\x03\x00\x40")  # a 64-byte handshake record header
                    for _ in range(64):  # its body, one byte per 50 ms
                        conn.sendall(b"\x00")
                        time.sleep(0.05)
                except OSError:
                    pass

        thread = threading.Thread(target=trickle, daemon=True)
        thread.start()
        self.addCleanup(lambda: (listener.close(), thread.join(5)))
        ready.wait(2)
        request = urllib.request.Request(f"https://127.0.0.1:{port}/v1/messages", data=b"{}", method="POST")
        deadline = time.monotonic() + 0.6
        with no_proxy(), net.open_deadline(deadline), self.assertRaises(urllib.error.URLError) as caught:
            net.provider_urlopen(request, 5.0, "cielairouter", {}, lambda *_: None)
        self.assertIsInstance(caught.exception.reason, TimeoutError)
        self.assertLessEqual(time.monotonic() - deadline, TOLERANCE)

    def test_two_slow_addresses_share_one_deadline(self):
        attempts = []

        def two_addresses(host, port, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 1)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 2))]

        def slow_connect(sock, address):
            # A black-holed SYN: the attempt lasts exactly its socket timeout.
            attempts.append((address, time.monotonic(), sock.gettimeout()))
            time.sleep(sock.gettimeout())
            raise TimeoutError("black hole")

        deadline = time.monotonic() + 0.6
        request = urllib.request.Request("http://two-addresses.invalid/v1/messages", data=b"{}", method="POST")
        with no_proxy(), mock.patch.object(socket, "getaddrinfo", two_addresses), \
                mock.patch.object(socket.socket, "connect", slow_connect), net.open_deadline(deadline), \
                self.assertRaises(urllib.error.URLError) as caught:
            net.provider_urlopen(request, 5.0, "cielairouter", {}, lambda *_: None)
        ended = time.monotonic()
        self.assertIsInstance(caught.exception.reason, TimeoutError)
        self.assertTrue(attempts)
        for address, started, timeout in attempts:
            # Each address gets only what is left (clock granularity slack), never a fresh 0.6 s.
            self.assertLessEqual(timeout, deadline - started + 0.05, f"{address} got a fresh allowance")
        self.assertLessEqual(ended - deadline, TOLERANCE)

    def test_timer_callback_after_cancel_never_kills_a_returned_body(self):
        """Race order 1: getresponse returned and cancelled the timer, but the timer
        thread had already passed Timer.run's finished check, so its callback still
        runs (threading.Timer.cancel only sets ``finished``). The body stays intact."""
        timers = []
        server = serve(self, delayed(0.0, body=b"x" * 10, body_delay=0.4))
        with no_proxy(), _captured_timers(timers), net.open_deadline(time.monotonic() + 5.0):
            response = net.provider_urlopen(self.request(server.server_port), 5.0, "cielairouter", {}, lambda *_: None)
        self.assertTrue(timers, "the open armed a deadline timer")
        for timer in timers:
            timer.function()  # the callback that lost the race runs now
        with response:
            self.assertEqual(b"x" * 10, response.read())

    def test_timer_callback_before_disarm_raises_the_typed_timeout(self):
        """Race order 2: the callback wins just before getresponse returns: the open
        raises the typed timeout and never returns that response."""
        timers = []
        server = serve(self, delayed(0.0, body=b"x" * 10, body_delay=0.4))
        real = http.client.HTTPConnection.getresponse

        def getresponse_then_expire(connection):
            response = real(connection)
            for timer in timers:
                timer.function()
            return response

        with no_proxy(), _captured_timers(timers), net.open_deadline(time.monotonic() + 5.0), \
                mock.patch.object(http.client.HTTPConnection, "getresponse", getresponse_then_expire), \
                self.assertRaises(OSError) as caught:
            net.provider_urlopen(self.request(server.server_port), 5.0, "cielairouter", {}, lambda *_: None)
        reason = caught.exception.reason if isinstance(caught.exception, urllib.error.URLError) else caught.exception
        self.assertIsInstance(reason, TimeoutError)

    def test_tls_verification_is_unchanged_under_a_deadline(self):
        cert = Path(__file__).resolve().parent / "fixtures" / "selfsigned_localhost.pem"
        if not cert.exists():
            self.skipTest("self-signed fixture not present")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert))
        server = serve(self, delayed(0.0))
        server.socket = context.wrap_socket(server.socket, server_side=True)
        request = urllib.request.Request(f"https://localhost:{server.server_port}/v1/messages", data=b"{}", method="POST")
        with no_proxy(), net.open_deadline(time.monotonic() + 5.0), self.assertRaises(urllib.error.URLError) as caught:
            net.provider_urlopen(request, 5.0, "cielairouter", {}, lambda *_: None)
        self.assertIsInstance(caught.exception.reason, ssl.SSLCertVerificationError)

    def test_transient_retry_publishes_the_arrival_deadline_and_stops_honestly(self):
        server = serve(self, delayed(5.0))
        now = time.monotonic()
        budget = retry.TransientRetryBudget(retry.WINDOW_RETRY_CEILING, 420.0, window_mode=True,
                                            arrival_started=now - (retry.HARD_PREOUTPUT_SECONDS - 1.0))
        opens = []

        def open_once():
            opens.append(time.monotonic())
            return net.provider_urlopen(self.request(server.server_port),
                                        retry.bounded_timeout({}, "cielairouter", 300.0),
                                        "cielairouter", {}, lambda *_: None)

        with no_proxy(), retry.request_scope({}, budget=budget), self.assertRaises(Exception) as caught:
            retry.open_with_transient_retry(open_once, provider="cielairouter", config={},
                                            log=lambda *_: None, sleep=lambda _: None)
        ended = time.monotonic()
        self.assertLessEqual(ended - (budget.arrival_started + retry.HARD_PREOUTPUT_SECONDS), TOLERANCE)
        self.assertEqual(1, len(opens), "nothing opens after the bound")
        self.assertIn(budget.stop_reason, {"preoutput_bound", "deadline"})
        self.assertTrue(retry.retry_handled(caught.exception))


if __name__ == "__main__":
    unittest.main()
