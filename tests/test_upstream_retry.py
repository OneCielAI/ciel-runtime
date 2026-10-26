import http.client
import http.server
import io
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from unittest import mock

from ciel_runtime_support import upstream_retry as retry
from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.cielairouter_gateway_overlay import CielAiRouterGatewayOverlay, ROUTE_TOKEN_KEY
from ciel_runtime_support.cielairouter_gateways import CielAiRouterGatewayService, ProbeResult
from ciel_runtime_support.upstream_error_policy import configured_gateway_retries, retryable_exception as retryable_upstream_exception

ENTRYPOINTS = (retry.post_json_with_rate_retry, retry.open_provider_request_with_key_retry, retry.open_openai_stream_with_rate_retry)
CENTRAL, OLD, NEW = "https://central.test", "https://a.test", "https://b.test"
BODY = b'{"error":{"message":"gateway unavailable"}}'

class Response(io.BytesIO):
    def __init__(self):
        super().__init__(b'{"id":"ok"}')
        self.headers = {"content-type": "application/json"}
        self.status = 200

def services(open_request, *, key_count=1, activity=None, log=None):
    noop = mock.Mock()
    return retry.UpstreamRetryServices(
        policy=retry.UpstreamRetryPolicy(configured_gateway_retries, lambda *_: (False, 0), retryable_upstream_exception, noop, frozenset({500, 502, 503, 504, 529}), noop, lambda *_: 0),
        keys=retry.UpstreamRetryKeys(lambda h: h.get("authorization", ""), lambda *_: key_count, lambda *_: True, mock.Mock(return_value={"authorization": "Bearer next"}), lambda _p, _c, h: h, noop),
        rate_limit=retry.UpstreamRetryRateLimit(noop, log or noop, mock.Mock(return_value=0), activity or noop),
        http=retry.UpstreamRetryHttp(lambda *_: 1, open_request, noop, lambda *_: 30, lambda exc, raw: raw))

def invoke(fn, svc, pcfg, provider="cielairouter", **kwargs):
    return fn(pcfg["base_url"] + "/v1/chat/completions", {"messages": []}, {"authorization": "Bearer test"}, 1, provider, pcfg, "test-model", services=svc, **kwargs)

def gateway_fixture(old=OLD):
    now = 1000.0
    def stamp(t):
        return datetime.fromtimestamp(t, timezone.utc).isoformat()
    def row(node, endpoint):
        return {"id": node, "endpoint": endpoint, "status": "online", "health": "healthy", "lastSeenAt": stamp(now - 1), "availableUntil": stamp(now + 300)}
    selector = CielAiRouterGatewayService(clock=lambda: now, discovery=lambda *_: {"object": "list", "protocol_version": 1, "generated_at": stamp(now), "data": [row("a", old), row("b", NEW)]}, probe=lambda c, *_: ProbeResult(True, 0.001 if c.endpoint == old else 0.05, 200, c.node_id, "gateway", True))
    overlay = CielAiRouterGatewayOverlay(lambda: selector, clock=lambda: now)
    config = {"base_url": CENTRAL, "api_key": "test", "current_model": "test-model", "gateway_retries": 17}
    return selector, overlay, config

def failure(req, kind):
    if isinstance(kind, int):
        return urllib.error.HTTPError(req.full_url, kind, "unavailable", {"Retry-After": "3", "content-type": "application/json"}, io.BytesIO(BODY))
    if kind == "reset":
        return ConnectionResetError(104, "connection reset")
    if kind == "url_timeout":
        return urllib.error.URLError(TimeoutError("timed out"))
    return TimeoutError("timed out")

def outcome(error, activity, log):
    """The caller-visible shape of a final failed attempt, minus per-attempt counters."""

    status = getattr(error, "code", getattr(error, "upstream_status", None))
    cause = error.__cause__
    warn = [call.args[1].split(" ", 1)[0] for call in log.call_args_list if call.args[0] == "WARN"]
    errors = [{k: v for k, v in call.kwargs.items() if k != "message"} for call in activity.call_args_list if call.args[0] == "error"]
    retries = [call for call in activity.call_args_list if call.args[0] == "retry"]
    return type(error), type(cause) if cause is not None else None, status, warn, errors, len(retries)

def exhausted_baseline(fn, kind):
    """Run the generic final-attempt branch: no overlay route, no retry budget."""

    activity, log = mock.Mock(), mock.Mock()
    config = {"base_url": OLD, "gateway_retries": 0}
    def dispatch(req, **_):
        raise failure(req, kind)
    try:
        with mock.patch.object(retry.time, "sleep"):
            invoke(fn, services(dispatch, activity=activity, log=log), config)
    except Exception as error:  # noqa: BLE001 - the baseline records whatever the branch raises
        return outcome(error, activity, log)
    raise AssertionError("baseline request unexpectedly succeeded")

