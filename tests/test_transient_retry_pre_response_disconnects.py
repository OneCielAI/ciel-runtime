"""R3: typed pre-response transport disconnects are retried like ConnectionResetError.

Lead ruling R3 (lead-rulings-independent-review-sse-retry-20261113.md): the live :9524
router.log at 14:02:20 local shows `upstream_direct_transport_error ... URLError:
<urlopen error [WinError 10053] ...>` on the main dispatch, inside urlopen, before any
response header, then a 502 with zero retries. Admit, raw and via URLError.reason:
ConnectionAbortedError (observed), BrokenPipeError during the request send and
ssl.SSLEOFError during connect/handshake. Same budget, window and arrival bound as
reset. Unchanged: read-time failures, string-only classification, exposed output.
"""
import io
import socket
import ssl
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from ciel_runtime_support import transient_retry as retry


class Response(io.BytesIO):
    def __init__(self):
        super().__init__(b'{"id":"ok"}')
        self.headers = {"content-type": "application/json"}
        self.status = 200


def aborted():
    return ConnectionAbortedError(10053, "An established connection was aborted by the software in your host machine")


ADMITTED = {
    "ConnectionAbortedError": aborted,
    "BrokenPipeError": lambda: BrokenPipeError(32, "Broken pipe"),
    "SSLEOFError": lambda: ssl.SSLEOFError(8, "EOF occurred in violation of protocol"),
}


class PreResponseDisconnectTests(unittest.TestCase):
    def open_twice(self, first):
        calls, logs = [], []

        def open_once():
            calls.append(1)
            if len(calls) == 1:
                raise first
            return Response()

        with retry.request_scope({}, "cielairouter") as budget:
            result = retry.open_with_transient_retry(open_once, provider="cielairouter", config={},
                                                     log=lambda *args: logs.append(args), sleep=lambda _: None)
            with result:
                body = result.read()
        return len(calls), body, budget, logs

    def test_each_admitted_class_raw_and_wrapped_retries_once_then_succeeds(self):
        for name, make in ADMITTED.items():
            for wrapped in (False, True):
                with self.subTest(error=name, wrapped=wrapped):
                    error = urllib.error.URLError(make()) if wrapped else make()
                    opens, body, budget, logs = self.open_twice(error)
                    self.assertEqual(2, opens)
                    self.assertEqual(b'{"id":"ok"}', body)
                    self.assertEqual(1, budget.attempts)
                    self.assertTrue(any("kind=transport_disconnect" in line for _, line in logs))

    def test_the_live_shape_urlerror_10053_is_retried(self):
        opens, body, _, _ = self.open_twice(urllib.error.URLError(aborted()))
        self.assertEqual((2, b'{"id":"ok"}'), (opens, body))

    def test_string_only_and_untyped_failures_stay_terminal(self):
        for error in (urllib.error.URLError("[WinError 10053] connection aborted"),
                      OSError("An established connection was aborted"),
                      RuntimeError("ConnectionAbortedError")):
            with self.subTest(error=repr(error)):
                calls = []

                def open_once():
                    calls.append(1)
                    raise error

                with retry.request_scope({}, "cielairouter"), self.assertRaises(type(error)):
                    retry.open_with_transient_retry(open_once, provider="cielairouter", config={},
                                                    log=lambda *_: None, sleep=lambda _: None)
                self.assertEqual(1, len(calls))

    def test_read_time_abort_after_acceptance_is_not_replayed(self):
        response = Response()
        response.read = mock.Mock(side_effect=aborted())
        opener = mock.Mock(return_value=response)
        with retry.request_scope({}, "cielairouter"):
            result = retry.open_with_transient_retry(opener, provider="cielairouter", config={},
                                                     log=lambda *_: None, sleep=lambda _: None)
            with self.assertRaises(ConnectionAbortedError):
                result.read()
        self.assertEqual(1, opener.call_count)

    def test_admitted_classes_share_the_attempt_ceiling(self):
        calls = []

        def open_once():
            calls.append(1)
            raise urllib.error.URLError(aborted())

        with retry.request_scope({"gateway_retries": 2}, "cielairouter"):
            with self.assertRaises(urllib.error.URLError) as caught:
                retry.open_with_transient_retry(open_once, provider="cielairouter", config={"gateway_retries": 2},
                                                log=lambda *_: None, sleep=lambda _: None)
        self.assertEqual(3, len(calls))
        self.assertTrue(retry.retry_handled(caught.exception))

    def test_other_providers_never_replay(self):
        opener = mock.Mock(side_effect=aborted())
        with self.assertRaises(ConnectionAbortedError):
            retry.open_with_transient_retry(opener, provider="other", config={"gateway_retries": 2},
                                            log=lambda *_: None, sleep=lambda _: None)
        self.assertEqual(1, opener.call_count)


def _read_request(conn: socket.socket) -> None:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(65536)
        if not chunk:
            return
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    length = next((int(line.split(b":", 1)[1]) for line in head.split(b"\r\n")
                   if line.lower().startswith(b"content-length:")), 0)
    while len(rest) < length:
        chunk = conn.recv(65536)
        if not chunk:
            return
        rest += chunk


class LoopbackAbortBeforeHeadersTests(unittest.TestCase):
    """Real sockets, in the style of 708ef03: the upstream drops the socket before any header.

    Measured on this Windows host (2026-11-13): a graceful close by the upstream while a
    ~2.1 MB request body (the live request was 2175173 bytes) is still being sent
    surfaces as ConnectionAbortedError, WinError 10053, the live :9524 class. 6 of 6
    probes; an RST close surfaces as ConnectionResetError (10054) instead.
    """

    BODY = b"x" * 2_175_173

    def test_abort_before_headers_then_one_success(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        hits = []

        def serve():
            for index in range(2):
                conn, _ = listener.accept()
                hits.append(index)
                with conn:
                    if index == 0:
                        conn.recv(4096)  # only the start of the request, no header sent back
                        conn.shutdown(socket.SHUT_RDWR)
                        continue
                    _read_request(conn)
                    body = b'{"id":"ok"}'
                    conn.sendall(b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: "
                                 + str(len(body)).encode() + b"\r\nconnection: close\r\n\r\n" + body)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.addCleanup(lambda: (listener.close(), thread.join(5)))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(f"http://127.0.0.1:{listener.getsockname()[1]}/v1/messages", data=self.BODY)
        failures = []

        def open_once():
            try:
                return opener.open(request, timeout=retry.bounded_timeout({}, "cielairouter", 5.0))
            except BaseException as error:
                failures.append(type(getattr(error, "reason", error)).__name__)
                raise

        with retry.request_scope({}, "cielairouter"):
            result = retry.open_with_transient_retry(open_once, provider="cielairouter", config={},
                                                     log=lambda *_: None, sleep=lambda _: None)
            with result:
                self.assertEqual(b'{"id":"ok"}', result.read())
        self.assertEqual([0, 1], hits)
        # Windows reports the peer's shutdown as ConnectionAbortedError; Linux
        # reports the request body write into it as BrokenPipeError (CI 2e799ad).
        self.assertEqual(1, len(failures))
        self.assertIn(failures[0], ("ConnectionAbortedError", "BrokenPipeError", "ConnectionResetError"))
        print("loopback first-open failure class:", failures[0])


if __name__ == "__main__":
    unittest.main()
