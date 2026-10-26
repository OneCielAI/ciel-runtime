"""Attempt admission and socket timeout evidence; no strict DNS/wall-clock claim."""
import socket
import socketserver
import threading
import time
import unittest
import urllib.error
import urllib.request

from ciel_runtime_support import transient_retry as retry


class Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now


class TransientRetryDeadlineTests(unittest.TestCase):
    def test_active_timeout_clips_each_attempt_against_current_remainder(self):
        config, clock = {}, Clock()
        budget = retry.TransientRetryBudget(3, 60, clock=clock)
        with retry.request_scope(config, budget=budget):
            self.assertEqual(60, retry.bounded_timeout(config, "cielairouter", 1200))
            self.assertEqual(4, retry.bounded_timeout(config, "cielairouter", 4))
            clock.now += 55
            self.assertEqual(5, retry.bounded_timeout(config, "cielairouter", 1200))
            for value in [None, 0, -1, float("inf"), float("nan"), "bad", True, object()]:
                with self.subTest(timeout=value):
                    self.assertEqual(5, retry.bounded_timeout(config, "cielairouter", value))

    def test_other_provider_disabled_and_unscoped_preserve_timeout_exactly(self):
        sentinel = object()
        config = {}
        self.assertIs(sentinel, retry.bounded_timeout(config, "cielairouter", sentinel))
        with retry.request_scope(config):
            self.assertIs(sentinel, retry.bounded_timeout(config, "openai", sentinel))
            self.assertIs(sentinel, retry.bounded_timeout({"gateway_retries": 0}, "cielairouter", sentinel))

    def test_expired_helper_stops_before_network(self):
        config, clock = {}, Clock()
        budget = retry.TransientRetryBudget(3, 1, clock=clock)
        with retry.request_scope(config, budget=budget):
            clock.now += 1
            with self.assertRaises(TimeoutError) as caught:
                retry.bounded_timeout(config, "cielairouter", 1200)
        self.assertTrue(budget.stopped)
        self.assertTrue(retry.retry_handled(caught.exception))

    def test_expired_initial_attempt_never_calls_opener(self):
        config, clock, calls = {}, Clock(), []
        budget = retry.TransientRetryBudget(3, 1, clock=clock)
        with retry.request_scope(config, budget=budget):
            clock.now += 1
            with self.assertRaises(RuntimeError):
                retry.open_with_transient_retry(lambda: calls.append(1), provider="cielairouter",
                                                config=config, log=lambda *_: None)
        self.assertEqual([], calls)
        self.assertEqual(0, budget.opens)

    def test_expired_nested_attempt_never_calls_inner_opener(self):
        config, clock, calls = {}, Clock(), []
        budget = retry.TransientRetryBudget(3, 1, clock=clock)

        def outer():
            clock.now += 1
            return retry.open_with_transient_retry(lambda: calls.append(1), provider="cielairouter",
                                                    config=config, log=lambda *_: None)

        with retry.request_scope(config, budget=budget):
            with self.assertRaises(TimeoutError):
                retry.open_with_transient_retry(outer, provider="cielairouter", config=config,
                                                log=lambda *_: None)
        self.assertEqual([], calls)
        self.assertEqual(1, budget.opens)

    def test_stalled_loopback_headers_hit_bounded_socket_timeout_without_replay(self):
        release, accepted = threading.Event(), threading.Event()
        connections, calls, timeouts = [], [], []

        class Stall(socketserver.BaseRequestHandler):
            def handle(self):
                connections.append(1)
                self.request.settimeout(2)
                try:
                    self.request.recv(65536)
                    accepted.set()
                    release.wait(2)
                except OSError:
                    pass

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True

        server = Server(("127.0.0.1", 0), Stall)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/v1/responses", data=b"{}")
        config = {"gateway_retries": 3}
        budget = retry.TransientRetryBudget(3, 0.15)

        def open_once():
            calls.append(1)
            timeout = retry.bounded_timeout(config, "cielairouter", 1200)
            timeouts.append(timeout)
            return opener.open(request, timeout=timeout)

        started = time.monotonic()
        try:
            with retry.request_scope(config, budget=budget):
                with self.assertRaises((TimeoutError, urllib.error.URLError)) as caught:
                    retry.open_with_transient_retry(open_once, provider="cielairouter", config=config,
                                                    log=lambda *_: None)
            elapsed = time.monotonic() - started
            self.assertTrue(accepted.wait(0.5))
            self.assertEqual([1], calls)
            self.assertEqual([1], connections)
            self.assertGreater(timeouts[0], 0)
            self.assertLessEqual(timeouts[0], 0.15 + 1e-9)
            self.assertGreaterEqual(elapsed, 0.07)
            self.assertLess(elapsed, 1.5)
            error = caught.exception
            self.assertTrue(retry.retry_handled(error))
            self.assertTrue(isinstance(error, TimeoutError) or isinstance(error.reason, (TimeoutError, socket.timeout)))
            self.assertTrue(budget.stopped)
            self.assertEqual(0, budget.attempts)
            print(f"loopback_stall elapsed={elapsed:.3f}s socket_timeout={timeouts[0]:.3f}s opens={budget.opens} retries={budget.attempts} handled={retry.retry_handled(error)}")
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == "__main__":
    unittest.main()
