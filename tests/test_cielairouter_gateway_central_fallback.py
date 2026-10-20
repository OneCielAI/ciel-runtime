"""A discovery node whose endpoint is the configured central base.

The selector can return such a node as a gateway candidate (for example a
``reprobed fallback`` when every node is degraded).  The overlay sends that
request to the central base unchanged, without a route token, so no transport
observation exists for it.  These tests drive the real selector with injected
discovery/probe callbacks; nothing here touches the network.
"""

import copy
import ssl
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from unittest import mock

import ciel_runtime
from ciel_runtime_support import provider_network
from ciel_runtime_support.cielairouter_gateway_overlay import (
    DISCOVERY_BASE_URL_KEY,
    ROUTE_TOKEN_KEY,
    CielAiRouterGatewayOverlay,
    observed_gateway_open,
    use_gateway_overlay,
)
from ciel_runtime_support.cielairouter_gateways import CielAiRouterGatewayService, ProbeResult

CENTRAL = "https://ciel-router-01.ezonebot.com"
GATEWAY = "https://gw-2.ezonebot.com"
CENTRAL_REASON = "gateway endpoint is the central base"


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def node(node_id, endpoint, now, *, health):
    return {"id": node_id, "endpoint": endpoint, "endpoints": [endpoint], "status": "online",
            "health": health, "lastSeenAt": stamp(now - 2), "availableUntil": stamp(now + 3600),
            "healthUrl": endpoint + "/healthz",
            "clientProbe": {"path": "/healthz", "method": "GET", "timeoutMs": 2000},
            "congestion": {"utilization": 0.2, "backlog": 1, "observedAt": stamp(now - 2)}}


def cielairouter_pcfg(**overrides):
    pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["cielairouter"])
    pcfg.update({"base_url": CENTRAL, "api_key": "router-key", "current_model": "ASTRA"})
    pcfg.update(overrides)
    return pcfg


class Recorder:
    def __init__(self):
        self.lines = []

    def __call__(self, level, message):
        self.lines.append((level, message))

    def named(self, name):
        return [line for line in self.lines if name in line[1]]


class Fixture:
    """A real selector over a mutable discovery document (per API key)."""

    def __init__(self, test):
        self.now = [1_000_000.0]
        self.nodes = {"router-key": [("ciel-router-01", CENTRAL, "degraded")]}
        self.discovery_calls = []
        self.failures = []
        self.service = CielAiRouterGatewayService(
            discovery=self.discovery, probe=self.probe, clock=self.clock, refresh_interval=5.0)
        original = self.service.record_failure

        def record_failure(selection, classification):
            self.failures.append((selection.endpoint, classification))
            return original(selection, classification)

        self.service.record_failure = record_failure
        self.log = Recorder()
        self.overlay = CielAiRouterGatewayOverlay(self.selector, log=self.log, clock=self.clock)
        use_gateway_overlay(self.overlay)
        test.addCleanup(use_gateway_overlay, None)

    def clock(self):
        return self.now[0]

    def selector(self):
        return self.service

    def probe(self, candidate, timeout):
        return ProbeResult(True, 0.01)

    def discovery(self, base, key, timeout):
        self.discovery_calls.append((base, key))
        rows = [node(name, endpoint, self.now[0], health=health)
                for name, endpoint, health in self.nodes.get(key, [])]
        return {"object": "list", "protocol_version": 1, "generated_at": stamp(self.now[0]), "data": rows}

    def apply(self, pcfg=None, *, session="", provider="cielairouter"):
        return self.overlay.apply(provider, cielairouter_pcfg() if pcfg is None else pcfg,
                                  path="/v1/messages", session=session)

    def route(self):
        routes = self.overlay.status()["routes"]
        assert len(routes) == 1, routes
        return routes[0]

    def cooldowns(self):
        return [scope["cooldowns"] for scope in self.service.status()]


def tls_eof_through_provider_urlopen(pcfg):
    """Send one request through provider_urlopen; every opener raises a TLS EOF."""

    error = urllib.error.URLError(ssl.SSLEOFError(8, "TLS EOF in violation of protocol"))
    request = urllib.request.Request(str(pcfg["base_url"]) + "/v1/messages", data=b"{}", method="POST")
    opened = []

    class Opener:
        def open(self, *args, **kwargs):
            opened.append("gateway-opener")
            raise error

    with mock.patch("urllib.request.urlopen", side_effect=error) as urlopen:
        with mock.patch("urllib.request.build_opener", return_value=Opener()):
            try:
                provider_network.provider_urlopen(request, 5, "cielairouter", pcfg, lambda *args: None)
            except urllib.error.URLError as raised:
                return raised, urlopen.call_count + len(opened)
    raise AssertionError("the transport error was not re-raised")


