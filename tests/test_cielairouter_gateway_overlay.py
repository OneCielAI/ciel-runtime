import copy
import io
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest import mock

import ciel_runtime
from ciel_runtime_support import cielairouter_gateway_overlay as overlay_module
from ciel_runtime_support import provider_network
from ciel_runtime_support.cielairouter_gateway_overlay import (
    DISCOVERY_BASE_URL_KEY,
    ROUTE_TOKEN_KEY,
    CielAiRouterGatewayOverlay,
    discovery_provider_config,
    gateway_overlay_status,
    observed_gateway_open,
    use_gateway_overlay,
)
from ciel_runtime_support.provider_failover import (
    Backup,
    FailoverRequested,
    ProviderFailoverService,
    use_failover_service,
)
from ciel_runtime_support.router_http import RouterHttpHandler
from ciel_runtime_support.router_server_context import RouterServerContext

CENTRAL = "https://ciel-router-01.ezonebot.com"
GATEWAY = "https://gw-2.ezonebot.com"


def cielairouter_pcfg(**overrides):
    pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["cielairouter"])
    pcfg.update({"base_url": CENTRAL, "api_key": "router-key", "current_model": "ASTRA"})
    pcfg.update(overrides)
    return pcfg


class FakeSelector:
    def __init__(self, endpoint=GATEWAY, *, candidate=True, reason="ranked", degraded=False, error=None):
        self.calls = []
        self.endpoint = endpoint
        self.candidate = candidate
        self.reason = reason
        self.degraded = degraded
        self.error = error

    def select(self, base_url, api_key, model=""):
        self.calls.append((base_url, api_key, model))
        if self.error is not None:
            raise self.error
        candidate = SimpleNamespace(node_id="gw-2") if self.candidate else None
        return SimpleNamespace(endpoint=self.endpoint if self.candidate else base_url, candidate=candidate,
                               reason=self.reason, degraded=self.degraded, generation=1)

    def status(self):
        return [{"origin": "ciel-router-01.ezonebot.com", "endpoint": self.endpoint}]


class Recorder:
    def __init__(self):
        self.lines = []

    def __call__(self, level, message):
        self.lines.append((level, message))

    def named(self, name):
        return [line for line in self.lines if name in line[1]]


