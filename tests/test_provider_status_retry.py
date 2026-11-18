import json
import socket
import ssl
import unittest
import urllib.error
from dataclasses import replace
from unittest import mock

from ciel_runtime_support.architecture import ProviderConfig, ProviderStatusPolicy
from ciel_runtime_support.provider_adapters import PROVIDER_ADAPTERS
from ciel_runtime_support.provider_readiness import (
    ProviderReadinessCapabilities, ProviderReadinessLmStudio,
    ProviderReadinessMode, ProviderReadinessServices, launch_readiness_errors,
)
from ciel_runtime_support.provider_status import (
    ProviderStatusCatalog, ProviderStatusGeneric, ProviderStatusRouting,
    ProviderStatusServices, base_url_status_line,
)


class ProviderStatusRetryTests(unittest.TestCase):
    def setUp(self):
        self.pcfg = {"base_url": "https://probe.invalid", "api_key": "test-key"}
        self.config = ProviderConfig(name="cielairouter", base_url=self.pcfg["base_url"], model="test", options=self.pcfg)
        self.adapter = PROVIDER_ADAPTERS.create("cielairouter")
        self.policy = self.adapter.status_policy(self.config)
        self.urlopen = mock.Mock()
        self.http_json = mock.Mock(return_value={"data": [{"id": "test"}]})
        self.context = mock.Mock(return_value=None)
        self.model_ids = mock.Mock(side_effect=lambda data: [row["id"] for row in data["data"]])
        self.services = ProviderStatusServices(
            routing=ProviderStatusRouting(mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock(), "http://127.0.0.1"),
            catalog=ProviderStatusCatalog(
                mock.Mock(return_value={"authorization": "Bearer test-key"}), self.http_json,
                lambda base, path: base + path, mock.Mock(return_value="https://management.invalid"), self.model_ids,
            ),
            generic=ProviderStatusGeneric(
                mock.Mock(return_value="test-key"), mock.Mock(return_value=True),
                lambda headers: headers, self.urlopen, self.context,
            ),
        )

    def response(self, data=None, *, read_error=None):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(data if data is not None else {"data": [{"id": "test"}]}).encode()
        if read_error is not None:
            response.read.side_effect = read_error
        return response

    def status(self, policy=None):
        return base_url_status_line("cielairouter", self.pcfg, policy or self.policy, services=self.services)

    def assert_timeouts(self, port, expected):
        self.assertEqual(expected, [call.kwargs["timeout"] for call in port.call_args_list])
        for call in port.call_args_list:
            self.assertEqual("cielairouter", call.kwargs["provider"])
            self.assertIs(self.pcfg, call.kwargs["pcfg"])
            if port is self.urlopen:
                self.assertEqual("GET", call.args[0].get_method())
                self.assertEqual("Bearer test-key", call.args[0].get_header("Authorization"))

    def catalog_policy(self):
        return replace(self.policy, kind="catalog", label="Probe")

    def test_retry_policy_is_owned_only_by_cielairouter(self):
        self.assertEqual(0.0, ProviderStatusPolicy().probe_retry_timeout_seconds)
        self.assertEqual(10.0, self.policy.probe_timeout_seconds)
        self.assertEqual(30.0, self.policy.probe_retry_timeout_seconds)
        for provider in PROVIDER_ADAPTERS.names():
            with self.subTest(provider=provider):
                config = ProviderConfig(name=provider, base_url="https://probe.invalid", model="test", options={})
                policy = PROVIDER_ADAPTERS.create(provider).status_policy(config)
                self.assertEqual(30.0 if provider == "cielairouter" else 0.0, policy.probe_retry_timeout_seconds)

    def test_initial_success_does_not_retry(self):
        for catalog in (False, True):
            with self.subTest(catalog=catalog):
                self.urlopen.return_value = self.response()
                status = self.status(self.catalog_policy() if catalog else self.policy)
                self.assertIn("model list reachable", status)
                self.assertNotIn("retry", status)
                self.assert_timeouts(self.http_json if catalog else self.urlopen, [10.0])

    def test_timeout_retry_success(self):
        for catalog in (False, True):
            for wrapped in (False, True):
                with self.subTest(catalog=catalog, wrapped=wrapped):
                    port = self.http_json if catalog else self.urlopen
                    port.reset_mock()
                    error = urllib.error.URLError(TimeoutError()) if wrapped else TimeoutError()
                    result = {"data": [{"id": "test"}]} if catalog else self.response()
                    port.side_effect = [error, result]
                    status = self.status(self.catalog_policy() if catalog else self.policy)
                    self.assertIn("model list reachable", status)
                    self.assertIn("retry succeeded (30s timeout)", status)
                    self.assert_timeouts(port, [10.0, 30.0])

    def test_both_timeout_attempts_remain_unreachable_and_bounded(self):
        for catalog in (False, True):
            for wrapped in (False, True):
                with self.subTest(catalog=catalog, wrapped=wrapped):
                    port = self.http_json if catalog else self.urlopen
                    port.reset_mock()
                    error = urllib.error.URLError(TimeoutError()) if wrapped else TimeoutError()
                    port.side_effect = error
                    status = self.status(self.catalog_policy() if catalog else self.policy)
                    self.assertIn("unreachable", status)
                    self.assertIn("model-list probe timeout (10s + 30s retry)", status)
                    self.assert_timeouts(port, [10.0, 30.0])
                    self.context.assert_not_called()

    def test_non_timeout_failures_never_retry(self):
        errors = [urllib.error.HTTPError("https://probe.invalid", code, "rejected", {}, None)
                  for code in (401, 403, 404, 429, 500, 503)]
        errors += [
            urllib.error.HTTPError("https://probe.invalid", 503, TimeoutError(), {}, None),
            urllib.error.URLError(socket.gaierror("DNS failure")),
            urllib.error.URLError(ssl.SSLCertVerificationError("certificate failure")),
            urllib.error.URLError("timed out"), ConnectionRefusedError(), ValueError("invalid response"),
        ]
        for catalog in (False, True):
            for error in errors:
                with self.subTest(catalog=catalog, error=repr(error)):
                    port = self.http_json if catalog else self.urlopen
                    port.reset_mock()
                    port.side_effect = error
                    status = self.status(self.catalog_policy() if catalog else self.policy)
                    self.assert_timeouts(port, [10.0])
                    self.assertNotIn("retry", status)
                    if isinstance(error, urllib.error.HTTPError):
                        self.assertIn(str(error.code), status)

    def test_retry_non_timeout_failure_is_not_retried_again(self):
        for catalog in (False, True):
            with self.subTest(catalog=catalog):
                port = self.http_json if catalog else self.urlopen
                port.side_effect = [TimeoutError(), urllib.error.URLError("DNS failure")]
                status = self.status(self.catalog_policy() if catalog else self.policy)
                self.assertIn("unreachable", status)
                self.assertNotIn("probe timeout", status)
                self.assert_timeouts(port, [10.0, 30.0])

    def test_default_policy_does_not_retry_timeouts(self):
        for catalog in (False, True):
            with self.subTest(catalog=catalog):
                port = self.http_json if catalog else self.urlopen
                port.side_effect = TimeoutError()
                status = self.status(replace(ProviderStatusPolicy(), kind="catalog" if catalog else "generic"))
                self.assertIn("unreachable", status)
                self.assertNotIn("retry", status)
                self.assert_timeouts(port, [2.5])

    def test_body_read_timeout_retries_then_preserves_header_evidence(self):
        self.urlopen.side_effect = [self.response(read_error=TimeoutError()), self.response(read_error=TimeoutError())]
        status = self.status()
        self.assertIn("unreachable", status)
        self.assertIn("HTTP response headers received; model-list body read timed out", status)
        self.assert_timeouts(self.urlopen, [10.0, 30.0])
        self.context.assert_not_called()

    def test_body_read_timeout_can_recover_on_retry(self):
        self.urlopen.side_effect = [self.response(read_error=TimeoutError()), self.response()]
        status = self.status()
        self.assertIn("model list reachable", status)
        self.assertIn("retry succeeded", status)
        self.assert_timeouts(self.urlopen, [10.0, 30.0])

    def test_final_connection_timeout_does_not_claim_headers_received(self):
        self.urlopen.side_effect = [self.response(read_error=TimeoutError()), TimeoutError()]
        status = self.status()
        self.assertIn("before response headers", status)
        self.assertNotIn("headers received", status)

    def test_large_catalog_is_fully_read(self):
        data = {"data": [{"id": str(index), "description": "x" * 1024} for index in range(150)]}
        response = self.response(data)
        self.assertGreater(len(response.read.return_value), 131072)
        self.urlopen.return_value = response
        self.assertIn("150 models", self.status())
        response.read.assert_called_once_with(1048577)

    def test_incomplete_catalog_fails_closed_without_retry(self):
        for data in ({}, {"data": None}, {"data": {}}, [], "not an object"):
            with self.subTest(data=data):
                self.urlopen.reset_mock()
                self.urlopen.return_value = self.response(data)
                self.assertIn("incomplete model catalog", self.status())
                self.assert_timeouts(self.urlopen, [10.0])
        self.context.assert_not_called()

    def test_malformed_catalog_fails_closed_without_retry(self):
        response = self.response()
        response.read.return_value = b'{"data": ['
        self.urlopen.return_value = response
        self.assertIn("unreachable (JSONDecodeError)", self.status())
        self.assert_timeouts(self.urlopen, [10.0])
        self.context.assert_not_called()

    def test_oversized_catalog_fails_closed_without_retry(self):
        response = self.response()
        response.read.return_value = b" " * 1048577
        self.urlopen.return_value = response
        self.assertIn("unreachable (ValueError)", self.status())
        response.read.assert_called_once_with(1048577)
        self.assert_timeouts(self.urlopen, [10.0])
        self.context.assert_not_called()

    def test_catalog_projection_failure_does_not_retry(self):
        self.model_ids.side_effect = ValueError("invalid catalog")
        self.assertIn("unreachable (ValueError)", self.status(self.catalog_policy()))
        self.assert_timeouts(self.http_json, [10.0])

    def test_context_error_preserves_parsed_catalog_reachability(self):
        for error in (TimeoutError(), urllib.error.URLError(TimeoutError()), ValueError()):
            with self.subTest(error=type(error).__name__):
                self.urlopen.reset_mock()
                self.urlopen.return_value = self.response()
                self.context.side_effect = error
                status = self.status()
                self.assertIn("model list reachable", status)
                self.assertIn(f"context unavailable ({type(error).__name__})", status)
                self.assertNotIn("unreachable", status)
                self.assert_timeouts(self.urlopen, [10.0])
        self.context.assert_called_with("cielairouter", self.pcfg, timeout=1.0)

    def test_context_error_preserves_retry_success(self):
        self.urlopen.side_effect = [TimeoutError(), self.response()]
        self.context.side_effect = TimeoutError()
        status = self.status()
        self.assertIn("context unavailable", status)
        self.assertIn("retry succeeded", status)
        self.assertNotIn("unreachable", status)

    def test_context_limit_success_is_preserved(self):
        self.urlopen.return_value = self.response()
        self.context.return_value = 123456
        self.assertIn("max_model_len 123456", self.status())

    def test_other_provider_keeps_preview_read_and_status(self):
        response = self.response()
        self.urlopen.return_value = response
        self.assertEqual("Base URL: model list reachable (/v1/models, 1 models)", self.status(ProviderStatusPolicy()))
        response.read.assert_called_once_with(131072)
        self.assert_timeouts(self.urlopen, [2.5])

    def test_launch_gate_blocks_exhausted_timeout_and_allows_recovered_catalog(self):
        readiness = ProviderReadinessServices(
            mode=ProviderReadinessMode(mock.Mock(return_value=False), mock.Mock(return_value=False), mock.Mock(return_value=False)),
            capabilities=ProviderReadinessCapabilities(mock.Mock(return_value=False), mock.Mock(), mock.Mock()),
            lm_studio=ProviderReadinessLmStudio(mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock(), 1),
            base_url_status=lambda provider, pcfg: self.status(),
        )
        adapter = mock.Mock()
        adapter.launch_api_key_error.return_value = None
        self.urlopen.side_effect = TimeoutError()
        errors = launch_readiness_errors({}, "cielairouter", self.pcfg, adapter, self.config, self.policy, services=readiness)
        self.assertTrue(errors[0].startswith("Launch blocked: Base URL: model list unreachable"))
        self.urlopen.reset_mock()
        self.urlopen.side_effect = [TimeoutError(), self.response()]
        self.context.side_effect = TimeoutError()
        self.assertEqual([], launch_readiness_errors({}, "cielairouter", self.pcfg, adapter, self.config, self.policy, services=readiness))
        self.assert_timeouts(self.urlopen, [10.0, 30.0])


if __name__ == "__main__":
    unittest.main()