class CentralEqualGatewayStatusTests(unittest.TestCase):
    def test_the_selector_returns_the_central_node_as_a_degraded_fallback_candidate(self):
        fixture = Fixture(self)
        selection = fixture.service.select(CENTRAL, "router-key", "ASTRA")
        self.assertEqual(CENTRAL, selection.endpoint)
        self.assertIsNotNone(selection.candidate)
        self.assertEqual("reprobed fallback", selection.reason)
        self.assertTrue(selection.degraded)

    def test_status_reports_the_central_base_the_request_actually_uses(self):
        fixture = Fixture(self)
        pcfg = cielairouter_pcfg()
        routed = fixture.apply(pcfg)
        self.assertIs(pcfg, routed)
        self.assertNotIn(ROUTE_TOKEN_KEY, routed)
        self.assertNotIn(DISCOVERY_BASE_URL_KEY, routed)
        route = fixture.route()
        self.assertEqual("central", route["state"])
        self.assertEqual(CENTRAL, route["endpoint"])
        self.assertEqual(CENTRAL_REASON, route["reason"])
        self.assertEqual("reprobed fallback", route["selection_reason"])
        self.assertEqual("ciel-router-01", route["node_id"])
        self.assertTrue(route["degraded"])
        self.assertEqual(1, route["generation"])
        self.assertEqual("", fixture.overlay.status_line())
        self.assertEqual({}, dict(fixture.overlay._tokens))
        self.assertFalse(fixture.log.named("cielairouter_gateway_selected"))
        central_lines = fixture.log.named("cielairouter_gateway_central")
        self.assertEqual(1, len(central_lines))
        self.assertEqual("WARN", central_lines[0][0])
        self.assertIn("reason=" + CENTRAL_REASON, central_lines[0][1])
        self.assertIn("selection_reason=reprobed fallback", central_lines[0][1])
        self.assertIn("node=ciel-router-01", central_lines[0][1])
        self.assertNotIn("router-key", repr(fixture.log.lines) + repr(fixture.overlay.status()))

    def test_a_repeated_central_route_logs_once(self):
        fixture = Fixture(self)
        fixture.apply()
        fixture.apply()
        self.assertEqual(1, len(fixture.log.named("cielairouter_gateway_central")))

    def test_other_providers_with_the_same_base_are_untouched(self):
        fixture = Fixture(self)
        pcfg = {"base_url": CENTRAL, "api_key": "router-key"}
        self.assertIs(pcfg, fixture.apply(pcfg, provider="vllm"))
        self.assertEqual([], fixture.discovery_calls)
        self.assertEqual([], fixture.overlay.status()["routes"])


class CentralFailureIsNeverScoredTests(unittest.TestCase):
    def test_a_central_tls_eof_is_reraised_once_and_does_not_cool_central(self):
        fixture = Fixture(self)
        routed = fixture.apply(session="one")
        error, attempts = tls_eof_through_provider_urlopen(routed)
        self.assertIsInstance(error.reason, ssl.SSLEOFError)
        self.assertEqual(1, attempts)
        self.assertEqual([], fixture.failures)
        self.assertEqual([{}], fixture.cooldowns())
        self.assertFalse(fixture.log.named("cielairouter_gateway_failure"))

    def test_observation_ignores_a_central_request_without_a_route_token(self):
        fixture = Fixture(self)
        routed = fixture.apply(session="one")
        request = urllib.request.Request(CENTRAL + "/v1/messages", data=b"{}", method="POST")
        error = urllib.error.HTTPError(CENTRAL + "/v1/messages", 502, "bad gateway", {}, None)

        def fail():
            raise error

        with self.assertRaises(urllib.error.HTTPError) as raised:
            observed_gateway_open(request, routed, fail)
        self.assertIs(error, raised.exception)
        self.assertEqual([], fixture.failures)
        self.assertEqual([{}], fixture.cooldowns())


