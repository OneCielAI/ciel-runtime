import unittest

from ciel_runtime_support.llm_option_config import (
    AutoLlmModelPolicy,
    AutoLlmOptionsRepository,
    AutoLlmOptionsService,
    AutoLlmPresetPolicy,
)


class AutoLlmOptionsServiceTests(unittest.TestCase):
    def service(self, *, available=True):
        config = {
            "language": "en",
            "providers": {"vllm": {"current_model": "old-model"}},
        }
        events = []

        def set_model(model_id):
            config["providers"]["vllm"]["current_model"] = model_id
            return [f"selected:{model_id}"]

        def apply_preset(provider, provider_config, preset_id, language, **options):
            events.append(("preset", provider, preset_id, language, options))
            provider_config["llm_preset"] = preset_id
            return [f"applied:{preset_id}"]

        service = AutoLlmOptionsService(
            repository=AutoLlmOptionsRepository(
                set_model=set_model,
                load=lambda: config,
                save=lambda value: events.append(("save", value)),
                invalidate_cache=lambda: events.append(("invalidate",)),
            ),
            model=AutoLlmModelPolicy(
                current_provider=lambda value: ("vllm", value["providers"]["vllm"]),
                refresh_specs=lambda provider, provider_config: ["specs-refreshed"],
                sync_context=lambda provider, provider_config, model: [f"context:{model}"],
                cap_context=lambda provider, provider_config: ["context-capped"],
            ),
            preset=AutoLlmPresetPolicy(
                recommended=lambda provider, provider_config: "recommended",
                available=lambda provider, provider_config, preset_id: available,
                applied=lambda provider, provider_config: "fallback",
                text=lambda preset_id, language: (f"label:{preset_id}", "description"),
                apply=apply_preset,
            ),
        )
        return service, config, events

    def test_apply_auto_runs_model_context_preset_and_persistence_transaction(self):
        service, config, events = self.service()

        lines = service.apply_auto(" new-model ")

        self.assertEqual("new-model", config["providers"]["vllm"]["current_model"])
        self.assertIn("selected:new-model", lines)
        self.assertIn("context:new-model", lines)
        self.assertIn("applied:recommended", lines)
        self.assertEqual("save", events[-2][0])
        self.assertEqual(("invalidate",), events[-1])

    def test_apply_auto_falls_back_to_current_preset_when_recommendation_is_unavailable(self):
        service, _config, _events = self.service(available=False)

        lines = service.apply_auto()

        self.assertIn("applied:fallback", lines)

    def test_apply_recommended_skips_unavailable_preset(self):
        service, config, _events = self.service(available=False)

        self.assertEqual(
            [],
            service.apply_recommended("vllm", config["providers"]["vllm"], "en"),
        )

    def test_apply_recommended_disables_duplicate_context_sync(self):
        service, config, events = self.service()

        lines = service.apply_recommended(
            "vllm", config["providers"]["vllm"], "en"
        )

        self.assertIn("applied:recommended", lines)
        self.assertEqual({"sync_ollama_context": False}, events[-1][-1])


if __name__ == "__main__":
    unittest.main()
