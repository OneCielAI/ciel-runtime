"""CielAiRouter typed open-timeouts spend the shared preacceptance allowance."""
import socket
import json
import ssl
import unittest
import urllib.error
from unittest import mock

from ciel_runtime_support import transient_retry as retry
from ciel_runtime_support.upstream_stream_io import UpstreamClientDisconnected
from test_transient_retry import Clock, Response, failure


class TimeoutRetryTests(unittest.TestCase):
    def invoke(self, opener, config=None, **kwargs):
        return retry.open_with_transient_retry(
            opener, provider=kwargs.pop("provider", "cielairouter"),
            config={"gateway_retries": 2} if config is None else config,
            log=kwargs.pop("log", lambda *_: None), sleep=kwargs.pop("sleep", lambda _: None), **kwargs)

    def test_typed_open_timeouts_retry_then_success(self):
        for error in (TimeoutError("The read operation timed out"), socket.timeout("timed out"),
                      urllib.error.URLError(TimeoutError("The read operation timed out"))):
            with self.subTest(error=error):
                opener = mock.Mock(side_effect=[error, Response()])
                logs, events = [], []
                result = self.invoke(opener, log=lambda *args: logs.append(args),
                                     publish=lambda **fields: events.append(fields))
                self.assertEqual(b"ok", result.read())
                self.assertEqual(2, opener.call_count)
                self.assertIn("kind=transport_timeout", logs[0][1])
                self.assertNotIn("status=504", logs[0][1])
                self.assertEqual("transport_timeout", events[0]["data"]["kind"])
                self.assertIsNone(events[0]["data"].get("status"))
                result.close()

    def test_mixed_http_and_timeout_spend_one_allowance(self):
        final = TimeoutError("last timeout")
        opener = mock.Mock(side_effect=[failure(), TimeoutError("second"), final, Response()])
        with retry.request_scope({"gateway_retries": 2}) as budget:
            with self.assertRaises(TimeoutError) as caught:
                self.invoke(opener)
            self.assertIs(final, caught.exception)
            self.assertEqual(3, opener.call_count)
            self.assertEqual(2, budget.attempts)
            self.assertTrue(budget.stopped)
            self.assertTrue(retry.retry_handled(final))

    def test_exact_timeout_http504_json_or_sse_success_exhausted_and_zero(self):
        raw = json.dumps({"type": "error", "error": {"type": "api_error",
            "message": "TimeoutError: The read operation timed out"}}).encode()
        for payload in (raw, b"event: error" + bytes([10]) + b"data: " + raw + bytes([10, 10])):
            for retries, success, expected in ((2, True, 2), (2, False, 3), (0, False, 1)):
                with self.subTest(sse=payload != raw, retries=retries, success=success):
                    calls = []
                    def open_once():
                        calls.append(1)
                        if success and len(calls) > 1:
                            return Response()
                        raise failure(504, payload)
                    if success:
                        result = self.invoke(open_once, {"gateway_retries": retries})
                        self.assertEqual(b"ok", result.read())
                        result.close()
                    else:
                        with self.assertRaises(urllib.error.HTTPError) as caught:
                            self.invoke(open_once, {"gateway_retries": retries})
                        self.assertEqual(504, caught.exception.code)
                        self.assertEqual(payload, caught.exception.read())
                        caught.exception.close()
                    self.assertEqual(expected, len(calls))

    def test_timeout_default_limit_and_cap(self):
        for config, expected in (({}, 4), ({"gateway_retries": 1}, 2), ({"gateway_retries": 99}, 11)):
            with self.subTest(config=config):
                def timeout():
                    raise TimeoutError("The read operation timed out")
                opener = mock.Mock(side_effect=timeout)
                with self.assertRaises(TimeoutError):
                    self.invoke(opener, config)
                self.assertEqual(expected, opener.call_count)

    def test_backoff_cancellation_prevents_reselection_and_second_open(self):
        cancelled = False
        def sleep(_):
            nonlocal cancelled
            cancelled = True
        opener = mock.Mock(side_effect=TimeoutError("timed out"))
        reselect = mock.Mock()
        with self.assertRaises(UpstreamClientDisconnected):
            self.invoke(opener, sleep=sleep, cancelled=lambda: cancelled, reselect=reselect)
        self.assertEqual(1, opener.call_count)
        reselect.assert_not_called()

    def test_explicit_zero_and_other_providers_preserve_timeout(self):
        for provider, config in (("cielairouter", {"gateway_retries": 0}), ("other", {"gateway_retries": 2})):
            error = TimeoutError("timed out")
            opener = mock.Mock(side_effect=error)
            with self.subTest(provider=provider), self.assertRaises(TimeoutError) as caught:
                self.invoke(opener, config, provider=provider)
            self.assertIs(error, caught.exception)
            self.assertEqual(1, opener.call_count)

    def test_other_transport_errors_still_do_not_replay(self):
        for error in (urllib.error.URLError("timed out"), ssl.SSLEOFError("EOF"),
                      ConnectionResetError("reset"), BrokenPipeError("broken"),
                      OSError("The read operation timed out")):
            opener = mock.Mock(side_effect=error)
            with self.subTest(error=error), self.assertRaises(type(error)) as caught:
                self.invoke(opener)
            self.assertIs(error, caught.exception)
            self.assertEqual(1, opener.call_count)

    def test_expired_open_timeout_does_not_restart_deadline(self):
        clock = Clock()
        budget = retry.TransientRetryBudget(2, 2, clock)
        error = TimeoutError("timed out")
        def open_once():
            clock.now += 3
            raise error
        opener = mock.Mock(side_effect=open_once)
        with retry.request_scope(budget=budget), self.assertRaises(TimeoutError) as caught:
            self.invoke(opener, sleep=clock.sleep)
        self.assertIs(error, caught.exception)
        self.assertEqual(1, opener.call_count)
        self.assertEqual(0, budget.attempts)

    def test_timeout_with_time_remaining_reselects_same_budget(self):
        clock = Clock()
        config = {"gateway_retries": 2}
        budget = retry.TransientRetryBudget(2, 10, clock)
        observed, timeouts = [], []
        def open_once():
            observed.append(retry.budget_for(config, "cielairouter"))
            timeouts.append(retry.bounded_timeout(config, "cielairouter", 30))
            if len(observed) == 1:
                clock.now += 2
                raise TimeoutError("timed out")
            return Response()
        reselect = mock.Mock()
        with retry.request_scope(config, budget=budget):
            result = self.invoke(open_once, config, sleep=clock.sleep, reselect=reselect)
        self.assertEqual([budget, budget], observed)
        self.assertEqual([10, 7], timeouts)
        reselect.assert_called_once_with()
        result.close()

    def test_cancellation_after_timeout_stops_before_backoff(self):
        cancelled = False
        def open_once():
            nonlocal cancelled
            cancelled = True
            raise TimeoutError("timed out")
        sleep, reselect = mock.Mock(), mock.Mock()
        with self.assertRaises(UpstreamClientDisconnected):
            self.invoke(open_once, sleep=sleep, cancelled=lambda: cancelled, reselect=reselect)
        sleep.assert_not_called()
        reselect.assert_not_called()

    def test_already_handled_timeout_cannot_open_a_second_allowance(self):
        error = TimeoutError("timed out")
        error._ciel_transient_retry_handled = True
        opener = mock.Mock(side_effect=error)
        with self.assertRaises(TimeoutError) as caught:
            self.invoke(opener)
        self.assertIs(error, caught.exception)
        self.assertEqual(1, opener.call_count)

    def test_accepted_body_timeout_is_not_reopened(self):
        response = Response()
        response.read = mock.Mock(side_effect=TimeoutError("accepted read timeout"))
        opener = mock.Mock(return_value=response)
        result = self.invoke(opener)
        with self.assertRaises(TimeoutError):
            result.read()
        self.assertEqual(1, opener.call_count)
        result.close()

    def test_preflight_timeout_is_not_reopened_after_response_acceptance(self):
        response = Response()
        opener = mock.Mock(return_value=response)
        with mock.patch.object(retry, "_preflight", side_effect=TimeoutError("accepted preflight timeout")):
            with self.assertRaises(TimeoutError):
                self.invoke(opener)
        self.assertEqual(1, opener.call_count)

    def test_exposed_owner_forbids_timeout_retry(self):
        budget = retry.TransientRetryBudget(2, 60)
        budget.exposed = True
        opener = mock.Mock(side_effect=TimeoutError("timed out"))
        with retry.request_scope(budget=budget), self.assertRaisesRegex(RuntimeError, "committed or stopped"):
            self.invoke(opener)
        opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
