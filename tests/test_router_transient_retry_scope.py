"""Logical generation retry ownership regression tests."""
import copy
import io
import json
import unittest
import urllib.error
import urllib.request
from contextlib import nullcontext
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support.initial_stream_retry import InitialStreamRetry
from ciel_runtime_support.router_http import RouterHttpHandler
from ciel_runtime_support.transient_retry import (
    BUDGET_CONFIG_KEY, budget_for, open_with_transient_retry, retry_handled,
)
from ciel_runtime_support.upstream_overload_retry import OverloadRetryState
from test_codex_backend_item_repair import FakeHandler, adapter_for


class RetryScopeTests(unittest.TestCase):
    def setUp(self):
        self.config = {"gateway_retries": 2, "base_url": "https://upstream.invalid"}
        self.cfg = {"current_provider": "cielairouter", "providers": {"cielairouter": self.config}}
        self.services = SimpleNamespace(core=SimpleNamespace(
            log=lambda *_args: None, observe_runtime=lambda *_args: nullcontext()))
        self.handler = SimpleNamespace(_ciel_runtime_response_status=None)
        self.failover = SimpleNamespace(effective=lambda _cfg, provider, config, **_kw: (provider, config))
        self.overlay = SimpleNamespace(apply=lambda _provider, config, **_kw: config)
        self.body = {"model": "model", "messages": [{"role": "user", "content": "hi"}]}

    def dispatch(self, runtime, *, path="/v1/messages", provider="cielairouter"):
        with mock.patch("ciel_runtime_support.provider_failover.failover_service", return_value=self.failover):
            with mock.patch("ciel_runtime_support.cielairouter_gateway_overlay.gateway_overlay", return_value=self.overlay):
                return RouterHttpHandler._dispatch_with_failover(
                    self.handler, self.services, SimpleNamespace(runtime=runtime), self.cfg,
                    provider, self.config, path, self.body)

    @staticmethod
    def failure(raw=None, status=503):
        if raw is None:
            raw = json.dumps({"error": {"type": "server_error", "message": "overloaded"}}).encode()
        return urllib.error.HTTPError("https://upstream.invalid", status, "Rejected", {}, io.BytesIO(raw))

    def execute(self, open_once, config=None, provider="cielairouter"):
        return open_with_transient_retry(open_once, provider=provider,
            config=config or self.config, log=lambda *_args: None, sleep=lambda _seconds: None)

    def test_sequential_requests_have_independent_budgets_without_config_state(self):
        before = copy.deepcopy(self.cfg)
        budgets, counts = [], []
        def runtime(_handler, _cfg, provider, config, _path, _body):
            budgets.append(budget_for(config, provider))
            self.assertIsNotNone(budgets[-1])
            def fail():
                raise self.failure()
            upstream = mock.Mock(side_effect=fail)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.execute(upstream, config, provider)
            self.assertTrue(retry_handled(caught.exception))
            counts.append(upstream.call_count)
            return True
        self.assertTrue(self.dispatch(runtime))
        self.assertTrue(self.dispatch(runtime))
        self.assertEqual([3, 3], counts)
        self.assertIsNot(budgets[0], budgets[1])
        self.assertNotEqual(budgets[0].operation_id, budgets[1].operation_id)
        self.assertIsNone(budget_for(self.config, "cielairouter"))
        self.assertEqual(before, self.cfg)
        self.assertNotIn(BUDGET_CONFIG_KEY, self.config)

    def test_nested_transports_and_sequential_reopens_share_dispatch_allowance(self):
        counts, budgets = [], []
        def runtime(_handler, _cfg, provider, config, _path, _body):
            budget = budget_for(config, provider)
            budgets.append(budget)
            upstream = mock.Mock(side_effect=lambda: io.BytesIO(b"unconsumed"))
            def outer():
                budgets.append(budget_for(config, provider))
                return self.execute(upstream, config, provider)
            for _ in range(3):
                self.execute(outer, config, provider).close()
            with self.assertRaises(RuntimeError):
                self.execute(outer, config, provider)
            counts.append(upstream.call_count)
            self.assertEqual((2, 3), (budget.attempts, budget.opens))
            return True
        self.assertTrue(self.dispatch(runtime))
        self.assertEqual([3], counts)
        self.assertTrue(all(budget is budgets[0] for budget in budgets))

    def test_exposed_response_forbids_a_later_open(self):
        def runtime(*_args):
            upstream = mock.Mock(return_value=io.BytesIO(b"output"))
            self.assertEqual(b"output", self.execute(upstream).read())
            with self.assertRaises(RuntimeError) as caught:
                self.execute(upstream)
            self.assertTrue(retry_handled(caught.exception))
            self.assertEqual(1, upstream.call_count)
            return True
        self.assertTrue(self.dispatch(runtime))

    def test_non_generation_paths_and_other_providers_do_not_enter_scope(self):
        def runtime(_handler, _cfg, provider, config, _path, _body):
            self.assertIsNone(budget_for(config, provider))
            return True
        self.assertTrue(self.dispatch(runtime, path="/v1/models"))
        self.assertTrue(self.dispatch(runtime, path="/ca/other"))
        self.assertTrue(self.dispatch(runtime, provider="vllm"))

    def test_handled_errors_never_enter_codex_transport_or_overload_retries(self):
        for status, raw in [(503, b"malformed"), (401, b"unauthorized"), (503, None)]:
            for marker in ("_ciel_transient_retry_handled", "_ciel_transient_retry_exhausted"):
                with self.subTest(status=status, raw=raw, marker=marker):
                    error = self.failure(raw, status)
                    setattr(error, marker, True)
                    upstream = mock.Mock(side_effect=error)
                    adapter = adapter_for(upstream, [], transport_retries=5,
                                          classify_transport=lambda _error: True)
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        adapter.forward_json(FakeHandler(), "codex", {}, {"model": "m", "input": []})
                    self.assertIs(error, caught.exception)
                    self.assertEqual(1, upstream.call_count)
                    overload = OverloadRetryState()
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        adapter._retry_overload_or_raise(error, overload, "codex", "m")
                    self.assertIs(error, caught.exception)
                    self.assertEqual(0, overload.attempts)
                    if raw is not None:
                        self.assertEqual(raw, error.read())

    def test_cielairouter_ambiguous_codex_transport_failure_is_not_retried(self):
        error = ConnectionResetError("ambiguous request outcome")
        upstream = mock.Mock(side_effect=error)
        adapter = adapter_for(upstream, [], transport_retries=5, classify_transport=lambda _error: True)
        with self.assertRaises(ConnectionResetError) as caught:
            adapter._open_with_transport_retry(urllib.request.Request("https://upstream.invalid"),
                                               "cielairouter", self.config, "m", "responses")
        self.assertIs(error, caught.exception)
        self.assertEqual(1, upstream.call_count)

    def test_initial_stream_guard_preserves_handled_error_on_read_and_reopen(self):
        class Stream:
            def __init__(self, error):
                self.error = error
            def __iter__(self):
                raise self.error
                yield
            def close(self):
                pass
        for marker in ("_ciel_transient_retry_handled", "_ciel_transient_retry_exhausted"):
            error = OSError("retries already owned")
            setattr(error, marker, True)
            for raised_on_reopen in (False, True):
                with self.subTest(marker=marker, raised_on_reopen=raised_on_reopen):
                    reopen = mock.Mock(side_effect=error)
                    initial = Stream(OSError("initial")) if raised_on_reopen else Stream(error)
                    response = InitialStreamRetry(initial, reopen, 5, lambda _error: True,
                        lambda _attempt: 0, lambda _seconds: None, lambda *_args: None)
                    with self.assertRaises(OSError) as caught:
                        list(response)
                    self.assertIs(error, caught.exception)
                    self.assertEqual(int(raised_on_reopen), reopen.call_count)
