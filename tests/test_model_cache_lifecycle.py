import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.model_cache_lifecycle import (
    ModelCacheLifecyclePorts,
    ModelCacheLifecycleService,
)


class ModelCacheLifecycleServiceTests(unittest.TestCase):
    def service(self, **overrides):
        values = {
            "invalidate_config": lambda: None,
            "artifact_paths": lambda: (),
            "read_list_cache": lambda _provider, _config: None,
            "read_registry_models": lambda _provider, _config, _age: None,
            "upstream_model_ids": lambda _provider, _config: [],
            "catalog_model_ids": lambda _provider: [],
            "normalize_model_id": lambda _provider, model_id: str(model_id).strip(),
            "unique_model_ids": lambda _provider, ids: list(dict.fromkeys(ids)),
            "sorted_model_ids": sorted,
            "log": lambda _level, _message: None,
        }
        values.update(overrides)
        return ModelCacheLifecycleService(ModelCacheLifecyclePorts(**values))

    def test_clear_invalidates_config_and_all_artifacts(self):
        invalidated = []
        with tempfile.TemporaryDirectory() as tmp:
            paths = tuple(Path(tmp, name) for name in ("gateway", "list", "registry"))
            for path in paths:
                path.write_text("cached", encoding="utf-8")
            service = self.service(
                invalidate_config=lambda: invalidated.append(True),
                artifact_paths=lambda: paths,
            )
            service.clear()
            self.assertEqual([True], invalidated)
            self.assertFalse(any(path.exists() for path in paths))

    def test_cached_ids_merge_cloud_catalog_custom_and_current_models(self):
        service = self.service(
            read_list_cache=lambda _provider, _config: ["cached"],
            catalog_model_ids=lambda _provider: ["catalog"],
        )
        result = service.cached_or_configured_ids(
            "ollama-cloud",
            {"custom_models": ["custom"], "current_model": "current"},
        )
        self.assertEqual(["cached", "catalog", "current", "custom"], result)

    def test_launch_hydration_uses_registry_before_upstream(self):
        upstream_calls = []
        service = self.service(
            read_registry_models=lambda _provider, _config, _age: ["cached"],
            upstream_model_ids=lambda provider, _config: upstream_calls.append(provider),
        )
        service.ensure_for_launch("anthropic", {})
        self.assertEqual([], upstream_calls)


if __name__ == "__main__":
    unittest.main()
