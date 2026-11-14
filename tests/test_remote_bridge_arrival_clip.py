"""Production-shaped remote-bridge requests: one open, clipped to the router-arrival bound, no replay.

lead-rulings-independent-review-sse-retry-20261113.md, "R2 amendments":
- O1: route through the real ``RemoteBridgeRoutingService.resolve``. It sets
  REMOTE_BRIDGE_CONFIG_MARKER, which disables replay, so the production effect of
  the bridge scope is the single open's socket timeout. Arrival 400 s ago with a
  300 s provider timeout leaves 140 s for that one open.
- O2: /v1/chat/completions and /v1/responses bridge passthroughs must see the
  active cielairouter scope for their timeout, while replay stays off. Other
  providers and non-bridge paths stay unchanged; a non-bridge open under an
  arrival scope gets the same clipped timeout before and after the edit.
"""
import io
import unittest
import urllib.error
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support import openai_chat_passthrough as chat_module
from ciel_runtime_support import provider_responses_passthrough as responses_module
from ciel_runtime_support import transient_retry as retry
from ciel_runtime_support.openai_chat_passthrough import OpenAIChatPassthrough, OpenAIChatPassthroughPorts
from ciel_runtime_support.provider_responses_passthrough import (
    ProviderResponsesPassthrough, ProviderResponsesPassthroughPorts,
)
from ciel_runtime_support.remote_bridge import (
    REMOTE_BRIDGE_CONFIG_MARKER, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, RemoteBridgeRoutingService,
)
from test_transient_retry_preoutput_bound import ERROR, Clock

ARRIVAL_AGO, PROVIDER_TIMEOUT = 400.0, 300.0


def marked_route(provider="cielairouter", path="/v1/chat/completions"):
    """The production route: the real resolver adds the bridge marker."""
    service = RemoteBridgeRoutingService(normalize_provider=lambda name: name,
                                         parse_bool=lambda value, default: bool(value), environ={})
    config = {"current_provider": provider, "providers": {provider: {"base_url": "http://gateway.test",
                                                                       "current_model": "m"}}}
    return service.resolve(config, {}, {"model": "m", "messages": [], "input": []}, path)


def bridge_handler():
    handler = SimpleNamespace(headers={}, wfile=io.BytesIO(), send_response=mock.Mock(), end_headers=mock.Mock())
    setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)
    return handler


class Opens:
    """Records each open's socket timeout; every open answers 503."""

    def __init__(self):
        self.timeouts = []

    def __call__(self, request, *, timeout, provider, pcfg):
        self.timeouts.append(timeout)
        raise urllib.error.HTTPError("http://gateway.test", 503, "x", {}, io.BytesIO(ERROR))


def chat_route(opens):
    return OpenAIChatPassthrough(OpenAIChatPassthroughPorts(
        normalize_model=lambda p, c, m: m, normalize_request=lambda p, c, b: b,
        upstream_base=lambda p, c: c["base_url"], join_url=lambda b, p: b + p, headers=lambda *a: {},
        urlopen=opens, timeout_seconds=lambda c: PROVIDER_TIMEOUT, copy_response_headers=lambda *a: None))


def responses_route(opens):
    return ProviderResponsesPassthrough(ProviderResponsesPassthroughPorts(
        project_channel_context=lambda body: (body, {}), begin_channel_delivery=mock.Mock(),
        normalize_model=lambda p, c, m: m, normalize_request=lambda p, c, b: b,
        upstream_base=lambda p, c: c["base_url"], join_url=lambda b, p: b + p, headers=lambda *a: {},
        urlopen=opens, timeout_seconds=lambda c: PROVIDER_TIMEOUT, copy_response_headers=lambda *a: None))


