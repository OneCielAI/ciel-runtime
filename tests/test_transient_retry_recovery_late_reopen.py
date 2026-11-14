"""R1A2-F1: a recovery child's late pre-output SSE reopen stays the child's.

continue-work-backend's finding on transient_retry a429a609 (evidence
work-parallel-open-items/evidence/r1a2-relay-probe.py/.out). claude_router opens
the recovery inside ``with request_scope(recovery_config, provider)`` and reads
the response after that scope has exited (claude_router.py:609-616, :647-654,
then :655-707). A retryable error frame that arrives after the open-time
preflight is retried by ``_PreOutputSSE`` during that read. It must still:
- run under the recovery child: the socket timeout and the open deadline are
  clipped to the request's arrival bound, never the raw provider timeout;
- charge its retry to the parent request (R1 amendment 2, condition 1).

Real loopback upstream, real urllib, no sleeps (the wait is recorded).
"""
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ciel_runtime_support import transient_retry as retry

NL = bytes([10])
LATE_ERROR = NL.join([b"event: error", b'data: {"type":"error","error":{"type":"api_error",'
                      b'"message":"Model timed out (HTTP 504)"}}', b"", b""])
START = NL.join([b"event: message_start", b'data: {"type":"message_start","message":{"id":"m"}}', b"", b""])
PROVIDER_TIMEOUT = 300.0
ARRIVAL_AGO = 500.0   # 40 s of the 540 s arrival bound left
LATE = 0.4            # later than PREFLIGHT_SECONDS: the error is read after the open returned


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


class RecoveryLateReopenTests(unittest.TestCase):
    def setUp(self):
        served = self.served = []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *_):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("content-length") or 0))
                index = len(served)
                served.append(index)
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                self.wfile.flush()
                if index == 0:
                    time.sleep(LATE)
                    self.wfile.write(LATE_ERROR)
                else:
                    self.wfile.write(START)
                self.wfile.flush()

        server = _Server(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(5)))
        self.url = f"http://127.0.0.1:{server.server_port}/v1/messages"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.opener = opener

    def recover(self, read_inside_scope):
        recovery = {"gateway_retries": 0}
        opens, waits = [], []

        def open_once():
            timeout = retry.bounded_timeout(recovery, "cielairouter", PROVIDER_TIMEOUT)
            deadline = retry.open_deadline(recovery, "cielairouter")
            opens.append((timeout, None if deadline is None else deadline - time.monotonic()))
            return self.opener.open(urllib.request.Request(self.url, data=b"{}", method="POST"), timeout=timeout)

        arrival = time.monotonic() - ARRIVAL_AGO
        with retry.request_scope({}, "cielairouter", arrival_started=arrival) as parent:
            with retry.request_scope(recovery, "cielairouter") as child:
                self.assertIs(parent, child.recovery_parent)
                response = retry.open_with_transient_retry(
                    open_once, provider="cielairouter", config=recovery,
                    log=lambda *_: None, sleep=waits.append)
                if read_inside_scope:
                    body = response.read()
            if not read_inside_scope:
                body = response.read()  # claude_router's shape: read after the scope exits
            response.close()
        return parent, child, opens, waits, body

    def assert_late_reopen_belongs_to_the_child(self, read_inside_scope):
        parent, child, opens, waits, body = self.recover(read_inside_scope)
        self.assertIn(b"message_start", body)
        self.assertEqual(2, len(opens), "the open, then one late-SSE reopen")
        self.assertEqual(1, len(waits))
        for timeout, deadline_left in opens:
            self.assertLessEqual(timeout, retry.HARD_PREOUTPUT_SECONDS - ARRIVAL_AGO + 1e-6,
                                 f"socket timeout {timeout} escaped the arrival bound")
            self.assertIsNotNone(deadline_left, "the whole-open deadline is published (U1)")
            self.assertLessEqual(deadline_left, retry.HARD_PREOUTPUT_SECONDS - ARRIVAL_AGO + 1e-6)
        self.assertEqual(1, child.attempts)
        self.assertEqual(1, parent.attempts, "the late retry is charged to the request")

    def test_late_reopen_read_after_the_recovery_scope_stays_the_childs(self):
        self.assert_late_reopen_belongs_to_the_child(read_inside_scope=False)

    def test_late_reopen_read_inside_the_recovery_scope_control(self):
        self.assert_late_reopen_belongs_to_the_child(read_inside_scope=True)

    def test_write_back_is_not_double_counted(self):
        # Scope exit and the late retry both report; the parent counts each retry once.
        parent, child, _opens, _waits, _body = self.recover(read_inside_scope=True)
        self.assertEqual(child.attempts, parent.attempts)


if __name__ == "__main__":
    unittest.main()