class OverlayTests(unittest.TestCase):
    def overlay(self, selector, log=None):
        self.factory_calls = 0

        def factory():
            self.factory_calls += 1
            if isinstance(selector, Exception):
                raise selector
            return selector

        return CielAiRouterGatewayOverlay(factory, log=log or Recorder())

    def test_other_providers_are_returned_untouched_without_loading_the_selector(self):
        overlay = self.overlay(FakeSelector())
        pcfg = {"base_url": "http://a", "api_key": "k"}
        self.assertIs(pcfg, overlay.apply("vllm", pcfg))
        self.assertEqual(0, self.factory_calls)

    def test_selected_gateway_is_applied_to_a_copy_with_credentials_and_central_base(self):
        selector = FakeSelector()
        log = Recorder()
        overlay = self.overlay(selector, log)
        pcfg = cielairouter_pcfg(api_keys=["router-key", "second-key"])
        original = copy.deepcopy(pcfg)
        routed = overlay.apply("cielairouter", pcfg, path="/v1/messages")
        self.assertIsNot(pcfg, routed)
        self.assertEqual(original, pcfg)
        self.assertEqual(GATEWAY, routed["base_url"])
        self.assertEqual(CENTRAL, routed[DISCOVERY_BASE_URL_KEY])
        self.assertEqual("router-key", routed["api_key"])
        self.assertEqual(["router-key", "second-key"], routed["api_keys"])
        self.assertEqual([(CENTRAL, "router-key", "ASTRA")], selector.calls)
        self.assertEqual(1, len(log.named("cielairouter_gateway_selected")))
        self.assertNotIn("router-key", repr(log.lines))
        status = overlay.status()
        self.assertEqual("available", status["selector"])
        self.assertEqual("gateway", status["routes"][0]["state"])
        self.assertEqual(GATEWAY, status["routes"][0]["endpoint"])
        self.assertEqual("ciel-router-01.ezonebot.com", status["routes"][0]["central"])
        self.assertNotIn("router-key", repr(status))
        self.assertIn(GATEWAY, overlay.status_line())

    def test_a_second_request_with_the_same_route_does_not_log_again(self):
        log = Recorder()
        overlay = self.overlay(FakeSelector(), log)
        overlay.apply("cielairouter", cielairouter_pcfg())
        overlay.apply("cielairouter", cielairouter_pcfg())
        self.assertEqual(1, len(log.named("cielairouter_gateway_selected")))
        self.assertEqual(1, self.factory_calls)

    def test_applying_to_an_overlay_copy_discovers_from_the_central_base(self):
        selector = FakeSelector()
        overlay = self.overlay(selector)
        routed = overlay.apply("cielairouter", cielairouter_pcfg())
        again = overlay.apply("cielairouter", routed)
        self.assertEqual([CENTRAL, CENTRAL], [call[0] for call in selector.calls])
        self.assertEqual(GATEWAY, again["base_url"])
        self.assertEqual(CENTRAL, again[DISCOVERY_BASE_URL_KEY])

    def test_no_gateway_candidate_keeps_the_central_base(self):
        log = Recorder()
        overlay = self.overlay(FakeSelector(candidate=False, reason="discovery unavailable", degraded=True), log)
        pcfg = cielairouter_pcfg()
        self.assertIs(pcfg, overlay.apply("cielairouter", pcfg))
        central_lines = log.named("cielairouter_gateway_central")
        self.assertEqual(1, len(central_lines))
        self.assertEqual("WARN", central_lines[0][0])
        self.assertIn("reason=discovery unavailable", central_lines[0][1])
        self.assertEqual("central", overlay.status()["routes"][0]["state"])
        self.assertEqual("", overlay.status_line())

    def test_selector_failure_keeps_the_central_base_and_logs_once(self):
        log = Recorder()
        error = RuntimeError("secret body router-key")
        overlay = self.overlay(FakeSelector(error=error), log)
        pcfg = cielairouter_pcfg()
        self.assertIs(pcfg, overlay.apply("cielairouter", pcfg))
        self.assertIs(pcfg, overlay.apply("cielairouter", pcfg))
        failures = log.named("cielairouter_gateway_select_failed")
        self.assertEqual(1, len(failures))
        self.assertIn("error=RuntimeError", failures[0][1])
        self.assertNotIn("secret body", repr(log.lines))
        self.assertEqual("failed", overlay.status()["routes"][0]["state"])

    def test_missing_selector_module_keeps_the_central_base_and_loads_once(self):
        log = Recorder()
        overlay = self.overlay(ImportError("no module"), log)
        pcfg = cielairouter_pcfg()
        self.assertIs(pcfg, overlay.apply("cielairouter", pcfg))
        self.assertIs(pcfg, overlay.apply("cielairouter", pcfg))
        self.assertEqual(1, self.factory_calls)
        self.assertEqual(1, len(log.named("cielairouter_gateway_selector_unavailable")))
        status = overlay.status()
        self.assertEqual("unavailable", status["selector"])
        self.assertEqual("ImportError", status["selector_error"])

    def test_without_a_router_key_no_discovery_is_attempted(self):
        overlay = self.overlay(FakeSelector())
        pcfg = cielairouter_pcfg(api_key="", api_keys=[])
        self.assertIs(pcfg, overlay.apply("cielairouter", pcfg))
        self.assertEqual(0, self.factory_calls)

    def test_status_before_first_use_does_not_load_the_selector(self):
        use_gateway_overlay(None)
        status = gateway_overlay_status()
        self.assertEqual("not_loaded", status["selector"])
        self.assertIsNone(overlay_module._OVERLAY)


