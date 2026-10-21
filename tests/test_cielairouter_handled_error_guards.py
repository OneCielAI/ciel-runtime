"""Regression: core-adjudicated CielAiRouter failures must not re-enter legacy loops.

The transient CielAiRouter transport marks every structured rejection it has
already adjudicated (retried, terminal, or non-retryable) with
``_ciel_transient_retry_handled``/``_ciel_transient_retry_exhausted``.  Before
the guards under test, the legacy HTTP 429 key-rotation and rate-limit backoff
branches ignored that marker and re-entered the stopped transient opener, which
raised ``RuntimeError("transient retry operation is already committed or
stopped")`` instead of returning the provider's own status and body.

Terminal verdicts (malformed, authentication, usage) keep one upstream attempt.
A strict structured rate-limit verdict is the one replay the transient owner
still performs, inside its own shared budget; after that budget is exhausted the
legacy branches must still not start a second, independent retry loop.

These tests drive the three public entrypoints of
``ciel_runtime_support.upstream_retry`` through the shared
``test_upstream_retry`` service harness.  They assert the caller-visible
contract: the expected upstream attempt count, the original status and body, the
handled marker surviving ``_preserved_http_error``, and untouched key rotation,
backoff, sleep, and retry activity.
"""

import contextlib
import dataclasses
import http.server
import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest import mock

# The exact-file/CI runner owns process isolation. Test discovery must preserve
# its isolation marker and temporary paths for other lifecycle tests.
from ciel_runtime_support import transient_retry
from ciel_runtime_support import upstream_retry as retry
from test_upstream_retry import ENTRYPOINTS, Response, invoke, services

BASE_URL = "https://provider.test"

# A structured rate-limit body whose type/message are not terminal-usage text.
STRUCTURED_429 = b'{"error":{"type":"rate_limit_error","message":"Rate limit reached for requests"}}'
# Missing the error ``type``: the transient layer cannot read a retryable
# verdict, so the 503 is adjudicated as terminal and marked handled.
MALFORMED_503 = b'{"error":{"message":"gateway unavailable"}}'
# Same missing verdict for a 429: no replay, even though the status is a rate limit.
MALFORMED_429 = b'{"error":{"message":"too many requests"}}'
AUTH_401 = b'{"error":{"type":"authentication_error","message":"invalid x-api-key"}}'
# Contains "reached your usage limit": terminal account action, never replayed.
USAGE_429 = b'{"error":{"type":"usage_limit_error","message":"You have reached your usage limit for this session"}}'


def failing(status, body):
    """Return a dispatch that rejects every attempt with one provider error."""

    def respond(req, _attempt):
        raise urllib.error.HTTPError(
            req.full_url,
            status,
            "rejected",
            {"content-type": "application/json"},
            io.BytesIO(body),
        )

    return respond


def build_services(dispatch, *, key_count=2, activity=None, log=None):
    """Shared harness services with an isolated cooldown recorder.

    ``test_upstream_retry.services`` reuses one ``noop`` mock for several
    callbacks (including the header learner), so its cooldown slot cannot be
    counted directly.  Replace only that slot so cooldown assertions are exact.
    """

    base = services(dispatch, key_count=key_count, activity=activity, log=log)
    cooldown = mock.Mock()
    return (
        dataclasses.replace(
            base,
            keys=dataclasses.replace(base.keys, register_api_key_cooldown=cooldown),
        ),
        cooldown,
    )


def run_entrypoint(
    fn,
    respond,
    *,
    gateway_retries=3,
    key_count=2,
    provider="cielairouter",
    outer_scope=False,
):
    """Invoke one public entrypoint and capture everything the caller can see.

    ``outer_scope`` mirrors the generation dispatcher, which enters
    ``request_scope`` around a whole generation.  In that shape a legacy branch
    that reopens after the transient budget stopped raises the reported
    ``RuntimeError`` instead of merely issuing another request.
    """

    calls = []

    def dispatch(req, **_kwargs):
        calls.append(req)
        return respond(req, len(calls))

    activity, log = mock.Mock(), mock.Mock()
    svc, cooldown = build_services(
        dispatch, key_count=key_count, activity=activity, log=log
    )
    config = {"base_url": BASE_URL}
    if gateway_retries is not None:
        config["gateway_retries"] = gateway_retries
    raised, result = None, None
    scope = (
        transient_retry.request_scope(config, provider)
        if outer_scope
        else contextlib.nullcontext()
    )
    with scope:
        with mock.patch.object(retry.time, "sleep") as sleep:
            try:
                result = invoke(fn, svc, config, provider)
            except Exception as error:  # noqa: BLE001 - the raised shape is asserted
                raised = error
    return SimpleNamespace(
        calls=calls,
        activity=activity,
        log=log,
        sleep=sleep,
        services=svc,
        cooldown=cooldown,
        raised=raised,
        result=result,
    )


