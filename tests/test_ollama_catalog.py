import unittest

from ciel_runtime_support import ollama_catalog


class OllamaCatalogPolicyTests(unittest.TestCase):
    def test_context_parser_extracts_tag_specific_windows(self):
        page = "qwen3.6:27b <div>256K context window</div> qwen3.6:35b <div>1M context window</div>"
        self.assertEqual(
            {"27b": 256 * 1024, "35b": 1024 * 1024},
            ollama_catalog.parse_library_context_map(page, "qwen3.6"),
        )

    def test_catalog_context_prefers_requested_tag(self):
        catalog = {
            "source": "test",
            "models": {
                "qwen3.6": {
                    "id": "qwen3.6",
                    "context_windows": {"27b": 262144, "latest": 131072},
                }
            },
        }
        context, matched, source = ollama_catalog.catalog_context_for_model(
            catalog,
            "qwen3.6:27b",
            lambda model: [model],
        )
        self.assertEqual((262144, "qwen3.6:27b", "test"), (context, matched, source))

    def test_context_update_is_immutable(self):
        original = {"models": {}}
        updated = ollama_catalog.with_updated_context(
            original,
            "qwen3.6:27b",
            262144,
            "qwen3.6:27b",
            "https://ollama.com/library/qwen3.6/tags",
            now=10.0,
        )
        self.assertEqual({}, original["models"])
        self.assertEqual(262144, updated["models"]["qwen3.6"]["context_windows"]["27b"])
        self.assertEqual(10.0, updated["updated_at"])

    def test_catalog_staleness_accepts_injected_clock(self):
        catalog = {"updated_at": 100.0, "models": {}}
        self.assertFalse(ollama_catalog.catalog_is_stale(catalog, 60, now=159.0))
        self.assertTrue(ollama_catalog.catalog_is_stale(catalog, 60, now=161.0))


if __name__ == "__main__":
    unittest.main()
