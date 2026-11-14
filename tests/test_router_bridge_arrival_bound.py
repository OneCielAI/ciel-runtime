"""A remote-bridge request keeps the 540 s pre-output bound from router arrival (ruling R2).

lead-rulings-independent-review-sse-retry-20261113.md R2: the bound is per router
arrival, not per path. ``do_POST`` stamps the arrival first. The bridge branch
must enter the same arrival-stamped retry scope as ``_dispatch_with_failover``,
under the same provider condition.

Two shapes, per R2-O1:
- ``ProductionBridgeArrivalClipTests`` is the production bridge shape. The real
  ``RemoteBridgeRoutingService.resolve`` writes REMOTE_BRIDGE_CONFIG_MARKER, which
  disables router replay, and the real ``open_provider_request_with_key_retry``
  makes the single open. Arrival 400 s ago with a 300 s provider timeout leaves
  140 s for that one open.
- ``BridgeArrivalBoundTests`` is a generic request_scope integration control: an
  unmarked config (``resolve_route=None``) and a test opener that replays 503s.
  Its repeated-open sequence cannot occur on a production bridge route; it only
  shows that the bridge branch's scope bounds whatever opens run inside it.

The clock is continue-retry's Clock from test_transient_retry_preoutput_bound.
"""
import io
import json
import unittest
import urllib.error
from contextlib import nullcontext
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support import router_http
from ciel_runtime_support import transient_retry as retry
from ciel_runtime_support import upstream_retry
from ciel_runtime_support.remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER, RemoteBridgeRoutingService
from ciel_runtime_support.router_http import RouterHttpHandler
from test_transient_retry_preoutput_bound import ERROR, Clock

PRE_OPEN_SECONDS = 200.0
ARRIVAL_AGO, PROVIDER_TIMEOUT = 400.0, 300.0


class Handler(RouterHttpHandler):
    """A request handler without a socket: do_POST runs on fixed headers and body."""

    def __init__(self, services, body: bytes):  # noqa: D107 - no BaseHTTPRequestHandler setup
        self._fixture_services = services
        self.path = "/v1/messages"
        self.headers = {"content-type": "application/json", "content-length": str(len(body)),
                        "x-ciel-remote-bridge": "1"}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.client_address = ("127.0.0.1", 50000)
        self.close_connection = False
        self.requestline = "POST /v1/messages HTTP/1.1"
        self.errors: list[tuple[int, str]] = []

    def _services(self):
        return self._fixture_services

    def _validated_content_length(self, _path, _services):
        return len(self.rfile.getvalue())

    def _read_request_body(self, _path, _services, _length):
        return self.rfile.getvalue()

    def _write_request_error(self, _path, _services, *, status, error_type, message):
        self.errors.append((status, error_type))


def services_for(runtime, bridge_request: bool, provider: str = "cielairouter", *,
                 resolve_route=None, config=None):
    passthrough = lambda *_a, **_k: False  # noqa: E731
    # The real endpoint dataclass: every optional handler keeps its real default.
    endpoints = router_http.RouterHttpPostEndpoints(
        speech=passthrough, llm_config=passthrough, channel_mcp=passthrough, chat=passthrough, plan=passthrough,
        runtime=runtime)
    bridge = router_http.RouterHttpRemoteBridge(
        enabled=lambda _cfg: True, resolve_route=resolve_route, is_request=lambda _handler, _cfg: bridge_request)
    loaded = config if config is not None else {"current_provider": provider}
    core = SimpleNamespace(
        load_config=lambda: loaded, reject_external=lambda *_a: False,
        get_current_provider=lambda _cfg: (provider, {}), parse_json_body=json.loads,
        is_client_disconnect=lambda _exc: False, log=lambda *_a: None,
        observe_runtime=lambda *_a: nullcontext(),
        request_body_policy=SimpleNamespace(admit=lambda *_a: nullcontext(),
                                            validate_parsed_body=lambda *_a: None),
        remote_bridge=bridge, session_keys=None)
    return SimpleNamespace(core=core, post=endpoints, files=None, access=None, session_keys_http=None,
                           presentation=SimpleNamespace(write_json=lambda *_a, **_k: None),
                           errors=SimpleNamespace(write_openai_responses_error=lambda *_a, **_k: None,
                                                  try_write_json=lambda *_a, **_k: None))


def clocked_default_budget(clock):
    real = retry._default_budget

    def build(config, provider, *, arrival_started=None):
        budget = real(config, provider, arrival_started=arrival_started)
        if budget is not None:
            budget.clock = clock
            budget.rand = lambda: 0.0
            budget.started = clock()
            if budget.window_mode and arrival_started is None:
                budget.arrival_started = budget.started
        return budget

    return build


def clocked_post(handler, clock):
    with mock.patch.object(retry, "RETRY_WINDOW_SECONDS", 420.0), \
            mock.patch.object(router_http.time, "monotonic", clock), \
            mock.patch.object(retry, "_default_budget", clocked_default_budget(clock)), \
            mock.patch("ciel_runtime_support.provider_failover.failover_service",
                       return_value=SimpleNamespace(effective=lambda _c, name, config, **_k: (name, config))), \
            mock.patch("ciel_runtime_support.cielairouter_gateway_overlay.gateway_overlay",
                       return_value=SimpleNamespace(apply=lambda _n, config, **_k: config)):
        handler.do_POST()


