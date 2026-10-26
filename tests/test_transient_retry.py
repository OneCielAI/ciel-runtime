"""Isolated request ownership and explicit rejection policy."""

import io
import threading
import unittest
import urllib.error
from ciel_runtime_support import transient_retry as retry
from ciel_runtime_support.upstream_stream_io import UpstreamClientDisconnected

SCREENSHOT = (
    b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error",'
    b'"message":"service_unavailable: all targets were skipped by pre-dispatch filters"}}\n\n'
)
JSON_ERROR = b'{"error":{"type":"overloaded_error","message":"busy"}}'
# A strict structured rate-limit verdict: capacity pressure, not account action.
RATE_LIMIT_429 = b'{"error":{"type":"rate_limit_error","message":"Rate limit reached for requests"}}'
# Account-action 429s: replaying the same request cannot succeed.
USAGE_429 = b'{"error":{"type":"usage_limit_error","message":"You have reached your usage limit for this session"}}'
QUOTA_429 = b'{"error":{"type":"insufficient_quota","message":"quota exhausted"}}'
# Missing error ``type``: no structured verdict, so no replay.
MALFORMED_429 = b'{"error":{"message":"too many requests"}}'


class Response(io.BytesIO):
    def __init__(self, body=b"ok", sse=False):
        super().__init__(body)
        self.headers = {"Content-Type": "text/event-stream"} if sse else {}
        self.status = 200


def failure(status=503, payload=SCREENSHOT, headers=None):
    return urllib.error.HTTPError(
        "http://upstream/v1/responses",
        status,
        "original reason",
        headers or {},
        io.BytesIO(payload),
    )


class Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


