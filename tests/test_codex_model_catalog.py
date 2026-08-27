import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from ciel_runtime_support.codex_model_catalog import (
    CodexModelCatalogService,
    CodexModelCatalogSpec,
)


class CodexModelCatalogServiceTests(unittest.TestCase):
    @staticmethod
    def _run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "models": [
                        {
                            "slug": "gpt-5.2",
                            "display_name": "GPT",
                            "context_window": 272000,
                        }
                    ]
                }
            ),
            stderr="",
        )

    def test_provider_model_snapshots_use_distinct_immutable_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = CodexModelCatalogService(
                Path(tmp), self._run, lambda _level, _message: None
            )
            kimi = service.write(
                "codex",
                CodexModelCatalogSpec(
                    alias="ciel-runtime-kimi-k3[1m]",
                    provider_label="Kimi",
                    context_window=1_048_576,
                    auto_compact_token_limit=891_289,
                ),
                {},
            )
            self.assertIsNotNone(kimi)
            kimi_payload = kimi.read_text(encoding="utf-8")

            other = service.write(
                "codex",
                CodexModelCatalogSpec(
                    alias="ciel-runtime-other-model",
                    provider_label="Other",
                    context_window=262_144,
                    auto_compact_token_limit=235_929,
                ),
                {},
            )

            self.assertIsNotNone(other)
            self.assertNotEqual(kimi, other)
            self.assertEqual(kimi_payload, kimi.read_text(encoding="utf-8"))
            self.assertTrue(kimi.exists())
            self.assertTrue(other.exists())

    def test_identical_concurrent_snapshots_share_content_address(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = CodexModelCatalogService(
                Path(tmp), self._run, lambda _level, _message: None
            )
            spec = CodexModelCatalogSpec(
                alias="ciel-runtime-kimi-k3[1m]",
                provider_label="Kimi",
                context_window=1_048_576,
                auto_compact_token_limit=891_289,
            )

            with ThreadPoolExecutor(max_workers=4) as executor:
                paths = list(
                    executor.map(
                        lambda _index: service.write("codex", spec, {}), range(8)
                    )
                )

            self.assertEqual(1, len(set(paths)))
            self.assertTrue(paths[0].exists())
            self.assertEqual([], list(paths[0].parent.glob("*.tmp")))

    def test_explicit_provider_reasoning_metadata_replaces_template_levels(self):
        bundled = {
            "models": [
                {
                    "slug": "gpt-5.2",
                    "display_name": "GPT",
                    "default_reasoning_level": "medium",
                    "supported_reasoning_levels": [
                        {"effort": "low", "description": "Low"},
                        {"effort": "medium", "description": "Medium"},
                        {"effort": "high", "description": "High"},
                        {"effort": "xhigh", "description": "Extra high"},
                    ],
                }
            ]
        }

        def run(*_args, **_kwargs):
            return SimpleNamespace(returncode=0, stdout=json.dumps(bundled), stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            service = CodexModelCatalogService(
                Path(tmp), run, lambda _level, _message: None
            )
            path = service.write(
                "codex",
                CodexModelCatalogSpec(
                    alias="ciel-runtime-ollama-cloud-kimi-k3",
                    provider_label="Ollama Cloud",
                    context_window=1_048_576,
                    effort="max",
                    metadata={
                        "default_reasoning_level": "xhigh",
                        "supported_reasoning_levels": [
                            {"effort": "low", "description": "Low"},
                            {"effort": "high", "description": "High"},
                            {"effort": "xhigh", "description": "Maximum"},
                        ],
                    },
                ),
                {},
            )
            model = next(
                item
                for item in json.loads(path.read_text(encoding="utf-8"))["models"]
                if item["slug"] == "ciel-runtime-ollama-cloud-kimi-k3"
            )

        self.assertEqual("xhigh", model["default_reasoning_level"])
        self.assertEqual(
            ["low", "high", "xhigh"],
            [item["effort"] for item in model["supported_reasoning_levels"]],
        )


if __name__ == "__main__":
    unittest.main()
