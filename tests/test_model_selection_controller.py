import unittest

from ciel_runtime_support.provider_model_selection import (
    AdvisorModelMutationPorts,
    AdvisorModelSelectionController,
    ModelMutationConfigPorts,
    ModelMutationEffectPorts,
    ModelMutationPolicyPorts,
    ModelSelectionController,
)


class ModelSelectionControllerTests(unittest.TestCase):
    def test_advisor_selection_uses_provider_policy_and_tracks_custom_models(self):
        provider_config = {"advisor_model": ""}
        config = {"providers": {"test": provider_config}}
        saved = []
        cleared = []
        controller = AdvisorModelSelectionController(
            AdvisorModelMutationPorts(
                load_config=lambda: config,
                current_provider=lambda _config: ("test", provider_config),
                save_config=saved.append,
                clear_model_cache=lambda: cleared.append(True),
                normalize=lambda _provider, value: value.casefold(),
                read_model_list=lambda _provider, _config: ["known"],
                uses_native_advisor=lambda _provider, _config: False,
            )
        )

        messages = controller.select("CUSTOM")

        self.assertEqual("custom", provider_config["advisor_model"])
        self.assertEqual(["custom"], provider_config["custom_models"])
        self.assertEqual([config], saved)
        self.assertEqual([True], cleared)
        self.assertIn("set to custom", messages[0])

    def test_advisor_selection_defers_to_native_provider_without_mutation(self):
        provider_config = {"advisor_model": "keep"}
        controller = AdvisorModelSelectionController(
            AdvisorModelMutationPorts(
                load_config=lambda: {"providers": {"anthropic": provider_config}},
                current_provider=lambda _config: ("anthropic", provider_config),
                save_config=lambda _config: self.fail("native advisor must not persist"),
                clear_model_cache=lambda: self.fail("native advisor must not clear cache"),
                normalize=lambda _provider, value: value,
                read_model_list=lambda _provider, _config: [],
                uses_native_advisor=lambda _provider, _config: True,
            )
        )

        messages = controller.select("ignored")

        self.assertEqual("keep", provider_config["advisor_model"])
        self.assertIn("built-in /advisor", messages[0])

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
        self.assertIn("new session", messages[3])
        self.assertEqual(["profile", "sync", "cap", "preset:ko", "timeout"], [messages[index] for index in (2, 5, 6, 7, 8)])
        self.assertIn("thinking model", messages[-1])


if __name__ == "__main__":
    unittest.main()