def decoded(result, fn):
    """Normalize an entrypoint success (JSON dict or readable response)."""

    if fn is retry.post_json_with_rate_retry:
        return result
    return json.loads(result.read())


def retry_events(run):
    return [call for call in run.activity.call_args_list if call.args[0] == "retry"]


def retry_waits(run):
    """The scheduled retry waits; real sockets also poll in 5 ms slices."""

    return [
        call.args[0]
        for call in run.sleep.call_args_list
        if call.args[0] >= transient_retry.MIN_WAIT_SECONDS
    ]


class HandledCielAiRouterErrorTests(unittest.TestCase):
    def test_adjudicated_errors_get_one_attempt_and_keep_status_and_body(self):
        cases = {
            "malformed429": (429, MALFORMED_429),
            "malformed503": (503, MALFORMED_503),
            "auth401": (401, AUTH_401),
            "usage429": (429, USAGE_429),
        }
        # 3 is the explicit contract; unset uses the transient default of 3
        # while the legacy configured_gateway_retries stays 0.
        for fn in ENTRYPOINTS:
            for retries in (3, None):
                for case, (status, body) in cases.items():
                    for outer_scope in (False, True):
                        with self.subTest(
                            entrypoint=fn.__name__,
                            gateway_retries=retries,
                            case=case,
                            outer_scope=outer_scope,
                        ):
                            run = run_entrypoint(
                                fn,
                                failing(status, body),
                                gateway_retries=retries,
                                key_count=2,
                                outer_scope=outer_scope,
                            )
                            self.assertEqual(
                                1, len(run.calls), "handled error was replayed upstream"
                            )
                            self.assertIsInstance(
                                run.raised,
                                urllib.error.HTTPError,
                                "a handled provider rejection must reach the caller "
                                "as its own HTTP status, not a retry re-entry RuntimeError",
                            )
                            error = run.raised
                            self.assertEqual(status, error.code)
                            self.assertEqual(body, error.read())
                            self.assertEqual(body, getattr(error, "ciel_runtime_body", None))
                            self.assertTrue(
                                retry.retry_handled(error),
                                "the transient handled marker must survive _preserved_http_error",
                            )
                            run.sleep.assert_not_called()
                            run.services.keys.provider_headers.assert_not_called()
                            run.services.rate_limit.register_backoff.assert_not_called()
                            self.assertEqual([], retry_events(run))
                            if status == 429:
                                # A cooldown is recorded, but it must not unlock rotation.
                                run.cooldown.assert_called_once()

    def test_structured_rate_limit_spends_the_shared_budget(self):
        """A strict rate-limit verdict is replayed by the transient owner only.

        Three retries admit four upstream attempts.  The legacy key-axis and
        backoff branches must stay out of the loop (the marker on the final
        error forbids re-entry), and the caller still receives the provider's
        own status and body after the shared ceiling is exhausted.
        """

        for fn in ENTRYPOINTS:
            for retries in (3, None):
                for outer_scope in (False, True):
                    with self.subTest(
                        entrypoint=fn.__name__,
                        gateway_retries=retries,
                        outer_scope=outer_scope,
                    ):
                        run = run_entrypoint(
                            fn,
                            failing(429, STRUCTURED_429),
                            gateway_retries=retries,
                            key_count=2,
                            outer_scope=outer_scope,
                        )
                        self.assertEqual(
                            4,
                            len(run.calls),
                            "3 retries must admit exactly 4 upstream attempts",
                        )
                        self.assertIsInstance(run.raised, urllib.error.HTTPError)
                        error = run.raised
                        self.assertEqual(429, error.code)
                        self.assertEqual(STRUCTURED_429, error.read())
                        self.assertEqual(
                            STRUCTURED_429, getattr(error, "ciel_runtime_body", None)
                        )
                        self.assertTrue(
                            retry.retry_handled(error),
                            "the exhausted marker must survive _preserved_http_error",
                        )
                        self.assertEqual(
                            [1.0, 2.0, 4.0],
                            retry_waits(run),
                            "the retries must use the shared bounded wait schedule",
                        )
                        run.services.keys.provider_headers.assert_not_called()
                        run.services.rate_limit.register_backoff.assert_not_called()
                        self.assertEqual([], retry_events(run))
                        run.cooldown.assert_called_once()

    def test_generic_provider_429_key_rotation_is_unchanged(self):
        def respond(req, attempt):
            if attempt > 1:
                return Response()
            raise urllib.error.HTTPError(
                req.full_url,
                429,
                "rejected",
                {"content-type": "application/json"},
                io.BytesIO(STRUCTURED_429),
            )

        for fn in ENTRYPOINTS:
            with self.subTest(entrypoint=fn.__name__):
                run = run_entrypoint(
                    fn, respond, gateway_retries=3, key_count=2, provider="other"
                )
                self.assertIsNone(run.raised)
                self.assertEqual(2, len(run.calls))
                run.sleep.assert_not_called()
                run.services.keys.provider_headers.assert_called_once()
                self.assertEqual("Bearer next", run.calls[1].get_header("Authorization"))
                run.services.rate_limit.register_backoff.assert_not_called()
                run.cooldown.assert_called_once()
                self.assertEqual({"id": "ok"}, decoded(run.result, fn))

    def test_generic_provider_429_backoff_is_unchanged(self):
        def respond(req, attempt):
            if attempt > 1:
                return Response()
            raise urllib.error.HTTPError(
                req.full_url,
                429,
                "rejected",
                {"content-type": "application/json"},
                io.BytesIO(STRUCTURED_429),
            )

        for fn in ENTRYPOINTS:
            with self.subTest(entrypoint=fn.__name__):
                run = run_entrypoint(
                    fn, respond, gateway_retries=3, key_count=1, provider="other"
                )
                self.assertIsNone(run.raised)
                self.assertEqual(2, len(run.calls))
                run.services.rate_limit.register_backoff.assert_called_once()
                run.sleep.assert_called_once_with(0)
                self.assertEqual({"id": "ok"}, decoded(run.result, fn))

    def test_explicit_zero_retries_single_key_makes_one_attempt(self):
        cases = {
            "structured429": (429, STRUCTURED_429),
            "malformed503": (503, MALFORMED_503),
            "auth401": (401, AUTH_401),
            "usage429": (429, USAGE_429),
        }
        for fn in ENTRYPOINTS:
            for case, (status, body) in cases.items():
                with self.subTest(entrypoint=fn.__name__, case=case):
                    run = run_entrypoint(
                        fn, failing(status, body), gateway_retries=0, key_count=1
                    )
                    self.assertEqual(1, len(run.calls))
                    self.assertIsNotNone(run.raised)
                    run.sleep.assert_not_called()
                    run.services.keys.provider_headers.assert_not_called()
                    run.services.rate_limit.register_backoff.assert_not_called()
                    self.assertEqual([], retry_events(run))
                    if status < 500:
                        # 4xx keeps its status and body without any retry budget.
                        self.assertIsInstance(run.raised, urllib.error.HTTPError)
                        self.assertEqual(status, run.raised.code)
                        self.assertEqual(body, run.raised.read())

    def test_explicit_zero_retries_does_not_reissue_after_structured_429(self):
        """CielAiRouter's explicit zero disables even legacy key-axis replay.

        A disabled transient owner cannot mark an error handled. The legacy
        attempt count must therefore remain one instead of expanding to the
        configured API-key count. Other providers retain their rotation tests.
        """

        for fn in ENTRYPOINTS:
            with self.subTest(entrypoint=fn.__name__):
                run = run_entrypoint(
                    fn, failing(429, STRUCTURED_429), gateway_retries=0, key_count=2
                )
                self.assertEqual(
                    1,
                    len(run.calls),
                    "gateway_retries=0 still reissued a structured 429 across "
                    "API keys; the key-rotation axis ignores the explicit disable",
                )