def upstream_services(provider_urlopen):
    """Inert ports around the real open_provider_request_with_key_retry."""
    nothing = lambda *_a, **_k: None  # noqa: E731
    return upstream_retry.UpstreamRetryServices(
        policy=upstream_retry.UpstreamRetryPolicy(
            configured_gateway_retries=lambda _c: 3, retry_after_exceeds_request_timeout=lambda *_a: (False, 0.0),
            retryable_upstream_exception=lambda _e: True, upstream_rate_limit_retry_message=nothing,
            upstream_retry_http_codes=frozenset({503}), upstream_retry_message=nothing,
            upstream_retry_wait_seconds=lambda _n: 0.0),
        keys=upstream_retry.UpstreamRetryKeys(
            key_from_request_headers=lambda _h: "", provider_api_key_count=lambda *_a: 1,
            provider_has_live_api_key=lambda *_a: True, provider_headers=lambda *_a, **_k: {},
            prepare_runtime_headers=lambda _p, _c, headers: headers, register_api_key_cooldown=nothing),
        rate_limit=upstream_retry.UpstreamRetryRateLimit(
            learn_headers=nothing, log=nothing, register_backoff=lambda *_a: 0.0, write_activity=nothing),
        http=upstream_retry.UpstreamRetryHttp(
            estimate_tokens=lambda _b: 1, provider_urlopen=provider_urlopen, set_stream_read_timeout=nothing,
            stream_idle_timeout_seconds=lambda *_a: 0.0, upstream_http_error_message=lambda _e, raw: raw))


class ProductionBridgeArrivalClipTests(unittest.TestCase):
    """R2-O1: the production bridge route makes one open, clipped from router arrival."""

    def drive(self, provider):
        clock = Clock()
        opens: list[float] = []
        markers: list[object] = []

        def provider_urlopen(_request, *, timeout, provider, pcfg):
            opens.append(timeout)
            raise urllib.error.HTTPError("http://gateway.test/v1/messages", 503, "x", {}, io.BytesIO(ERROR))

        def runtime(_handler, _cfg, name, pcfg, _path, body):
            markers.append(pcfg.get(REMOTE_BRIDGE_CONFIG_MARKER))
            clock.now += ARRIVAL_AGO  # route resolution and catalog before the single open
            try:
                upstream_retry.open_provider_request_with_key_retry(
                    "http://gateway.test/v1/messages", body, {}, PROVIDER_TIMEOUT, name, pcfg,
                    str(body.get("model") or ""), services=upstream_services(provider_urlopen))
            except Exception:  # noqa: BLE001 - the open count and its timeout are what is checked
                pass
            return True

        resolver = RemoteBridgeRoutingService(normalize_provider=lambda name: name,
                                              parse_bool=lambda value, _default: bool(value), environ={})
        config = {"current_provider": provider,
                  "providers": {provider: {"base_url": "http://gateway.test", "current_model": "m"}}}
        services = services_for(runtime, True, provider, resolve_route=resolver.resolve, config=config)
        body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]}).encode()
        clocked_post(Handler(services, body), clock)
        return opens, markers

    def test_marked_bridge_route_opens_once_clipped_from_arrival(self):
        opens, markers = self.drive("cielairouter")
        self.assertEqual([True], markers)  # the real resolver marked the route
        self.assertEqual(1, len(opens), opens)  # no replay on a bridge route
        self.assertAlmostEqual(540.0 - ARRIVAL_AGO, opens[0], places=6)

    def test_marked_bridge_route_for_another_provider_keeps_its_timeout(self):
        opens, markers = self.drive("deepseek")
        self.assertEqual([True], markers)
        self.assertEqual([PROVIDER_TIMEOUT], opens)


class BridgeArrivalBoundTests(unittest.TestCase):
    """Generic request_scope integration control (unmarked config); not the production bridge shape."""

    def drive(self, bridge_request: bool, provider: str = "cielairouter"):
        clock = Clock()
        opens: list[tuple[float, float]] = []
        answered: list[float] = []

        def runtime(_handler, _cfg, name, pcfg, _path, _body):
            clock.now += PRE_OPEN_SECONDS  # route resolution and catalog before the first open

            def open_once():
                timeout = retry.bounded_timeout(pcfg, name, 300.0)
                opens.append((clock.now - 1000.0, timeout))
                clock.now += min(88.0, timeout)
                raise urllib.error.HTTPError("http://gateway/v1/messages", 503, "x", {}, io.BytesIO(ERROR))

            try:
                retry.open_with_transient_retry(open_once, provider=name, config=pcfg,
                                                log=lambda *_a: None, sleep=clock.sleep)
            except Exception:  # noqa: BLE001 - the answer (an error) is what is timed
                pass
            answered.append(clock.now - 1000.0)
            return True

        services = services_for(runtime, bridge_request, provider)
        body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]}).encode()
        clocked_post(Handler(services, body), clock)
        return opens, answered

    def test_bridge_request_answers_before_540_seconds_from_arrival(self):
        opens, answered = self.drive(bridge_request=True)
        self.assertTrue(opens, "the bridge request never reached its opener")
        self.assertEqual(PRE_OPEN_SECONDS, opens[0][0])
        for elapsed, timeout in opens:
            self.assertLessEqual(elapsed + timeout, 540.0 + 1e-9, opens)
        self.assertLessEqual(answered[-1], 540.0 + 1e-9, (answered, opens))

    def test_the_routed_path_still_answers_inside_the_bound(self):
        opens, answered = self.drive(bridge_request=False)
        self.assertTrue(opens)
        self.assertLessEqual(answered[-1], 540.0 + 1e-9, (answered, opens))

    def test_another_provider_opens_no_bridge_scope(self):
        seen = []

        def runtime(_handler, _cfg, name, pcfg, _path, _body):
            seen.append(retry._SCOPE.get())
            return True

        services = services_for(runtime, True, provider="deepseek")
        body = json.dumps({"model": "m", "messages": []}).encode()
        Handler(services, body).do_POST()
        self.assertEqual([None], seen)


if __name__ == "__main__":
    unittest.main()
