import unittest

from ciel_runtime_support.provider_adapters import PROVIDER_ADAPTERS, PROVIDER_LABELS
from ciel_runtime_support.provider_model_identity import (
    ProviderModelIdentityApi,
    ProviderModelIdentityService,
)
from ciel_runtime_support.runtime_constants import PROVIDER_ALIASES


class ProviderModelIdentityServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = ProviderModelIdentityService(
            adapters=PROVIDER_ADAPTERS,
            aliases=PROVIDER_ALIASES,
            labels=PROVIDER_LABELS,
        )

    def test_normalizes_provider_aliases_and_rejects_unknown_names(self):
        self.assertEqual("openrouter", self.service.normalize_provider("Open Router"))
        with self.assertRaisesRegex(SystemExit, "Unknown provider: missing"):
            self.service.normalize_provider("missing")

    def test_normalizes_deduplicates_and_sorts_model_ids(self):
        self.assertEqual(
            ["Model-B", "model-a"],
            self.service.unique_ids("openrouter", [" Model-B ", "model-a", "MODEL-A"]),
        )
        self.assertEqual(["model-a", "Model-B"], self.service.sorted_ids(["Model-B", "model-a"]))

    def test_alias_round_trip_preserves_context_suffix_compatibility(self):
        alias = self.service.alias_for("openrouter", "vendor/model one")
        self.assertEqual("ciel-runtime-openrouter-vendor-model-one", alias)
        self.assertEqual(
            "vendor/model one",
            self.service.unslug_alias(
                "openrouter",
                alias + "[1m]",
                {alias: "vendor/model one"},
            ),
        )

    def test_provider_adapter_owns_specialized_display_name(self):
        self.assertEqual(
            "Nvidia Nemotron Ultra",
            self.service.display_name("nvidia-hosted", "claude-nvidia-nemotron-ultra"),
        )
        self.assertEqual(
            "vLLM Vendor Model",
            self.service.display_name("vllm", "vendor/model"),
        )

    def test_explicit_api_preserves_public_keyword_contract(self):
        api = ProviderModelIdentityApi(self.service)
        model_map = {
            "ciel-runtime-openrouter-vendor-model": "vendor/model",
        }

        self.assertEqual("openrouter", api.normalize_provider(name="Open Router"))
        self.assertEqual("vendor-model", api.slug(s="Vendor/Model"))
        self.assertEqual(("model", "Model"), api.model_sort_key(model_id="Model"))
        self.assertEqual(["a", "B"], api.sorted_model_ids(ids=["B", "a"]))
        self.assertEqual(
            ["model"], api.unique_model_ids(provider="openrouter", ids=["model"])
        )
        self.assertEqual(
            "vendor/model",
            api.normalize_model_id(provider="openrouter", model_id=" vendor/model "),
        )
        self.assertEqual(
            "model", api.strip_claude_context_suffix(model_id="model[1m]")
        )
        self.assertEqual(
            "vendor/model",
            api.upstream_api_model_id(provider="openrouter", model_id="vendor/model"),
        )
        alias = api.alias_for(provider="openrouter", model_id="vendor/model")
        self.assertEqual(
            "vendor/model",
            api.unslug_provider_alias(
                provider="openrouter", alias=alias, model_map=model_map
            ),
        )
        self.assertEqual(
            "Openrouter Vendor Model",
            api.display_name(provider="openrouter", model_id="vendor/model"),
        )


if __name__ == "__main__":
    unittest.main()