class CentralFallbackRefreshTests(unittest.TestCase):
    """A session on the central fallback asks the selector again on its next request."""

    def test_after_a_failed_central_fallback_the_next_request_reaches_a_recovered_gateway(self):
        fixture = Fixture(self)
        first = fixture.apply(session="one")
        self.assertEqual(CENTRAL, first["base_url"])
        tls_eof_through_provider_urlopen(first)
        fixture.nodes["router-key"] = [("ciel-router-01", CENTRAL, "degraded"), ("gw-2", GATEWAY, "healthy")]
        fixture.now[0] += 6  # past refresh_interval, far before availableUntil
        second = fixture.apply(session="one")
        self.assertEqual(GATEWAY, second["base_url"])
        self.assertEqual(CENTRAL, second[DISCOVERY_BASE_URL_KEY])
        self.assertIn(ROUTE_TOKEN_KEY, second)
        route = fixture.route()
        self.assertEqual("gateway", route["state"])
        self.assertEqual("ranked", route["reason"])
        self.assertEqual(2, route["generation"])
        self.assertNotIn("selection_reason", route)
        self.assertEqual([], fixture.failures)
        self.assertEqual([{}], fixture.cooldowns())

    def test_matches_the_central_no_route_session_semantics(self):
        fixture = Fixture(self)
        fixture.nodes["router-key"] = []
        first = fixture.apply(session="one")
        self.assertEqual(CENTRAL, first["base_url"])
        self.assertEqual("no healthy gateway", fixture.route()["reason"])
        self.assertEqual(0, fixture.overlay.status()["sessions"])
        fixture.nodes["router-key"] = [("gw-2", GATEWAY, "healthy")]
        fixture.now[0] += 6
        self.assertEqual(GATEWAY, fixture.apply(session="one")["base_url"])

    def test_a_central_fallback_is_not_pinned_to_the_session(self):
        fixture = Fixture(self)
        fixture.apply(session="one")
        self.assertEqual(0, fixture.overlay.status()["sessions"])

    def test_requests_within_the_refresh_interval_share_one_exploration(self):
        fixture = Fixture(self)
        for _ in range(3):
            self.assertEqual(CENTRAL, fixture.apply(session="one")["base_url"])
            fixture.now[0] += 1
        self.assertEqual(1, len(fixture.discovery_calls))
        self.assertEqual(1, fixture.route()["generation"])

    def test_a_gateway_session_pin_is_unchanged(self):
        fixture = Fixture(self)
        fixture.nodes["router-key"] = [("gw-2", GATEWAY, "healthy")]
        first = fixture.apply(session="one")
        self.assertEqual(GATEWAY, first["base_url"])
        self.assertEqual(1, fixture.overlay.status()["sessions"])
        fixture.nodes["router-key"] = [("gw-3", "https://gw-3.ezonebot.com", "healthy")]
        fixture.now[0] += 6
        self.assertEqual(GATEWAY, fixture.apply(session="one")["base_url"])
        self.assertEqual(1, len(fixture.discovery_calls))

    def test_a_real_gateway_fallback_is_still_observed_and_cooled_on_failure(self):
        fixture = Fixture(self)
        fixture.nodes["router-key"] = [("gw-2", GATEWAY, "degraded")]
        routed = fixture.apply(session="one")
        self.assertEqual(GATEWAY, routed["base_url"])
        self.assertEqual("reprobed fallback", fixture.route()["reason"])
        self.assertEqual("gateway", fixture.route()["state"])
        error, attempts = tls_eof_through_provider_urlopen(routed)
        self.assertIsInstance(error.reason, ssl.SSLEOFError)
        self.assertEqual(1, attempts)
        self.assertEqual([(GATEWAY, "connection")], fixture.failures)
        self.assertIn(GATEWAY, fixture.cooldowns()[0])
        self.assertNotIn(CENTRAL, fixture.cooldowns()[0])


class CentralFallbackScopeTests(unittest.TestCase):
    def test_key_and_model_scopes_stay_separate_and_discovery_stays_central(self):
        fixture = Fixture(self)
        fixture.nodes["other-key"] = [("gw-2", GATEWAY, "healthy")]
        self.assertEqual(CENTRAL, fixture.apply(session="a")["base_url"])
        other = fixture.apply(cielairouter_pcfg(api_key="other-key"), session="b")
        self.assertEqual(GATEWAY, other["base_url"])
        self.assertEqual("other-key", other["api_key"])
        nova = fixture.apply(cielairouter_pcfg(current_model="NOVA"), session="c")
        self.assertEqual(CENTRAL, nova["base_url"])
        self.assertEqual(3, len(fixture.service.status()))
        self.assertEqual([(CENTRAL, "router-key"), (CENTRAL, "other-key"), (CENTRAL, "router-key")],
                         fixture.discovery_calls)
        self.assertEqual([{}, {}, {}], fixture.cooldowns())
        fixture.now[0] += 6
        self.assertEqual(GATEWAY, fixture.apply(other, session="b")["base_url"])
        self.assertTrue(all(base == CENTRAL for base, key in fixture.discovery_calls))


if __name__ == "__main__":
    unittest.main()
