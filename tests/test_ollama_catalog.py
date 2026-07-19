import unittest
from unittest import mock

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

    def test_refresh_catalog_groups_tags_and_projects_context_policy(self):
        saved = mock.Mock()
        services = ollama_catalog.OllamaCatalogRefreshServices(
            load_catalog=lambda: {},
            fetch_catalog=lambda _url, **_kwargs: {
                "models": [{"name": "qwen3.6:27b"}, {"name": "qwen3.6:35b"}]
            },
            fetch_context_map=lambda _base, **_kwargs: (
                {"27b": 256 * 1024, "35b": 1024 * 1024},
                "test-source",
            ),
            save_catalog=saved,
            positive_int=lambda value: int(value) if int(value) > 0 else None,
            now=lambda: 10.0,
        )

        catalog = ollama_catalog.refresh_model_catalog(services)

        entry = catalog["models"]["qwen3.6"]
        self.assertEqual(["27b", "35b"], entry["tags"])
        self.assertEqual(1024 * 1024, entry["context_window"])
        self.assertEqual(300_000, entry["recommended_timeout_ms"])
        saved.assert_called_once_with(catalog)

    def test_refresh_without_context_fetch_preserves_cached_windows(self):
        fetch_context = mock.Mock()
        services = ollama_catalog.OllamaCatalogRefreshServices(
            load_catalog=lambda: {
                "models": {
                    "qwen3.6": {
                        "context_windows": {"27b": 262144},
                        "context_window": 262144,
                        "context_source": "cached",
                    }
                }
            },
            fetch_catalog=lambda _url, **_kwargs: {"models": [{"name": "qwen3.6:27b"}]},
            fetch_context_map=fetch_context,
            save_catalog=mock.Mock(),
            positive_int=lambda value: int(value) if value else None,
        )

        catalog = ollama_catalog.refresh_model_catalog(services, include_contexts=False)

        self.assertEqual(262144, catalog["models"]["qwen3.6"]["context_window"])
        fetch_context.assert_not_called()


if __name__ == "__main__":
    unittest.main()
