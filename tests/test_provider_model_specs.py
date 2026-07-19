import unittest
from unittest.mock import Mock

from ciel_runtime_support.architecture import ProviderContextPolicy
from ciel_runtime_support.provider_model_specs import (
    ModelSpecLookupPorts,
    ModelSpecMutationPorts,
    ModelSpecRefreshPorts,
    ProviderModelSpecService,
)


def positive_int(value):
    try:
        fixed = int(value)
    except (TypeError, ValueError):
        return None
    return fixed if fixed > 0 else None


class ProviderModelSpecServiceTests(unittest.TestCase):
    def service(self, *, strategy="standard", cache=None, refresh=None, preserve=False):
        self.apply_profile = Mock(return_value=["profile applied"])
        self.refresh_models = Mock(
            side_effect=refresh if isinstance(refresh, Exception) else None,
            return_value=[] if refresh is None or isinstance(refresh, Exception) else refresh,
        )
        return ProviderModelSpecService(
            ModelSpecLookupPorts(
                read_cache=lambda _provider, _config: cache or {},
                normalize_model=lambda _provider, model: model.strip().lower(),
                upstream_model=lambda _provider, config: str(config.get("upstream") or ""),
                strip_context_suffix=lambda model: model.removesuffix("[1m]"),
            ),
            ModelSpecMutationPorts(
                positive_int=positive_int,
                apply_model_profile=self.apply_profile,
                context_policy=lambda _provider, _config: ProviderContextPolicy(
                    settings_strategy=strategy
                ),
                ollama_model_matches=lambda left, right: left == right,
                preserve_ollama_cap=lambda _config: preserve,
                format_context=lambda value: f"{value // 1024}K" if value else "unknown",
            ),
            ModelSpecRefreshPorts(refresh_models=self.refresh_models),
        )

    def test_lookup_supports_configured_context_suffix_and_casefold(self):
        exact = {"max_model_len": 131072}
        service = self.service(cache={"model": exact})

        info = service.current_info(
            "provider",
            {"upstream": "missing", "current_model": "MODEL[1m]"},
        )

        self.assertIs(exact, info)

    def test_standard_strategy_projects_model_context(self):
        config = {"upstream": "model"}
        messages = self.service(
            cache={"model": {"max_model_len": 262144}}
        ).apply("vllm", config)

        self.assertEqual(262144, config["max_model_len"])
        self.assertEqual("profile applied", messages[0])
        self.assertIn("256K", messages[1])

    def test_ollama_strategy_preserves_an_explicit_smaller_cap(self):
        config = {"upstream": "model", "num_ctx_max": 65536}
        self.service(
            strategy="ollama",
            cache={"model": {"max_model_len": 262144}},
            preserve=True,
        ).apply("ollama", config)

        self.assertEqual(65536, config["num_ctx_max"])
        self.assertEqual(262144, config["model_context_max"])
        self.assertEqual("model", config["model_context_model"])

    def test_refresh_reports_catalog_size_then_applies_specs(self):
        config = {"upstream": "model"}
        messages = self.service(
            cache={"model": {"max_model_len": 131072}},
            refresh=["a", "b"],
        ).refresh("provider", config)

        self.refresh_models.assert_called_once_with(
            "provider", config, force_refresh=True
        )
        self.assertEqual("Model specs refreshed from provider: 2 model(s).", messages[0])
        self.assertEqual(131072, config["max_model_len"])

    def test_refresh_failure_is_isolated_before_cached_specs_apply(self):
        config = {"upstream": "model"}
        messages = self.service(
            cache={"model": {"max_model_len": 131072}},
            refresh=RuntimeError("offline"),
        ).refresh("provider", config)

        self.assertEqual(
            "Model specs refresh failed: RuntimeError: offline",
            messages[0],
        )
        self.assertEqual(131072, config["max_model_len"])


if __name__ == "__main__":
    unittest.main()