class BridgeArrivalClipTests(unittest.TestCase):
    def run_in_scope(self, provider, forward, *, scoped=True):
        """Like router_http's bridge branch: an arrival-stamped scope, entered for cielairouter only."""
        clock = Clock()
        arrival = clock.now
        clock.now += ARRIVAL_AGO
        real = retry._default_budget

        def clocked(config, name, *, arrival_started=None):
            budget = real(config, name, arrival_started=arrival_started)
            if budget is not None:
                budget.clock, budget.rand, budget.started = clock, (lambda: 0.0), clock()
            return budget

        with mock.patch.object(retry, "RETRY_WINDOW_SECONDS", 420.0), \
                mock.patch.object(retry, "_default_budget", clocked):
            if scoped and provider == "cielairouter":
                route = marked_route(provider)
                with retry.request_scope(route.provider_config, provider, arrival_started=arrival):
                    forward(route.provider, route.provider_config)
            else:
                route = marked_route(provider)
                forward(route.provider, route.provider_config)

    def assert_one_clipped_open(self, opens, expected):
        self.assertEqual(1, len(opens.timeouts), opens.timeouts)  # no replay
        self.assertAlmostEqual(expected, opens.timeouts[0], places=6)

    def test_production_route_carries_the_bridge_marker(self):
        route = marked_route()
        self.assertIs(True, route.provider_config.get(REMOTE_BRIDGE_CONFIG_MARKER))

    def test_chat_bridge_open_is_clipped_to_the_arrival_bound(self):
        opens = Opens()
        self.run_in_scope("cielairouter", lambda provider, config: chat_route(opens).forward(
            bridge_handler(), provider, config, {"model": "m", "messages": []}))
        self.assert_one_clipped_open(opens, 540.0 - ARRIVAL_AGO)

    def test_responses_bridge_open_is_clipped_to_the_arrival_bound(self):
        opens = Opens()

        def forward(provider, config):
            with self.assertRaises(Exception):
                responses_route(opens).forward(bridge_handler(), provider, config, {"model": "m", "input": []})

        self.run_in_scope("cielairouter", forward)
        self.assert_one_clipped_open(opens, 540.0 - ARRIVAL_AGO)

    def test_other_provider_bridge_keeps_its_own_timeout(self):
        opens = Opens()
        self.run_in_scope("deepseek", lambda provider, config: chat_route(opens).forward(
            bridge_handler(), provider, config, {"model": "m", "messages": []}))
        self.assert_one_clipped_open(opens, PROVIDER_TIMEOUT)

    def test_bridge_without_a_scope_keeps_its_own_timeout(self):
        opens = Opens()
        self.run_in_scope("cielairouter", lambda provider, config: chat_route(opens).forward(
            bridge_handler(), provider, config, {"model": "m", "messages": []}), scoped=False)
        self.assert_one_clipped_open(opens, PROVIDER_TIMEOUT)

    def test_non_bridge_open_timeout_under_an_arrival_scope_is_unchanged(self):
        """Non-bridge requests already passed ``provider`` as retry_provider: O2 must be a no-op here.

        The same assertion passes on the pre-edit and the post-edit passthroughs.
        """
        config = {"base_url": "http://gateway.test", "current_model": "m"}
        plain = lambda: SimpleNamespace(headers={}, wfile=io.BytesIO(),  # noqa: E731 - no bridge attribute
                                        send_response=mock.Mock(), end_headers=mock.Mock())

        def chat(opens):
            chat_route(opens).forward(plain(), "cielairouter", config, {"model": "m", "messages": []})

        def responses(opens):
            with self.assertRaises(Exception):
                responses_route(opens).forward(plain(), "cielairouter", config, {"model": "m", "input": []})

        for name, forward in (("chat", chat), ("responses", responses)):
            with self.subTest(path=name):
                opens, clock = Opens(), Clock()
                arrival = clock.now
                clock.now += ARRIVAL_AGO
                real = retry._default_budget

                def clocked(cfg, provider, *, arrival_started=None, _real=real, _clock=clock):
                    budget = _real(cfg, provider, arrival_started=arrival_started)
                    if budget is not None:
                        budget.clock, budget.rand, budget.started = _clock, (lambda: 0.0), _clock()
                    return budget

                real_open = retry.open_with_transient_retry  # its sleep default is bound at definition
                fake_sleep = lambda *a, _o=real_open, _s=clock.sleep, **kw: _o(*a, sleep=_s, **kw)  # noqa: E731
                with mock.patch.object(retry, "RETRY_WINDOW_SECONDS", 420.0), \
                        mock.patch.object(retry, "_default_budget", clocked), \
                        mock.patch.object(chat_module, "open_with_transient_retry", fake_sleep), \
                        mock.patch.object(responses_module, "open_with_transient_retry", fake_sleep), \
                        retry.request_scope(config, "cielairouter", arrival_started=arrival):
                    forward(opens)
                self.assertTrue(opens.timeouts)
                self.assertAlmostEqual(540.0 - ARRIVAL_AGO, opens.timeouts[0], places=6)
                self.assertTrue(all(timeout <= 540.0 - ARRIVAL_AGO + 1e-9 for timeout in opens.timeouts),
                                opens.timeouts)

    def test_non_bridge_chat_still_retries_under_its_scope(self):
        opens = Opens()
        handler = SimpleNamespace(headers={}, wfile=io.BytesIO(), send_response=mock.Mock(), end_headers=mock.Mock())
        clock = Clock()
        with mock.patch.object(retry, "RETRY_WINDOW_SECONDS", 0.0):
            config = {"base_url": "http://gateway.test", "gateway_retries": 2}
            with retry.request_scope(config, "cielairouter"):
                with mock.patch.object(retry.time, "sleep", clock.sleep):
                    chat_route(opens).forward(handler, "cielairouter", config, {"model": "m", "messages": []})
        self.assertEqual(3, len(opens.timeouts))  # 1 open + 2 replays: count mode unchanged


if __name__ == "__main__":
    unittest.main()