class TransientRetryTests(unittest.TestCase):
    def run_open(self, results, config=None, **kwargs):
        config = {"gateway_retries": 3} if config is None else config
        calls, logs, sleeps = [], [], []

        def open_once():
            calls.append(1)
            result = results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        response = retry.open_with_transient_retry(
            open_once,
            provider="cielairouter",
            config=config,
            log=lambda level, message: logs.append((level, message)),
            sleep=sleeps.append,
            **kwargs,
        )
        return response, calls, logs, sleeps, config

    def test_screenshot_retries_then_succeeds_and_closes_error(self):
        rejected = failure()
        original = rejected.fp
        response, calls, logs, sleeps, config = self.run_open([rejected, Response()])
        self.assertEqual(b"ok", response.read())
        self.assertEqual(2, len(calls))
        self.assertEqual([1.0], sleeps)
        self.assertIn("status=503", logs[0][1])
        self.assertTrue(original.closed)
        self.assertNotIn(retry.BUDGET_CONFIG_KEY, config)
        response.close()

    def test_explicit_zero_and_other_providers_untouched(self):
        for provider, config in [
            ("cielairouter", {"gateway_retries": 0}),
            ("anthropic", {}),
        ]:
            error = failure()
            with self.assertRaises(urllib.error.HTTPError) as caught:
                retry.open_with_transient_retry(
                    lambda: (_ for _ in ()).throw(error),
                    provider=provider,
                    config=config,
                    log=lambda *_: None,
                )
            self.assertIs(error, caught.exception)
            self.assertEqual(SCREENSHOT, caught.exception.read())
            caught.exception.close()

    def test_exhaustion_preserves_exact_status_headers_body_reason(self):
        headers = {"X-Original": "yes"}
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.run_open(
                [failure(), failure(503, SCREENSHOT, headers)], {"gateway_retries": 1}
            )
        error = caught.exception
        self.assertTrue(retry.retry_handled(error))
        self.assertTrue(error._ciel_transient_retry_exhausted)
        self.assertEqual(503, error.code)
        self.assertEqual("original reason", error.msg)
        self.assertEqual(headers, error.headers)
        self.assertEqual(SCREENSHOT, error.read())
        self.assertIn("retried this request 1 time", error._ciel_transient_retry_note)
        error.close()

    def test_structured_rate_limit_retries_then_succeeds(self):
        rejected = failure(429, RATE_LIMIT_429)
        original = rejected.fp
        response, calls, logs, sleeps, config = self.run_open([rejected, Response()])
        self.assertEqual(b"ok", response.read())
        self.assertEqual(2, len(calls))
        self.assertEqual([1.0], sleeps)
        self.assertIn("status=429", logs[0][1])
        self.assertTrue(original.closed)
        self.assertNotIn(retry.BUDGET_CONFIG_KEY, config)
        response.close()

    def test_persistent_rate_limit_exhausts_one_shared_budget(self):
        calls, sleeps = [], []
        errors = [failure(429, RATE_LIMIT_429) for _ in range(4)]

        def open_once():
            calls.append(1)
            raise errors[len(calls) - 1]

        with self.assertRaises(urllib.error.HTTPError) as caught:
            retry.open_with_transient_retry(
                open_once,
                provider="cielairouter",
                config={"gateway_retries": 3},
                log=lambda *_: None,
                sleep=sleeps.append,
            )
        error = caught.exception
        self.assertEqual(4, len(calls), "3 retries admit 4 upstream attempts")
        self.assertEqual([1.0, 2.0, 4.0], sleeps)
        self.assertTrue(retry.retry_handled(error))
        self.assertTrue(error._ciel_transient_retry_exhausted)
        self.assertEqual(429, error.code)
        self.assertEqual(RATE_LIMIT_429, error.read())
        self.assertIn("retried this request 3 time", error._ciel_transient_retry_note)
        error.close()

    def test_rate_limit_shares_the_same_attempt_ceiling(self):
        response, calls, _, sleeps, _ = self.run_open(
            [failure(503, SCREENSHOT), failure(429, RATE_LIMIT_429), Response()],
            {"gateway_retries": 2},
        )
        self.assertEqual(b"ok", response.read())
        self.assertEqual(3, len(calls))
        self.assertEqual([1.0, 2.0], sleeps)
        response.close()

    def test_terminal_rate_limit_verdicts_are_not_replayed(self):
        for raw in (USAGE_429, QUOTA_429, MALFORMED_429):
            with self.subTest(raw=raw[:80]):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.run_open([failure(429, raw)])
                self.assertTrue(retry.retry_handled(caught.exception))
                self.assertEqual(429, caught.exception.code)
                self.assertEqual(raw, caught.exception.read())
                caught.exception.close()

    def test_rate_limit_retry_after_is_bounded(self):
        budget = retry.TransientRetryBudget(3, 600)
        self.assertEqual(
            30.0,
            budget.next_wait(failure(429, RATE_LIMIT_429), RATE_LIMIT_429,
                             {"Retry-After": "120"}),
        )
        budget = retry.TransientRetryBudget(3, 600)
        self.assertEqual(
            0.25,
            budget.next_wait(failure(429, RATE_LIMIT_429), RATE_LIMIT_429,
                             {"Retry-After": "0"}),
        )

    def test_all_terminal_and_malformed_marked_without_retry(self):
        cases = [
            (401, SCREENSHOT),
            (400, SCREENSHOT),
            (429, SCREENSHOT),
            (503, b"broken"),
        ]
        for kind, message, code in [
            ("insufficient_quota", "busy", ""),
            ("authentication_error", "busy", ""),
            ("overloaded_error", "session usage limit", ""),
            ("overloaded_error", "busy", "billing_limit"),
        ]:
            import json

            cases.append(
                (
                    503,
                    json.dumps(
                        {"error": {"type": kind, "message": message, "code": code}}
                    ).encode(),
                )
            )
        for status, raw in cases:
            with self.subTest(raw=raw, status=status):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.run_open([failure(status, raw)])
                self.assertTrue(retry.retry_handled(caught.exception))
                self.assertEqual(raw, caught.exception.read())
                caught.exception.close()

    def test_strict_json_rejects_mixed_duplicate_nan_and_unknown(self):
        values = [
            b'{"error":{"type":"overloaded_error","message":"busy","x":NaN}}',
            b'{"error":{"type":"overloaded_error","message":"a","message":"b"}}',
            b'{"error":{"type":"overloaded_error","message":"busy"},"choices":[]}',
            b'{"error":{"type":"overloaded_error","message":"busy"},"delta":{}}',
            b'{"error":{"type":"overloaded_error","message":"busy"},"output":[]}',
            b'{"error":{"type":"overloaded_error","message":"busy"},"tool_calls":[]}',
            JSON_ERROR + b"junk",
            JSON_ERROR + b" " * retry.MAX_ERROR_BYTES,
            b"\xff",
            b"null",
        ]
        for raw in values:
            with self.subTest(raw=raw[:120]):
                self.assertIsNone(retry._error_verdict(raw))

    def test_structural_json_and_complete_sse_variants(self):
        for raw in [
            JSON_ERROR,
            SCREENSHOT,
            b"data: " + JSON_ERROR + b"\n\n",
            b": comment\r\nevent: error\r\ndata: " + JSON_ERROR + b"\r\n\r\n",
            b'data: {\ndata: "error":{"type":"overloaded_error","message":"busy"}}\n\n',
        ]:
            with self.subTest(raw=raw):
                self.assertIsNotNone(retry._error_verdict(raw))
        for raw in [
            SCREENSHOT.rstrip(),
            SCREENSHOT + SCREENSHOT,
            b"event: content\ndata: " + JSON_ERROR + b"\n\n",
            SCREENSHOT + b"data: output",
            b"event: error\n\ndata: " + JSON_ERROR + b"\n\n",
        ]:
            with self.subTest(raw=raw):
                self.assertIsNone(retry._error_verdict(raw))

    def test_scope_identity_no_config_mutation_or_cross_request_leak(self):
        config, identities = {"gateway_retries": 1}, []
        self.assertIsNone(retry.budget_for(config, "cielairouter"))
        for _ in range(2):
            with retry.request_scope(config) as budget:
                identities.append(budget.operation_id)
                self.assertIs(budget, retry.budget_for(dict(config), "cielairouter"))
                response, calls, *_ = self.run_open([failure(), Response()], config)
                self.assertEqual(2, len(calls))
                response.close()
        self.assertNotEqual(*identities)
        self.assertEqual({"gateway_retries": 1}, config)

    def test_same_request_cannot_reopen_after_error_or_exposure(self):
        config = {"gateway_retries": 1}
        for results in [[failure(), failure()], [Response()]]:
            with retry.request_scope(config):
                try:
                    response, *_ = self.run_open(results, config)
                    response.read(1)
                    response.close()
                except urllib.error.HTTPError as error:
                    error.close()
                with self.assertRaisesRegex(RuntimeError, "committed or stopped"):
                    self.run_open([Response()], config)

    def test_nested_owner_does_not_multiply_budget(self):
        config, calls = {"gateway_retries": 1}, []

        def inner():
            calls.append(1)
            raise failure()

        def outer():
            return retry.open_with_transient_retry(
                inner,
                provider="cielairouter",
                config=config,
                log=lambda *_: None,
                sleep=lambda _: None,
            )

        with self.assertRaises(urllib.error.HTTPError) as caught:
            retry.open_with_transient_retry(
                outer,
                provider="cielairouter",
                config=config,
                log=lambda *_: None,
                sleep=lambda _: None,
            )
        self.assertEqual(2, len(calls))
        caught.exception.close()

    def test_nested_scope_cannot_reset_active_owner(self):
        config = {}

        def malicious():
            with retry.request_scope(config):
                return Response()

        with self.assertRaisesRegex(RuntimeError, "replace an active"):
            retry.open_with_transient_retry(
                malicious, provider="cielairouter", config=config, log=lambda *_: None
            )

    def test_thread_scopes_independent_on_same_config(self):
        config, ids, errors = {}, [], []
        barrier = threading.Barrier(2)

        def run():
            try:
                with retry.request_scope(config) as budget:
                    barrier.wait(2)
                    response, *_ = self.run_open([failure(), Response()], config)
                    response.close()
                    ids.append(budget.operation_id)
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
        self.assertEqual([], errors)
        self.assertEqual(2, len(set(ids)))

    def test_retry_after_finite_and_remaining_below_minimum(self):
        for hint in ["nan", "inf", "-inf", "1e999", "bad", "0", "-3", "0.02"]:
            budget = retry.TransientRetryBudget(3, 0.1, clock=Clock())
            self.assertIsNone(budget.next_wait(failure(), SCREENSHOT, {"Retry-After": hint}))
            self.assertEqual(0, budget.attempts)
        budget = retry.TransientRetryBudget(3, 60)
        self.assertEqual(
            0.25, budget.next_wait(failure(), SCREENSHOT, {"Retry-After": "0"})
        )

    def test_deadline_includes_attempt_and_wait(self):
        clock, config, calls = Clock(), {}, []
        budget = retry.TransientRetryBudget(3, 0.1, clock=clock)

        def open_once():
            calls.append(1)
            raise failure()

        with retry.request_scope(config, budget=budget):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                retry.open_with_transient_retry(
                    open_once,
                    provider="cielairouter",
                    config=config,
                    log=lambda *_: None,
                    sleep=clock.sleep,
                )
        self.assertEqual(1, len(calls))
        self.assertEqual(SCREENSHOT, caught.exception.read())
        caught.exception.close()

    def test_cancellation_before_open_and_after_wait(self):
        config, calls = {}, []
        with self.assertRaises(UpstreamClientDisconnected):
            retry.open_with_transient_retry(
                lambda: calls.append(1),
                provider="cielairouter",
                config=config,
                log=lambda *_: None,
                cancelled=lambda: True,
            )
        self.assertEqual([], calls)
        cancelled = [False]

        def opening():
            calls.append(1)
            raise failure()

        with self.assertRaises(UpstreamClientDisconnected):
            retry.open_with_transient_retry(
                opening,
                provider="cielairouter",
                config=config,
                log=lambda *_: None,
                sleep=lambda _: cancelled.__setitem__(0, True),
                cancelled=lambda: cancelled[0],
            )
        self.assertEqual([1], calls)

    def test_ambiguous_transport_is_never_retried(self):
        error = urllib.error.URLError("response lost")
        with self.assertRaises(urllib.error.URLError) as caught:
            self.run_open([error])
        self.assertIs(error, caught.exception)
        self.assertTrue(retry.retry_handled(error))

    def test_oversized_error_replays_all_bytes(self):
        raw = b"x" * (retry.MAX_ERROR_BYTES * 3)
        rejected = failure(payload=raw)
        original = rejected.fp
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.run_open([rejected])
        self.assertEqual(raw, caught.exception.read())
        caught.exception.close()
        self.assertTrue(original.closed)

    def test_specialized_recovery_spends_same_budget(self):
        config = {"gateway_retries": 1}
        with retry.request_scope(config) as budget:
            budget.opens = 1  # Initial plain opener already ran outside this executor.
            self.assertTrue(retry.consume_external_retry(config, "cielairouter"))
            self.assertFalse(retry.consume_external_retry(config, "cielairouter"))
            with self.assertRaisesRegex(RuntimeError, "allowance exhausted"):
                self.run_open([failure()], config)
            self.assertEqual(1, budget.attempts)
            self.assertEqual(2, budget.opens)

    def test_configured_limit_public_contract(self):
        from decimal import Decimal

        for value in [
            True,
            False,
            2.0,
            2.5,
            float("inf"),
            float("nan"),
            [],
            {},
            Decimal(2),
            object(),
            "2.5",
            "inf",
        ]:
            with self.subTest(value=value):
                self.assertEqual(
                    3, retry.configured_retry_limit({"gateway_retries": value})
                )
        for value, expected in [
            (None, 3),
            (0, 0),
            ("0", 0),
            (" 2 ", 2),
            (5, 5),
            (-2, 0),
            ("99", 10),
        ]:
            self.assertEqual(
                expected, retry.configured_retry_limit({"gateway_retries": value})
            )
        self.assertEqual(10, retry.MAX_RETRIES)

    def test_sequential_unconsumed_reopens_share_allowance(self):
        config = {"gateway_retries": 1}
        with retry.request_scope(config) as budget:
            first, *_ = self.run_open([Response()], config)
            first.close()
            second, *_ = self.run_open([Response()], config)
            second.close()
            self.assertEqual(2, budget.opens)
            self.assertEqual(1, budget.attempts)
            with self.assertRaisesRegex(RuntimeError, "allowance exhausted"):
                self.run_open([Response()], config)


if __name__ == "__main__":
    unittest.main()