class CentralCatalogIdentityTests(unittest.TestCase):
    def test_overlay_copy_restores_the_central_base(self):
        routed = cielairouter_pcfg(base_url=GATEWAY, **{DISCOVERY_BASE_URL_KEY: CENTRAL})
        restored = discovery_provider_config(routed)
        self.assertEqual(CENTRAL, restored["base_url"])
        self.assertNotIn(DISCOVERY_BASE_URL_KEY, restored)
        self.assertEqual(GATEWAY, routed["base_url"])
        plain = cielairouter_pcfg()
        self.assertIs(plain, discovery_provider_config(plain))

    def test_model_cache_key_is_the_central_catalog_key(self):
        central = cielairouter_pcfg()
        routed = cielairouter_pcfg(base_url=GATEWAY, **{DISCOVERY_BASE_URL_KEY: CENTRAL})
        self.assertEqual(
            ciel_runtime.model_cache_key("cielairouter", central),
            ciel_runtime.model_cache_key("cielairouter", routed),
        )
        self.assertNotEqual(
            ciel_runtime.model_cache_key("cielairouter", central),
            ciel_runtime.model_cache_key("cielairouter", cielairouter_pcfg(base_url=GATEWAY)),
        )

    def test_catalog_fetch_reads_the_central_base(self):
        urls = []
        written = []

        def http_json(url, **_kwargs):
            urls.append(url)
            return {"data": [{"id": "ASTRA", "capabilities": {"tool_calling": True, "thinking": True}}]}

        routed = cielairouter_pcfg(base_url=GATEWAY, **{DISCOVERY_BASE_URL_KEY: CENTRAL})
        with mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=None), \
                mock.patch.object(ciel_runtime, "write_model_list_cache", side_effect=lambda provider, pcfg, *_rest: written.append(pcfg["base_url"])), \
                mock.patch.object(ciel_runtime, "write_model_registry", return_value=None), \
                mock.patch.object(ciel_runtime, "http_json", side_effect=http_json):
            ids = ciel_runtime.upstream_model_ids("cielairouter", routed)
        self.assertIn("ASTRA", ids)
        self.assertTrue(urls)
        self.assertTrue(all(url.startswith(CENTRAL + "/") for url in urls), urls)
        self.assertEqual([CENTRAL], written)


class RouterDispatchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.failover = ProviderFailoverService(Path(self._tmp.name))
        use_failover_service(self.failover)
        self.selector = FakeSelector()
        self.log = Recorder()
        use_gateway_overlay(CielAiRouterGatewayOverlay(lambda: self.selector, log=self.log))

    def tearDown(self):
        use_gateway_overlay(None)
        use_failover_service(None)
        self._tmp.cleanup()

    def dispatch(self, cfg, provider, path="/v1/messages", runtime=None):
        seen = []

        def default_runtime(_handler, _cfg, routed_provider, pcfg, _path, _body):
            seen.append((routed_provider, copy.deepcopy(pcfg)))
            return True

        services = SimpleNamespace(core=SimpleNamespace(log=self.log, observe_runtime=lambda *_args: mock.MagicMock()))
        endpoints = SimpleNamespace(runtime=runtime or default_runtime)
        handler = SimpleNamespace(_ciel_runtime_response_status=None)
        body = {"model": "ciel-runtime-cielairouter-astra", "messages": [{"role": "user", "content": "hi"}]}
        pcfg = cfg["providers"][provider]
        result = RouterHttpHandler._dispatch_with_failover(handler, services, endpoints, cfg, provider, pcfg, path, body)
        return result, seen

    def test_cielairouter_request_is_sent_to_the_selected_gateway_without_mutating_config(self):
        cfg = {"current_provider": "cielairouter", "providers": {"cielairouter": cielairouter_pcfg()}}
        before = copy.deepcopy(cfg)
        result, seen = self.dispatch(cfg, "cielairouter")
        self.assertTrue(result)
        self.assertEqual("cielairouter", seen[0][0])
        self.assertEqual(GATEWAY, seen[0][1]["base_url"])
        self.assertEqual(CENTRAL, seen[0][1][DISCOVERY_BASE_URL_KEY])
        self.assertEqual("router-key", seen[0][1]["api_key"])
        self.assertEqual(before, cfg)

    def test_non_llm_paths_and_other_providers_are_not_overlaid(self):
        cfg = {"current_provider": "cielairouter", "providers": {"cielairouter": cielairouter_pcfg()}}
        _result, seen = self.dispatch(cfg, "cielairouter", path="/ca/other")
        self.assertEqual(CENTRAL, seen[0][1]["base_url"])
        cfg = {"current_provider": "vllm", "providers": {"vllm": {"base_url": "http://a", "api_key": "k"}}}
        _result, seen = self.dispatch(cfg, "vllm")
        self.assertEqual("http://a", seen[0][1]["base_url"])
        self.assertEqual([], self.selector.calls)

    def test_active_failover_to_cielairouter_serves_through_the_gateway(self):
        cfg = {"current_provider": "vllm", "providers": {
            "vllm": {"base_url": "http://a", "api_key": "k", "current_model": "m"},
            "cielairouter": cielairouter_pcfg(),
        }}
        self.failover.activate("vllm", Backup("cielairouter", "ASTRA"), reason="test", trigger="manual",
                               until=time.time() + 600)
        _result, seen = self.dispatch(cfg, "vllm")
        self.assertEqual("cielairouter", seen[0][0])
        self.assertEqual(GATEWAY, seen[0][1]["base_url"])
        self.assertEqual(CENTRAL, cfg["providers"]["cielairouter"]["base_url"])

    def test_failover_retry_away_from_cielairouter_uses_the_backup_config(self):
        cfg = {"current_provider": "cielairouter", "providers": {
            "cielairouter": cielairouter_pcfg(),
            "vllm": {"base_url": "http://a", "api_key": "k", "current_model": "m"},
        }}
        attempts = []

        def runtime(_handler, _cfg, routed_provider, pcfg, _path, _body):
            attempts.append((routed_provider, copy.deepcopy(pcfg)))
            if routed_provider == "cielairouter":
                self.failover.activate("cielairouter", Backup("vllm", "m"), reason="429", trigger="auto",
                                       until=time.time() + 600)
                raise FailoverRequested("cielairouter", "vllm", "429")
            return True

        result, _seen = self.dispatch(cfg, "cielairouter", runtime=runtime)
        self.assertTrue(result)
        self.assertEqual(["cielairouter", "vllm"], [attempt[0] for attempt in attempts])
        self.assertEqual(GATEWAY, attempts[0][1]["base_url"])
        self.assertEqual("http://a", attempts[1][1]["base_url"])
        self.assertNotIn(DISCOVERY_BASE_URL_KEY, attempts[1][1])


