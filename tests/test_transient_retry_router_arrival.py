"""The window's hard bound is measured from do_POST's arrival stamp, not from scope entry.

Team-lead ruling 1 (2026-11-13): the 540 s pre-output bound starts when the request
reaches the router. router_http.do_POST stamps time.monotonic() first (continue-pty,
router_http.py 35d34782); _dispatch_with_failover hands it to the real
transient_retry.request_scope. Here the real scope runs, with 100 s of simulated
body/config work between the stamp and the scope's construction.
"""
import time
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support import transient_retry as retry
from ciel_runtime_support.router_http import RouterHttpHandler


class RouterArrivalBoundTests(unittest.TestCase):
    def dispatch(self, arrived, window=420.0):
        seen = []

        def runtime(_handler, _cfg, provider, pcfg, _path, _body):
            budget = retry.budget_for(pcfg, provider)
            seen.append((budget.arrival_started, budget.started, budget.hard_remaining(), budget.remaining()))
            return True

        services = SimpleNamespace(core=SimpleNamespace(log=lambda *_a: None,
                                                        observe_runtime=lambda *_a: nullcontext()))
        failover = SimpleNamespace(effective=lambda _cfg, name, config, **_kw: (name, config))
        overlay = SimpleNamespace(apply=lambda _name, config, **_kw: config)
        with mock.patch.object(retry, "RETRY_WINDOW_SECONDS", window), \
                mock.patch("ciel_runtime_support.provider_failover.failover_service", return_value=failover), \
                mock.patch("ciel_runtime_support.cielairouter_gateway_overlay.gateway_overlay", return_value=overlay):
            RouterHttpHandler._dispatch_with_failover(
                SimpleNamespace(_ciel_runtime_response_status=None), services,
                SimpleNamespace(runtime=runtime), {}, "cielairouter", {},
                "/v1/messages", {"model": "m", "messages": []}, arrived)
        self.assertEqual(1, len(seen))
        return seen[0]

    def test_bound_counts_from_the_router_arrival_stamp(self):
        arrived = time.monotonic() - 100.0  # 100 s of body/config work before dispatch
        arrival, started, hard_left, left = self.dispatch(arrived)
        self.assertEqual(arrived, arrival)
        self.assertGreaterEqual(started - arrival, 100.0)
        self.assertAlmostEqual(440.0, hard_left, delta=2.0)
        self.assertAlmostEqual(420.0, left, delta=2.0)

    def test_without_a_stamp_the_bound_falls_back_to_scope_entry(self):
        arrival, started, hard_left, _ = self.dispatch(None)
        self.assertEqual(started, arrival)
        self.assertAlmostEqual(540.0, hard_left, delta=2.0)

    def test_window_off_has_no_arrival_bound(self):
        arrival, _, hard_left, _ = self.dispatch(time.monotonic() - 100.0, window=0.0)
        self.assertIsNone(arrival)
        self.assertEqual(float("inf"), hard_left)


if __name__ == "__main__":
    unittest.main()
