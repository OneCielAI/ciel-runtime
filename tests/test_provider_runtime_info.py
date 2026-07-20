import unittest
from unittest import mock

from ciel_runtime_support.provider_runtime_info import ProviderRuntimeInfoPorts, ProviderRuntimeInfoService


class ProviderRuntimeInfoServiceTests(unittest.TestCase):
    def service(self, **overrides):
        values = {
            "strategy": lambda _provider: "generic",
            "lm_studio_info": lambda *_args, **_kwargs: None,
            "request_base": lambda _provider, _config: "http://runtime/v1",
            "current_model": lambda _provider, _config: "selected",
            "http_json": lambda *_args, **_kwargs: {
                "data": [
                    {"id": "fallback", "context_length": 1024},
                    {"id": "selected", "details": {"max_context_length": "8192"}, "owned_by": "local"},
                ]
            },
            "join_url": lambda base, path: base.rstrip("/") + "/" + path.lstrip("/"),
            "model_headers": lambda _provider, _config: {"x": "1"},
            "positive_int": lambda value: int(value) if value else None,
            "log": mock.Mock(),
        }
        values.update(overrides)
        return ProviderRuntimeInfoService(ProviderRuntimeInfoPorts(**values))

    def test_model_context_reads_top_level_dotted_and_nested_fields(self):
        self.assertEqual(4096, ProviderRuntimeInfoService.model_context({"max_model_len": "4096"}))
        self.assertEqual(2048, ProviderRuntimeInfoService.model_context({"runtime.context_length": 2048}))
        self.assertEqual(8192, ProviderRuntimeInfoService.model_context({"details": {"contextLength": 8192}}))

    def test_discover_selects_current_runtime_model(self):
        info = self.service().discover("vllm", {})
        self.assertEqual("selected", info["runtime_model"])
        self.assertEqual(8192, info["max_model_len"])
        self.assertEqual("local", info["owned_by"])

    def test_lm_studio_strategy_short_circuits_generic_catalog(self):
        http_json = mock.Mock()
        service = self.service(
            strategy=lambda _provider: "lm_studio",
            lm_studio_info=lambda *_args, **_kwargs: {"runtime_model": "loaded", "max_model_len": 32768},
            http_json=http_json,
        )
        self.assertEqual("loaded", service.discover("lm-studio", {})["runtime_model"])
        http_json.assert_not_called()

    def test_http_failure_is_logged_and_returns_no_info(self):
        log = mock.Mock()
        service = self.service(http_json=mock.Mock(side_effect=OSError("offline")), log=log)
        self.assertIsNone(service.discover("vllm", {}))
        self.assertIn("provider_runtime_info_failed", log.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