class LoopbackHandledErrorTests(unittest.TestCase):
    """Real HTTP fixtures: the wire itself proves how often Ciel reissues."""

    def serve(self, respond):
        """Start a loopback provider whose body depends on the hit number."""

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("content-length") or 0))
                self.server.hits += 1
                status, body = respond(self.server.hits)
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.hits = 0
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def invoke_loopback(self, fn, server):
        base = f"http://127.0.0.1:{server.server_address[1]}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def dispatch(req, *, timeout, **_kwargs):
            return opener.open(req, timeout=timeout)

        activity, log = mock.Mock(), mock.Mock()
        svc, cooldown = build_services(
            dispatch, key_count=2, activity=activity, log=log
        )
        config = {"base_url": base, "gateway_retries": 3}
        raised, result = None, None
        with mock.patch.object(retry.time, "sleep") as sleep:
            try:
                result = invoke(fn, svc, config)
            except Exception as error:  # noqa: BLE001 - the raised shape is asserted
                raised = error
        return SimpleNamespace(
            services=svc,
            cooldown=cooldown,
            activity=activity,
            sleep=sleep,
            raised=raised,
            result=result,
        )

    def test_real_loopback_rate_limit_then_success_is_replayed_once(self):
        def respond(hit):
            return (429, STRUCTURED_429) if hit == 1 else (200, b'{"id":"ok"}')

        server = self.serve(respond)
        for fn in ENTRYPOINTS:
            with self.subTest(entrypoint=fn.__name__):
                server.hits = 0
                run = self.invoke_loopback(fn, server)
                self.assertIsNone(run.raised)
                self.assertEqual(2, server.hits)
                self.assertEqual({"id": "ok"}, decoded(run.result, fn))
                self.assertEqual([1.0], retry_waits(run))

    def test_real_loopback_persistent_rate_limit_exhausts_one_budget(self):
        body = STRUCTURED_429
        server = self.serve(lambda _hit: (429, body))
        for fn in ENTRYPOINTS:
            with self.subTest(entrypoint=fn.__name__):
                server.hits = 0
                run = self.invoke_loopback(fn, server)
                self.assertEqual(
                    4, server.hits, "3 retries must admit exactly 4 upstream attempts"
                )
                self.assertIsInstance(run.raised, urllib.error.HTTPError)
                error = run.raised
                self.assertEqual(429, error.code)
                self.assertEqual(body, error.read())
                self.assertEqual(body, getattr(error, "ciel_runtime_body", None))
                self.assertTrue(retry.retry_handled(error))
                self.assertEqual(
                    [1.0, 2.0, 4.0],
                    retry_waits(run),
                )
                run.services.keys.provider_headers.assert_not_called()
                run.services.rate_limit.register_backoff.assert_not_called()
                run.cooldown.assert_called_once()
                self.assertEqual(
                    [],
                    [call.args[0] for call in run.activity.call_args_list
                     if call.args[0] == "retry"],
                )

    def test_real_loopback_terminal_rate_limits_get_one_hit(self):
        for raw in (USAGE_429, MALFORMED_429):
            server = self.serve(lambda _hit, _body=raw: (429, _body))
            for fn in ENTRYPOINTS:
                with self.subTest(raw=raw[:40], entrypoint=fn.__name__):
                    server.hits = 0
                    run = self.invoke_loopback(fn, server)
                    self.assertEqual(1, server.hits)
                    self.assertIsInstance(run.raised, urllib.error.HTTPError)
                    error = run.raised
                    self.assertEqual(429, error.code)
                    self.assertEqual(raw, error.read())
                    self.assertTrue(retry.retry_handled(error))
                    self.assertEqual([], retry_waits(run))
                    run.services.keys.provider_headers.assert_not_called()
                    run.services.rate_limit.register_backoff.assert_not_called()
                    run.cooldown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