class HealthStatusTests(unittest.TestCase):
    def health_payload(self, provider):
        health = SimpleNamespace(
            version="v", source_fingerprint="f",
            runtime=SimpleNamespace(current_pid=lambda: 1, active_client_pids=lambda: []),
            current_user=lambda: "u", home=Path("."), config_dir=Path("."), workspace="w",
            router_port=1, instance_id="i", current_alias=lambda _cfg: "alias",
        )
        return RouterServerContext.health_payload(SimpleNamespace(health=health), {}, provider, {})

    def tearDown(self):
        use_gateway_overlay(None)

    def test_health_reports_the_gateway_route_only_for_cielairouter(self):
        use_gateway_overlay(None)
        self.assertNotIn("cielairouter_gateway", self.health_payload("vllm"))
        self.assertEqual("not_loaded", self.health_payload("cielairouter")["cielairouter_gateway"]["selector"])
        overlay = CielAiRouterGatewayOverlay(FakeSelector)
        overlay.apply("cielairouter", cielairouter_pcfg())
        use_gateway_overlay(overlay)
        status = self.health_payload("cielairouter")["cielairouter_gateway"]
        self.assertEqual(GATEWAY, status["routes"][0]["endpoint"])
        self.assertEqual([{"origin": "ciel-router-01.ezonebot.com", "endpoint": GATEWAY}], status["gateways"])


def _iso(moment):
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class RealSelectorTests(unittest.TestCase):
    def test_gateway_service_selection_is_applied(self):
        from ciel_runtime_support.cielairouter_gateways import CielAiRouterGatewayService, ProbeResult

        now = datetime.now(timezone.utc)
        discovery_calls = []

        def discovery(base, key, timeout):
            discovery_calls.append((base, key))
            return {
                "object": "list", "protocol_version": 1, "generated_at": _iso(now - timedelta(seconds=1)),
                "data": [{
                    "id": "gw-2", "endpoint": GATEWAY + "/v1", "status": "online", "health": "healthy",
                    "lastSeenAt": _iso(now - timedelta(seconds=2)),
                    "availableUntil": _iso(now + timedelta(minutes=5)),
                }],
            }

        def probe(candidate, timeout):
            return ProbeResult(True, 0.01, 200, candidate.node_id, "gateway", True)

        service = CielAiRouterGatewayService(discovery=discovery, probe=probe)
        overlay = CielAiRouterGatewayOverlay(lambda: service)
        routed = overlay.apply("cielairouter", cielairouter_pcfg())
        self.assertEqual(GATEWAY, routed["base_url"])
        self.assertEqual(CENTRAL, routed[DISCOVERY_BASE_URL_KEY])
        self.assertEqual([(CENTRAL, "router-key")], discovery_calls)
        self.assertEqual("gw-2", overlay.status()["routes"][0]["node_id"])


