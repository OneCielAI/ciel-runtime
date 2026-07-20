import unittest

from ciel_runtime_support.provider_endpoint_probe import (
    ProviderEndpointProbePolicy,
    ProviderEndpointProbeProjection,
    ProviderEndpointProbeQueries,
    ProviderEndpointRouteAdapter,
    ProviderEndpointRoutePorts,
)


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _HttpError(Exception):
    def __init__(self, code):
        self.code = code

    def read(self):
        return b""


class ProviderEndpointProbeTests(unittest.TestCase):
    def policy(self, route_exists):
        return ProviderEndpointProbePolicy(
            projection=ProviderEndpointProbeProjection(
                upstream_base=lambda _provider, config: config.get(
                    "base_url", ""
                ),
                native_base=lambda _provider, config: config.get(
                    "native_base", ""
                ),
                join_url=lambda base, path: base.rstrip("/") + path,
            ),
            query=ProviderEndpointProbeQueries(
                primary_headers=lambda _provider, _config: {"primary": "1"},
                fallback_headers=lambda _provider, _config: {"fallback": "1"},
                route_exists=route_exists,
            ),
        )

    def test_route_adapter_classifies_http_status(self):
        adapter = ProviderEndpointRouteAdapter(
            ProviderEndpointRoutePorts(
                decorate_headers=lambda headers: headers,
                request=lambda *args, **kwargs: (args, kwargs),
                urlopen=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    _HttpError(404)
                ),
                http_error=_HttpError,
            )
        )
        self.assertFalse(adapter.exists("https://example.test", {}))

    def test_route_adapter_reports_success(self):
        adapter = ProviderEndpointRouteAdapter(
            ProviderEndpointRoutePorts(
                decorate_headers=lambda headers: headers,
                request=lambda *args, **kwargs: (args, kwargs),
                urlopen=lambda *_args, **_kwargs: _Response(),
                http_error=_HttpError,
            )
        )
        self.assertTrue(adapter.exists("https://example.test", {}))

    def test_detect_uses_fallback_headers_and_classifies_openai_only(self):
        calls = []

        def route_exists(url, headers, _timeout):
            calls.append((url, headers))
            return not url.endswith("/v1/messages")

        result = self.policy(route_exists).detect_native_compat(
            "local",
            {
                "base_url": "http://local/v1",
                "native_base": "http://local",
            },
            frozenset({"local"}),
        )
        self.assertEqual(
            (False, "OpenAI chat completions route detected"),
            result,
        )
        self.assertTrue(all(headers == {"fallback": "1"} for _, headers in calls))

    def test_report_bounds_timeout_and_projects_status(self):
        calls = []

        def route_exists(url, _headers, timeout):
            calls.append((url, timeout))
            return True

        lines = self.policy(route_exists).report(
            "remote",
            {
                "base_url": "https://remote/v1",
                "native_base": "https://remote",
            },
            frozenset(),
            timeout=99,
        )
        self.assertEqual([3.0, 3.0], [timeout for _, timeout in calls])
        self.assertIn("available", lines[1])


if __name__ == "__main__":
    unittest.main()
