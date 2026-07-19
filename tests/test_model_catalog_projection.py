import unittest

from ciel_runtime_support.model_catalog_projection import (
    ModelCatalogProjectionServices,
    project_model_info,
)


class ModelCatalogProjectionTests(unittest.TestCase):
    def test_generic_projection_uses_adapter_metadata_strategy(self):
        services = ModelCatalogProjectionServices(
            normalize_model_id=lambda provider, model: f"{provider}:{model}" if model else "",
            model_context=lambda raw: raw.get("context"),
            positive_int=lambda value: int(value) if value else None,
            project_metadata=lambda raw: {"badge": raw["badge"]} if raw.get("badge") else {},
        )

        info = project_model_info(
            "provider",
            {"data": [{"id": "model-a", "context": 8192, "badge": "custom"}]},
            services,
        )

        self.assertEqual(
            {"provider:model-a": {"badge": "custom", "max_model_len": 8192}},
            info,
        )

    def test_projection_accepts_string_and_single_object_shapes(self):
        services = ModelCatalogProjectionServices(
            normalize_model_id=lambda _provider, model: model,
            model_context=lambda _raw: None,
            positive_int=lambda _value: None,
            project_metadata=lambda raw: {"owned_by": raw["owned_by"]} if raw.get("owned_by") else {},
        )

        self.assertEqual({}, project_model_info("provider", "model-a", services))
        self.assertEqual(
            {"model-b": {"owned_by": "team"}},
            project_model_info("provider", {"model": {"id": "model-b", "owned_by": "team"}}, services),
        )


if __name__ == "__main__":
    unittest.main()