class SessionStickinessTests(unittest.TestCase):
    """Each client session keeps the gateway it started on; a new session ranks again."""

    class Selector:
        def __init__(self):
            self.calls = []
            self.next = ["https://gw-a.test", "https://gw-b.test", "https://gw-c.test"]
            self.cooling = set()

        def select(self, base_url, api_key, model="", *, fresh=False):
            self.calls.append(fresh)
            endpoint = self.next.pop(0)
            return SimpleNamespace(endpoint=endpoint, candidate=SimpleNamespace(node_id=endpoint[-6:]),
                                   reason="ranked", degraded=False, generation=len(self.calls))

        def is_cooling(self, selection):
            return selection.endpoint in self.cooling

    def setUp(self):
        self.selector = self.Selector()
        self.overlay = CielAiRouterGatewayOverlay(lambda: self.selector, log=Recorder())

    def route(self, session):
        return self.overlay.apply("cielairouter", cielairouter_pcfg(), session=session)["base_url"]

    def test_a_session_keeps_its_gateway_and_a_new_session_ranks_again(self):
        self.assertEqual("https://gw-a.test", self.route("session-1"))
        self.assertEqual("https://gw-a.test", self.route("session-1"))
        self.assertEqual("https://gw-b.test", self.route("session-2"))
        self.assertEqual([True, True], self.selector.calls)

    def test_a_cooling_gateway_moves_only_its_session(self):
        self.assertEqual("https://gw-a.test", self.route("session-1"))
        self.assertEqual("https://gw-b.test", self.route("session-2"))
        self.selector.cooling.add("https://gw-a.test")
        self.assertEqual("https://gw-c.test", self.route("session-1"))
        self.assertEqual("https://gw-b.test", self.route("session-2"))

    def test_requests_without_a_session_do_not_force_a_new_ranking(self):
        self.route("")
        self.assertEqual([False], self.selector.calls)


