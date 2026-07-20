import unittest
from unittest import mock

from ciel_runtime_support.model_registry_repository import (
    ModelRegistryApi,
    ModelRegistryRepository,
)


class ModelRegistryApiTests(unittest.TestCase):
    def test_explicit_api_preserves_default_ttl_and_write_contract(self):
        repository = mock.create_autospec(ModelRegistryRepository, instance=True)
        repository.read_registry.return_value = {"models": ["model"]}
        api = ModelRegistryApi(lambda: repository)
        config = {"current_model": "model"}

        self.assertEqual(
            {"models": ["model"]},
            api.read_registry(provider="test", pcfg=config),
        )
        api.write_registry(
            provider="test",
            pcfg=config,
            models=["model"],
            source="provider",
            metadata={"model_info": {}},
        )
        repository.read_registry.assert_called_once_with("test", config, 300)
        repository.write_registry.assert_called_once_with(
            "test", config, ["model"], "provider", {"model_info": {}}
        )


if __name__ == "__main__":
    unittest.main()
