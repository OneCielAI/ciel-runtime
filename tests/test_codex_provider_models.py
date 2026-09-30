import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ciel_runtime
from ciel_runtime_support.providers.native import (
    CODEX_OPENAI_MODEL_IDS,
    CodexProviderAdapter,
)


GPT_6_CODEX_MODELS = ("gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna")


class CodexProviderModelTests(unittest.TestCase):
    def test_fresh_codex_configuration_offers_the_gpt_6_models(self):
        defaults = CodexProviderAdapter().default_configuration()

        self.assertEqual(GPT_6_CODEX_MODELS, CODEX_OPENAI_MODEL_IDS)
        self.assertEqual(list(GPT_6_CODEX_MODELS), defaults["custom_models"])

    def test_migration_adds_the_gpt_6_models_once_and_keeps_user_entries(self):
        cfg = {
            "migrations": {},
            "providers": {
                "codex": {
                    "current_model": "gpt-6-astra",
                    "custom_models": ["GPT-6-Sol", "team-private-model"],
                }
            },
        }

        ciel_runtime.apply_config_migrations(cfg)
        ciel_runtime.apply_config_migrations(cfg)

        codex = cfg["providers"]["codex"]
        self.assertEqual("gpt-6-astra", codex["current_model"])
        self.assertEqual(
            ["GPT-6-Sol", "team-private-model", "gpt-6.1-sol", "gpt-6-luna"],
            codex["custom_models"],
        )
        self.assertTrue(cfg["migrations"]["codex_gpt_6_sol_luna_model_ids_20260929"])

    def test_codex_model_list_shows_the_gpt_6_models(self):
        pcfg = {
            "current_model": "gpt-6-astra",
            "custom_models": [],
        }
        ciel_runtime.apply_config_migrations({"migrations": {}, "providers": {"codex": pcfg}})

        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            ciel_runtime, "MODEL_LIST_CACHE_PATH", Path(td) / "model-list-cache.json"
        ):
            models = ciel_runtime.upstream_model_ids("codex", pcfg, force_refresh=True)

        for model in (*GPT_6_CODEX_MODELS, "gpt-6-astra"):
            self.assertIn(model, models)


if __name__ == "__main__":
    unittest.main()