class FailureFeedbackTests(unittest.TestCase):
    """A gateway's own failure moves later requests elsewhere; the request is not replayed."""

    class Selector(FakeSelector):
        def __init__(self):
            super().__init__()
            self.failures = []

        def record_failure(self, selection, classification):
            self.failures.append((selection.endpoint, classification))
            return True

    class Response(io.BytesIO):
        def __init__(self, body, content_type):
            super().__init__(body)
            self.status = 200
            self.headers = {"content-type": content_type}

    def routed(self, **overrides):
        self.selector = self.Selector()
        self.log = Recorder()
        self.overlay = CielAiRouterGatewayOverlay(lambda: self.selector, log=self.log)
        use_gateway_overlay(self.overlay)
        self.addCleanup(use_gateway_overlay, None)
        return self.overlay.apply("cielairouter", cielairouter_pcfg(**overrides), path="/v1/messages")

    def request(self, url):
        return urllib.request.Request(url, data=b"{}", method="POST")

    def test_off_switch_keeps_the_configured_base_without_discovery(self):
        selector = self.Selector()
        overlay = CielAiRouterGatewayOverlay(lambda: selector, log=Recorder())
        pcfg = cielairouter_pcfg(gateway_selection="off")
        routed = overlay.apply("cielairouter", pcfg)
        self.assertEqual(CENTRAL, routed["base_url"])
        self.assertNotIn(ROUTE_TOKEN_KEY, routed)
        self.assertEqual([], selector.calls)

    def test_connection_failure_rotates_and_reraises_the_same_error(self):
        pcfg = self.routed()
        error = urllib.error.URLError(ConnectionResetError(10054, "reset"))

        def fail():
            raise error

        with self.assertRaises(urllib.error.URLError) as raised:
            observed_gateway_open(self.request(GATEWAY + "/v1/messages"), pcfg, fail)
        self.assertIs(error, raised.exception)
        self.assertEqual([(GATEWAY, "connection")], self.selector.failures)
        self.assertTrue(self.log.named("cielairouter_gateway_failure"))

    def test_stream_ending_before_its_completion_event_rotates(self):
        pcfg = self.routed()
        body = b'event: message_start\ndata: {"type":"message_start"}\n\n'
        response = observed_gateway_open(
            self.request(GATEWAY + "/v1/messages"), pcfg,
            lambda: self.Response(body, "text/event-stream"),
        )
        self.assertEqual(body, b"".join(response))
        self.assertEqual([(GATEWAY, "stream_truncated")], self.selector.failures)

    def test_completed_stream_and_json_response_do_not_rotate(self):
        pcfg = self.routed()
        stream = b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        response = observed_gateway_open(
            self.request(GATEWAY + "/v1/messages"), pcfg,
            lambda: self.Response(stream, "text/event-stream"),
        )
        self.assertEqual(stream, response.read())
        response = observed_gateway_open(
            self.request(GATEWAY + "/v1/responses"), pcfg,
            lambda: self.Response(b'{"id":"r"}', "application/json"),
        )
        self.assertEqual(b'{"id":"r"}', response.read())
        self.assertEqual([], self.selector.failures)

    def test_provider_error_events_do_not_penalize_gateway(self):
        pcfg = self.routed()
        for path in ('/v1/messages', '/v1/responses', '/v1/chat/completions'):
            for body in (b'event: error\ndata: {"type":"error","message":"private"}\n\n',
                         b'event: error\ndata: ' + b'x' * 100000 + b'\n\n'):
                response = observed_gateway_open(self.request(GATEWAY + path), pcfg,
                    lambda: self.Response(body, 'text/event-stream'))
                self.assertEqual(response.read(), body)
                response.close()
        self.assertEqual([], self.selector.failures)
        self.assertFalse(self.log.named('cielairouter_gateway_failure'))

    def test_partial_terminal_exception_is_identical_and_does_not_penalize(self):
        import http.client
        pcfg = self.routed()
        for complete in (True, False):
            body = b'event: response.completed\ndata: {"type":"response.completed"}\n\n'
            partial = body if complete else body[:-1]
            error = http.client.IncompleteRead(partial, 42)
            raw = self.Response(b'', 'text/event-stream')
            def fail(*args):
                raise error
            raw.read = fail
            response = observed_gateway_open(self.request(GATEWAY + '/v1/responses'), pcfg, lambda: raw)
            with self.assertRaises(http.client.IncompleteRead) as raised:
                response.read(65536)
            self.assertIs(raised.exception, error)
            self.assertIs(raised.exception.partial, partial)
            response.close()
            self.assertEqual([] if complete else [(GATEWAY, 'connection')], self.selector.failures)

    def test_rejected_key_and_rate_limit_are_not_gateway_failures(self):
        pcfg = self.routed()
        for code in (401, 403, 429):
            error = urllib.error.HTTPError(GATEWAY + "/v1/messages", code, "x", {}, io.BytesIO(b"{}"))

            def fail(error=error):
                raise error

            with self.assertRaises(urllib.error.HTTPError):
                observed_gateway_open(self.request(GATEWAY + "/v1/messages"), pcfg, fail)
        self.assertEqual([], self.selector.failures)

    def test_central_catalog_requests_are_not_observed(self):
        pcfg = self.routed()
        sentinel = object()
        self.assertIs(sentinel, observed_gateway_open(self.request(CENTRAL + "/v1/models"), pcfg, lambda: sentinel))

    def test_provider_urlopen_routes_gateway_requests_through_observation(self):
        pcfg = self.routed()
        error = urllib.error.URLError(TimeoutError())
        opener = mock.Mock()
        opener.open.side_effect = error
        with mock.patch.object(provider_network.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(urllib.error.URLError):
                provider_network.provider_urlopen(self.request(GATEWAY + "/v1/messages"), 5, "cielairouter", pcfg, Recorder())
        self.assertEqual([(GATEWAY, "timeout")], self.selector.failures)

    def test_restored_central_config_drops_the_route_token(self):
        pcfg = self.routed()
        self.assertIn(ROUTE_TOKEN_KEY, pcfg)
        self.assertNotIn(ROUTE_TOKEN_KEY, discovery_provider_config(pcfg))


class SessionReselectionTests(unittest.TestCase):
    def test_pin_expires_at_candidate_availability_deadline(self):
        now = [1000.0]
        selector = SessionStickinessTests.Selector()
        original = selector.select

        def select(*args, **kwargs):
            result = original(*args, **kwargs)
            result.candidate.available_until = now[0] + 2
            return result

        selector.select = select
        overlay = CielAiRouterGatewayOverlay(lambda: selector, clock=lambda: now[0])
        def route():
            return overlay.apply('cielairouter', cielairouter_pcfg(), session='one')['base_url']
        self.assertEqual('https://gw-a.test', route())
        now[0] += 1
        self.assertEqual('https://gw-a.test', route())
        now[0] += 1
        self.assertEqual('https://gw-b.test', route())

    def test_session_pin_is_scoped_by_api_key(self):
        selector = SessionStickinessTests.Selector()
        overlay = CielAiRouterGatewayOverlay(lambda: selector)
        def route(key):
            return overlay.apply('cielairouter', cielairouter_pcfg(api_key=key), session='same')['base_url']
        self.assertEqual('https://gw-a.test', route('key-one'))
        self.assertEqual('https://gw-b.test', route('key-two'))
        self.assertEqual('https://gw-a.test', route('key-one'))

    def test_simultaneous_first_requests_share_one_pin_not_last_writer_wins(self):
        entered, release = threading.Event(), threading.Event()
        selector = SessionStickinessTests.Selector()
        original = selector.select

        def select(*args, **kwargs):
            result = original(*args, **kwargs)
            if len(selector.calls) == 1:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError()
            return result

        selector.select = select
        overlay = CielAiRouterGatewayOverlay(lambda: selector)
        routes = []
        def route():
            routes.append(overlay.apply('cielairouter', cielairouter_pcfg(), session='same')['base_url'])
        first = threading.Thread(target=route)
        second = threading.Thread(target=route)
        first.start()
        try:
            self.assertTrue(entered.wait(5))
            second.start()
            second.join(.1)
        finally:
            release.set()
            first.join(5)
            second.join(5)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(['https://gw-a.test'] * 2, sorted(routes))
        self.assertEqual([True], selector.calls)
        for _ in range(16):
            route()
        self.assertEqual({'https://gw-a.test'}, set(routes))


class LocalGatewayFixtureTests(unittest.TestCase):
    def test_large_sse_loopback_does_not_penalize_gateway(self):
        wire = [b'']

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(wire[0])))
                self.end_headers()
                for offset in range(0, len(wire[0]), 997):
                    self.wfile.write(wire[0][offset:offset + 997])

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        endpoint = f'http://127.0.0.1:{server.server_port}'
        selector = FailureFeedbackTests.Selector()
        selector.endpoint = endpoint
        overlay = CielAiRouterGatewayOverlay(lambda: selector, log=Recorder())
        routed = overlay.apply('cielairouter', cielairouter_pcfg())
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            for kind in ('response.completed', 'response.incomplete', 'response.failed', 'message_stop'):
                path = '/v1/messages' if kind == 'message_stop' else '/v1/responses'
                for explicit in (True, False):
                    with self.subTest(kind=kind, explicit=explicit):
                        wire[0] = (b'data: ' + json.dumps({'padding': 'x' * 131072, 'type': kind}).encode()
                                   + b'\r\n' + (f'event: {kind}\r\n'.encode() if explicit else b'') + b'\r\n')
                        request = urllib.request.Request(endpoint + path)
                        with overlay.observed_open(request, routed, lambda: opener.open(request, timeout=3)) as response:
                            actual = bytearray()
                            while part := response.read(113):
                                actual.extend(part)
                            self.assertEqual(bytes(actual), wire[0])
                            self.assertEqual(response.terminal_observed, kind if explicit else None)
                        self.assertEqual(selector.failures, [])
            wire[0] = b'data: {}\n\n'
            request = urllib.request.Request(endpoint + '/v1/responses')
            with overlay.observed_open(request, routed, lambda: opener.open(request, timeout=3)) as response:
                response.read()
            self.assertEqual(selector.failures, [(endpoint, 'stream_truncated')])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def test_unreachable_loopback_then_failure_rotates_once_and_16_later_posts_stick(self):
        from ciel_runtime_support.cielairouter_gateways import CielAiRouterGatewayService

        traffic = []
        payload = {}
        fail = [False]

        def handler(node):
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_):
                    pass

                def reply(self, code, body):
                    data = json.dumps(body).encode()
                    self.send_response(code)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

                def do_GET(self):
                    traffic.append((node, 'GET', self.path, self.headers.get('Authorization')))
                    self.reply(200, payload if self.path == '/api/gateway/discovery' else
                               {'nodeId': node, 'role': 'gateway', 'ready': True})

                def do_POST(self):
                    self.rfile.read(int(self.headers.get('Content-Length', 0)))
                    traffic.append((node, 'POST', self.path, self.headers.get('Authorization')))
                    self.reply(503 if node == 'a' and fail[0] else 200, {'gateway': node})
            return Handler

        servers = [ThreadingHTTPServer(('127.0.0.1', 0), handler(node)) for node in ('central', 'a', 'b')]
        threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
        # A bound socket without listen is reliably unreachable, without recycling its port.
        with socket.socket() as unreachable:
            unreachable.bind(('127.0.0.1', 0))
            endpoints = [f'http://127.0.0.1:{server.server_port}' for server in servers]
            dead = f'http://127.0.0.1:{unreachable.getsockname()[1]}'
            now = datetime.now(timezone.utc)
            payload.update(object='list', protocol_version=1, generated_at=_iso(now), data=[
                {'id': node, 'endpoint': endpoint, 'endpoints': [endpoint], 'status': 'online',
                 'health': 'healthy', 'lastSeenAt': _iso(now - timedelta(seconds=1)),
                 'availableUntil': _iso(now + timedelta(minutes=5)),
                 'congestion': {'utilization': load, 'backlog': 0, 'observedAt': _iso(now)}}
                for node, endpoint, load in [('dead', dead, 0), ('a', endpoints[1], .1), ('b', endpoints[2], .2)]])
            for thread in threads:
                thread.start()
            try:
                service = CielAiRouterGatewayService()
                overlay = CielAiRouterGatewayOverlay(lambda: service)
                pcfg = cielairouter_pcfg(base_url=endpoints[0], api_key='fixture-key')
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                # Use a real urllib client for discovery/probes, avoiding host proxy settings.
                from ciel_runtime_support.cielairouter_gateways import GatewayHttpClient
                client = GatewayHttpClient(opener.open)
                service.discovery, service.probe = client.discovery, client.probe

                def post():
                    routed = overlay.apply('cielairouter', pcfg, session='fixture-session')
                    request = urllib.request.Request(routed['base_url'] + '/v1/messages', data=b'{}',
                        headers={'Authorization': 'Bearer fixture-key'}, method='POST')
                    with overlay.observed_open(request, routed, lambda: opener.open(request, timeout=2)) as response:
                        return json.loads(response.read())

                self.assertEqual({'gateway': 'a'}, post())
                fail[0] = True
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    post()
                self.assertEqual(503, raised.exception.code)
                self.assertEqual(['a', 'a'], [t[0] for t in traffic if t[1] == 'POST'])
                for _ in range(16):
                    self.assertEqual({'gateway': 'b'}, post())
                self.assertEqual(['a', 'a'] + ['b'] * 16, [t[0] for t in traffic if t[1] == 'POST'])
                self.assertTrue(all(t[3] is None for t in traffic if t[2] == '/healthz'))
                self.assertTrue(all(t[3] == 'Bearer fixture-key' for t in traffic if t[2] == '/api/gateway/discovery'))
                self.assertEqual(1, overlay.status()['sessions'])
            finally:
                for server in servers:
                    server.shutdown()
                    server.server_close()
                for thread in threads:
                    thread.join(5)


if __name__ == "__main__":
    unittest.main()
