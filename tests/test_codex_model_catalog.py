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


if __name__ == "__main__":
    unittest.main()
