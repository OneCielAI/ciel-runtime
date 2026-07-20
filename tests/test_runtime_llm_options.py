import copy
import unittest
from unittest.mock import Mock

from ciel_runtime_support.runtime_llm_options import (
    RuntimeLlmConfigPorts,
    RuntimeLlmMutationPorts,
    RuntimeLlmOptionsApi,
    RuntimeLlmOptionsController,
    RuntimeLlmPresentationPorts,
    RuntimeLlmSettings,
)


class RuntimeLlmOptionsControllerTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "language": "en",
            "providers": {
                "provider": {"current_model": "model", "context_window": 32768},
                "ollama": {
                    "current_model": "local",
                    "ollama_options": {"num_predict": 4096},
                },
            },
        }
        self.save = Mock()
        self.clear = Mock()

        def apply_preset(_provider, provider_config, preset_id, _language):
            provider_config["llm_preset"] = preset_id
            provider_config["context_window"] = 131072
            return [f"applied {preset_id}"]

        self.controller = RuntimeLlmOptionsController(
            RuntimeLlmSettings(
                option_keys=frozenset(("context_window", "llm_preset")),
                original_key="_original",
                slider_labels={"balanced": "BAL", "long": "LONG"},
            ),
            RuntimeLlmConfigPorts(
                load=lambda: self.config,
                save=self.save,
                clear_model_cache=self.clear,
                deep_copy=copy.deepcopy,
                current_provider=lambda config: (
                    "provider",
                    config["providers"]["provider"],
                ),
                normalize_preset=lambda value: value.strip().lower(),
                resolve_preset=lambda value: value if value in {"balanced", "long"} else None,
            ),
            RuntimeLlmPresentationPorts(
                applied_preset=lambda _provider, config: str(
                    config.get("llm_preset") or "balanced"
                ),
                slider_presets=lambda: ("balanced", "long"),
                preset_text=lambda preset, _language: (preset.title(), f"{preset} desc"),
                provider_label=lambda provider, _config: provider.upper(),
                context_status=lambda _provider, config: str(
                    config.get("context_window", "managed")
                ),
                timeout_status=lambda _config, _language: "5m",
                ollama_options=lambda config: config.get("ollama_options", {}),
            ),
            RuntimeLlmMutationPorts(apply_preset=apply_preset),
        )

    def test_explicit_api_delegates_public_runtime_options_contract(self):
        controller = Mock(spec=RuntimeLlmOptionsController)
        controller.handle_action.return_value = (["status"], False)
        controller.snapshot.return_value = {"version": 1}
        controller.slider_line.return_value = "< [BAL] | LONG >"
        api = RuntimeLlmOptionsApi(lambda: controller)

        self.assertEqual(
            (["status"], False),
            api.handle_live_llm_options_action(action="status", preset=""),
        )
        self.assertEqual(
            {"version": 1},
            api.snapshot_from_provider(provider="provider", pcfg={}),
        )
        self.assertEqual(
            "< [BAL] | LONG >", api.slider_line(provider="provider", pcfg={})
        )

    def test_snapshot_deep_copies_owned_values(self):
        provider_config = {"current_model": "model", "context_window": {"value": 1}}
        snapshot = self.controller.snapshot("provider", provider_config)

        provider_config["context_window"]["value"] = 2
        self.assertEqual({"value": 1}, snapshot["values"]["context_window"])
        self.assertEqual("provider", snapshot["provider"])

    def test_ensure_snapshot_preserves_the_first_capture(self):
        provider_config = self.config["providers"]["provider"]

        self.assertTrue(self.controller.ensure_snapshot("provider", provider_config))
        first = provider_config["_original"]
        provider_config["context_window"] = 65536
        self.assertFalse(self.controller.ensure_snapshot("provider", provider_config))
        self.assertIs(first, provider_config["_original"])

    def test_apply_and_restore_round_trip_original_options(self):
        lines = self.controller.apply_preset("provider", "long")

        provider_config = self.config["providers"]["provider"]
        self.assertEqual(131072, provider_config["context_window"])
        self.assertTrue(lines[0].startswith("Captured current"))
        restored = self.controller.restore("provider")
        self.assertEqual(32768, provider_config["context_window"])
        self.assertNotIn("llm_preset", provider_config)
        self.assertNotIn("_original", provider_config)
        self.assertTrue(restored[0].startswith("Restored live"))
        self.assertEqual(2, self.save.call_count)
        self.assertEqual(2, self.clear.call_count)

    def test_slider_boundary_does_not_persist(self):
        lines = self.controller.apply_slider_delta("provider", -1)

        self.assertIn("remains at Balanced", lines[0])
        self.save.assert_not_called()
        self.clear.assert_not_called()

    def test_slider_move_captures_then_persists(self):
        lines = self.controller.apply_slider_delta("provider", 1)

        self.assertEqual("long", self.config["providers"]["provider"]["llm_preset"])
        self.assertTrue(lines[0].startswith("Live LLM preset moved"))
        self.assertTrue(lines[-1].startswith("Slider:"))
        self.save.assert_called_once_with(self.config)

    def test_status_and_list_project_ollama_output_and_restore(self):
        provider_config = self.config["providers"]["ollama"]
        provider_config["_original"] = {"values": {}}

        lines = self.controller.preset_list_lines("ollama", provider_config)

        self.assertIn("Output tokens: 4096", lines)
        self.assertIn("Restore available: yes", lines)
        self.assertTrue(any("* balanced" in line for line in lines))

    def test_action_dispatches_aliases_and_unknown_values(self):
        lines, changed = self.controller.handle_action("right")

        self.assertTrue(changed)
        self.assertTrue(any("Updated live LLM options" in line for line in lines))
        unknown, changed = self.controller.handle_action("missing")
        self.assertFalse(changed)
        self.assertTrue(unknown[0].startswith("Unknown live LLM"))


if __name__ == "__main__":
    unittest.main()