class ClosesWithoutResponse(http.server.BaseHTTPRequestHandler):
    """Read one complete request, then close the socket without a status line."""

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        self.server.hits += 1
        self.close_connection = True

    def log_message(self, *_args):
        pass

class GatewayRetryBoundaryTests(unittest.TestCase):
    def test_failed_gateway_dispatches_once_then_next_request_reselects(self):
        for fn in ENTRYPOINTS:
            for kind in ("reset", "timeout", "url_timeout", 500, 502, 503, 504, 529, 599):
                with self.subTest(entrypoint=fn.__name__, kind=kind):
                    selector, overlay, config = gateway_fixture()
                    pcfg = overlay.apply("cielairouter", config, session="same-session")
                    self.assertEqual(OLD, pcfg["base_url"])
                    calls, errors = [], []
                    def dispatch(req, *, timeout, provider, pcfg):
                        calls.append(req.full_url)
                        def open_request():
                            if req.full_url.startswith(NEW):
                                return Response()
                            error = failure(req, kind)
                            errors.append(error)
                            raise error
                        return overlay.observed_open(req, pcfg, open_request)
                    activity, log = mock.Mock(), mock.Mock()
                    svc = services(dispatch, activity=activity, log=log)
                    with mock.patch.object(retry.time, "sleep") as sleep, self.assertRaises(Exception) as raised:
                        invoke(fn, svc, pcfg)
                    self.assertEqual([OLD + "/v1/chat/completions"], calls)
                    sleep.assert_not_called()
                    # No same-call retry, but the failure leaves through the
                    # same final-attempt branch as an unrouted request does.
                    self.assertEqual(exhausted_baseline(fn, kind), outcome(raised.exception, activity, log))
                    self.assertFalse(HttpResponseAdapter.is_client_disconnect(raised.exception))
                    if isinstance(kind, int):
                        self.assertEqual(kind, getattr(raised.exception, "code", getattr(raised.exception, "upstream_status", None)))
                    else:
                        self.assertIs(type(raised.exception), RuntimeError)
                        self.assertIs(errors[0], raised.exception.__cause__)
                    self.assertIn(OLD, selector.status()[0]["cooldowns"])
                    next_pcfg = overlay.apply("cielairouter", config, session="same-session")
                    self.assertEqual(NEW, next_pcfg["base_url"])
                    result = invoke(fn, svc, next_pcfg)
                    if fn is not retry.post_json_with_rate_retry:
                        result = __import__("json").loads(result.read())
                    self.assertEqual({"id": "ok"}, result)
                    self.assertEqual([OLD + "/v1/chat/completions", NEW + "/v1/chat/completions"], calls)
                    self.assertEqual(CENTRAL, config["base_url"])
                    self.assertNotIn(ROUTE_TOKEN_KEY, config)

    def test_selected_gateway_remote_disconnect_is_upstream_error_not_client_disconnect(self):
        # A real urllib open against a gateway that reads the request and
        # closes without a status line raises http.client.RemoteDisconnected
        # unwrapped (getresponse runs outside urllib's URLError wrapper).  It
        # subclasses ConnectionResetError, so a raw re-raise reached the
        # protocol router as a client disconnect and no error was written.
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ClosesWithoutResponse)
        server.hits = 0
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        old = f"http://127.0.0.1:{server.server_address[1]}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for fn in ENTRYPOINTS:
            for retries in (None, 17):
                with self.subTest(entrypoint=fn.__name__, gateway_retries=retries):
                    server.hits = 0
                    selector, overlay, config = gateway_fixture(old)
                    if retries is None:
                        config.pop("gateway_retries")
                    pcfg = overlay.apply("cielairouter", config, session="same-session")
                    self.assertEqual(old, pcfg["base_url"])
                    calls = []
                    def dispatch(req, *, timeout, provider, pcfg):
                        calls.append(req.full_url)
                        def open_request():
                            if req.full_url.startswith(NEW):
                                return Response()
                            return opener.open(req, timeout=5)
                        return overlay.observed_open(req, pcfg, open_request)
                    activity, log = mock.Mock(), mock.Mock()
                    svc = services(dispatch, activity=activity, log=log)
                    with mock.patch.object(retry.time, "sleep") as sleep, self.assertRaises(Exception) as raised:
                        invoke(fn, svc, pcfg)
                    error = raised.exception
                    # The router's classifier must see an upstream failure,
                    # not the raw gateway error (a client-disconnect class).
                    self.assertFalse(
                        HttpResponseAdapter.is_client_disconnect(error),
                        f"delivered {type(error).__module__}.{type(error).__name__} {error!r} "
                        "is classified as a client disconnect",
                    )
                    self.assertIsInstance(error.__cause__, http.client.RemoteDisconnected)
                    self.assertTrue(HttpResponseAdapter.is_client_disconnect(error.__cause__))
                    self.assertIs(type(error), RuntimeError)
                    self.assertIn("RemoteDisconnected", str(error))
                    self.assertEqual(1, server.hits)
                    self.assertEqual([old + "/v1/chat/completions"], calls)
                    sleep.assert_not_called()
                    events = [call.args[0] for call in activity.call_args_list]
                    self.assertNotIn("retry", events)
                    self.assertEqual("error", events[-1])
                    self.assertEqual("RemoteDisconnected", activity.call_args_list[-1].kwargs["error"])
                    warn = [call.args[1] for call in log.call_args_list if call.args[0] == "WARN"]
                    self.assertEqual(1, len(warn))
                    self.assertIn("_transport_error ", warn[0])
                    self.assertIn("RemoteDisconnected", warn[0])
                    self.assertIn(old, selector.status()[0]["cooldowns"])
                    next_pcfg = overlay.apply("cielairouter", config, session="same-session")
                    self.assertEqual(NEW, next_pcfg["base_url"])

    def test_generic_provider_and_unoverlaid_cielairouter_keep_retry_budget(self):
        for fn in ENTRYPOINTS:
            for provider, token in (("other", None), ("cielairouter", None), ("cielairouter", "")):
                for kind in ("timeout", 503):
                    with self.subTest(fn=fn.__name__, provider=provider, token=token, kind=kind):
                        config = {"base_url": OLD, "gateway_retries": 17}
                        if token is not None:
                            config[ROUTE_TOKEN_KEY] = token
                        def fail(req, **_):
                            if kind == "timeout":
                                raise TimeoutError("timed out")
                            raise urllib.error.HTTPError(req.full_url, kind, "unavailable", {}, io.BytesIO(BODY))
                        dispatch = mock.Mock(side_effect=fail)
                        activity = mock.Mock()
                        with mock.patch.object(retry.time, "sleep") as sleep, self.assertRaises(Exception) as raised:
                            invoke(fn, services(dispatch, activity=activity), config, provider)
                        # CielAiRouter's typed open timeout shares its capped
                        # owner; an untyped 503 still cannot authorize replay.
                        expected = (11 if kind == "timeout" else 1) if provider == "cielairouter" else 18
                        self.assertEqual(expected, dispatch.call_count)
                        if provider == "cielairouter" and kind == "timeout":
                            self.assertTrue(
                                retry.retry_handled(raised.exception.__cause__),
                                "An exhausted timeout must not fall back to legacy replay",
                            )
                            self.assertEqual(10, sleep.call_count)
                            self.assertNotIn("retry", [call.args[0] for call in activity.call_args_list])

    def test_routed_auth_and_rate_limit_semantics_unchanged(self):
        for fn in ENTRYPOINTS:
            for code, key_count, retry_limits, expected in (
                    (401, 2, True, 1), (403, 2, True, 1), (400, 2, True, 1),
                    (429, 2, True, 2), (429, 1, True, 2), (429, 2, False, 1)):
                with self.subTest(fn=fn.__name__, code=code, keys=key_count, retry_limits=retry_limits):
                    selector, overlay, config = gateway_fixture()
                    config["gateway_retries"] = 1
                    pcfg = overlay.apply("cielairouter", config, session="session")
                    calls = []
                    def dispatch(req, *, pcfg, **_):
                        calls.append(req)
                        def open_request():
                            if len(calls) == 2:
                                return Response()
                            raise urllib.error.HTTPError(req.full_url, code, "rejected", {}, io.BytesIO(b"{}"))
                        return overlay.observed_open(req, pcfg, open_request)
                    svc = services(dispatch, key_count=key_count)
                    with mock.patch.object(retry.time, "sleep"):
                        if expected == 1:
                            with self.assertRaises(urllib.error.HTTPError) as raised:
                                invoke(fn, svc, pcfg, retry_rate_limits=retry_limits)
                            self.assertEqual(code, raised.exception.code)
                        else:
                            invoke(fn, svc, pcfg, retry_rate_limits=retry_limits)
                    self.assertEqual(expected, len(calls))
                    self.assertEqual({}, selector.status()[0]["cooldowns"])
                    if code == 429:
                        self.assertTrue(svc.keys.register_api_key_cooldown.called)
                    if expected == 2 and key_count > 1:
                        svc.keys.provider_headers.assert_called_once()
                        self.assertEqual("Bearer next", calls[1].get_header("Authorization"))
                    if expected == 2 and key_count == 1:
                        svc.rate_limit.register_backoff.assert_called_once()

if __name__ == "__main__":
    unittest.main()
