"""The router's arrival time reaches the dispatch's retry scope (continue-retry ruling 1 wiring).

``do_POST`` records ``time.monotonic()`` before config or body work and
``_dispatch_with_failover`` passes it to ``request_scope(...,
arrival_started=...)`` for the CielAiRouter generation scope only.
"""
import unittest
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support import router_http
from ciel_runtime_support.router_http import RouterHttpHandler


class ArrivalScopeTests(unittest.TestCase):
    def dispatch(self, provider, arrived):
        seen = []

        @contextmanager
        def scope(config, name, **kwargs):
            seen.append((name, kwargs))
            yield None

        services = SimpleNamespace(core=SimpleNamespace(log=lambda *_a: None,
                                                        observe_runtime=lambda *_a: nullcontext()))
        failover = SimpleNamespace(effective=lambda _cfg, name, config, **_kw: (name, config))
        overlay = SimpleNamespace(apply=lambda _name, config, **_kw: config)
        with mock.patch.object(router_http, "request_scope", scope), \
                mock.patch("ciel_runtime_support.provider_failover.failover_service", return_value=failover), \
                mock.patch("ciel_runtime_support.cielairouter_gateway_overlay.gateway_overlay", return_value=overlay):
            RouterHttpHandler._dispatch_with_failover(
                SimpleNamespace(_ciel_runtime_response_status=None), services,
                SimpleNamespace(runtime=lambda *_a: True), {}, provider, {"gateway_retries": 1},
                "/v1/messages", {"model": "m", "messages": []}, arrived)
        return seen

    def test_cielairouter_scope_gets_the_arrival_time(self):
        self.assertEqual([("cielairouter", {"arrival_started": 12.5})], self.dispatch("cielairouter", 12.5))

    def test_without_an_arrival_time_the_scope_call_is_unchanged(self):
        self.assertEqual([("cielairouter", {})], self.dispatch("cielairouter", None))

    def test_other_providers_open_no_scope(self):
        self.assertEqual([], self.dispatch("deepseek", 12.5))

    def test_do_post_captures_arrival_before_any_work(self):
        import inspect

        source = inspect.getsource(RouterHttpHandler.do_POST)
        first = [line.strip() for line in source.splitlines()[1:] if line.strip() and not line.strip().startswith("#")]
        self.assertEqual("request_arrived_at = time.monotonic()", first[0])
        self.assertIn("request_arrived_at", inspect.getsource(RouterHttpHandler.do_POST).split("_dispatch_with_failover(")[1])

    def test_the_real_request_scope_accepts_arrival_started(self):
        # Fails until transient_retry.request_scope takes the arrival_started keyword.
        from ciel_runtime_support.transient_retry import request_scope

        with request_scope({"gateway_retries": 1}, "cielairouter", arrival_started=0.0):
            pass


if __name__ == "__main__":
    unittest.main()
