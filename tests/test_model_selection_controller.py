import unittest

from ciel_runtime_support.provider_model_selection import (
    ModelMutationConfigPorts,
    ModelMutationEffectPorts,
    ModelMutationPolicyPorts,
    ModelSelectionController,
)


class ModelSelectionControllerTests(unittest.TestCase):
    def test_selection_coordinates_provider_owned_updates_and_recommendations(self):
        provider_config = {"custom_models": []}
        config = {"language": "ko", "providers": {"test": provider_config}}
        saved = []
        cleared = []
        selection_updates = []
        controller = ModelSelectionController(
            ModelMutationConfigPorts(
                load_config=lambda: config,
                current_provider=lambda _config: ("test", provider_config),
                save_config=saved.append,
                clear_model_cache=lambda: cleared.append(True),
            ),
            ModelMutationPolicyPorts(
                model_map=lambda _provider, _config, fetch=False: {"alias": "model-a"},
                unslug=lambda _provider, value, mapping: mapping.get(value),
                normalize=lambda _provider, value: value.strip(),
                apply_profile=lambda _provider, _config: ["profile"],
                read_model_info=lambda _provider, _config: {"model-a": {"max_model_len": 65536}},
                positive_int=lambda value: int(value) if value else None,
                model_preset=lambda _model: {"num_ctx_min": 32768, "thinking": True},
                apply_selection_updates=lambda provider, _config, model: selection_updates.append((provider, model)),
                alias=lambda provider, model: f"{provider}:{model}",
                format_context=lambda value: f"{value // 1024}K",
            ),
            ModelMutationEffectPorts(
                sync_context_limit=lambda _provider, _config, _model: ["sync"],
                cap_context_settings=lambda _provider, _config: ["cap"],
                apply_recommended_preset=lambda _provider, _config, language: [f"preset:{language}"],
                apply_recommended_timeout=lambda _provider, _config, **_kwargs: ["timeout"],
                read_model_list=lambda _provider, _config: [],
            ),
        )

        messages = controller.select("alias")

        self.assertEqual("model-a", provider_config["current_model"])
        self.assertEqual(65536, provider_config["max_model_len"])
        self.assertEqual(32768, provider_config["num_ctx_min"])
        self.assertEqual(["model-a"], provider_config["custom_models"])
        self.assertEqual([("test", "model-a")], selection_updates)
        self.assertEqual([config], saved)
        self.assertEqual([True], cleared)
        self.assertIn("Model context size: 64K (65,536 tokens).", messages)
        self.assertEqual(["profile", "sync", "cap", "preset:ko", "timeout"], [messages[index] for index in (2, 4, 5, 6, 7)])
        self.assertIn("thinking model", messages[-1])


if __name__ == "__main__":
    unittest.main()
