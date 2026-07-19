import unittest
from unittest import mock

from ciel_runtime_support.architecture import ProviderContextPolicy
from ciel_runtime_support.provider_context import (
    ContextPresetServices,
    ProviderContextServices,
    cap_context_settings,
    classify_model_family,
    infer_context_preset,
    recommended_preset,
    required_context_for_preset,
    resolve_context_capacity,
)


def positive_int(value):
    try:
        fixed = int(value)
    except (TypeError, ValueError):
        return None
    return fixed if fixed > 0 else None


class ProviderContextTests(unittest.TestCase):
    def services(self):
        return ProviderContextServices(
            positive_int=positive_int,
            model_context_hint=mock.Mock(return_value=300),
            anthropic_context_hint=mock.Mock(return_value=700),
            nvidia_context_default=mock.Mock(return_value=400),
            upstream_context_limit=mock.Mock(return_value=500),
            ollama_context_limit=mock.Mock(return_value=None),
        )

    def test_capacity_strategy_controls_lookup_precedence(self):
        config = {"current_model": "model", "max_model_len": 200, "context_window": 100}
        expected = {
            "managed": None,
            "nvidia": 400,
            "remote_first": 500,
            "hint_first": 300,
            "configured_first": 200,
            "hint_configured": 300,
            "anthropic_hint": 100,
        }
        for strategy, capacity in expected.items():
            with self.subTest(strategy=strategy):
                policy = ProviderContextPolicy(capacity_strategy=strategy)
                self.assertEqual(capacity, resolve_context_capacity("provider", config, policy, self.services()))

    def test_ollama_capacity_falls_back_to_configured_maximum(self):
        services = self.services()
        services.model_context_hint.return_value = None
        capacity = resolve_context_capacity(
            "provider",
            {"current_model": "model", "num_ctx_max": 131072},
            ProviderContextPolicy(capacity_strategy="ollama"),
            services,
        )
        self.assertEqual(131072, capacity)

    def test_setting_strategy_caps_only_owned_fields(self):
        ollama = {"num_ctx": 300, "num_ctx_min": 250, "num_ctx_max": 400}
        messages = cap_context_settings(
            ollama,
            200,
            ProviderContextPolicy(settings_strategy="ollama"),
            positive_int=positive_int,
        )
        self.assertEqual({"num_ctx": 200, "num_ctx_min": 200, "num_ctx_max": 200}, ollama)
        self.assertEqual(1, len(messages))

        standard = {"context_window": 400, "num_ctx_max": 500}
        cap_context_settings(
            standard,
            200,
            ProviderContextPolicy(settings_strategy="standard"),
            positive_int=positive_int,
        )
        self.assertEqual(200, standard["context_window"])
        self.assertEqual(500, standard["num_ctx_max"])

    def test_preset_inference_uses_context_setting_strategy(self):
        services = ContextPresetServices(
            positive_int=positive_int,
            ollama_options=lambda config: config.get("ollama_options", {}),
            ollama_thinking_enabled=lambda _model, config: bool(config.get("think")),
        )
        ollama = infer_context_preset(
            {"num_ctx_max": 262144, "ollama_options": {"num_predict": 8192}},
            ProviderContextPolicy(settings_strategy="ollama"),
            services,
        )
        standard = infer_context_preset(
            {"context_window": 131072, "max_output_tokens": 8192},
            ProviderContextPolicy(settings_strategy="standard"),
            services,
        )
        managed = infer_context_preset(
            {"max_output_tokens": 2048}, ProviderContextPolicy(), services
        )
        self.assertEqual("long-context-256k", ollama)
        self.assertEqual("long-context-128k", standard)
        self.assertIsNone(managed)

    def test_family_classification_uses_provider_context_policy(self):
        services = ContextPresetServices(
            positive_int=positive_int,
            ollama_options=lambda config: config.get("ollama_options", {}),
            ollama_thinking_enabled=lambda _model, _config: False,
        )
        regular = classify_model_family(
            {"current_model": "model-pro"},
            ProviderContextPolicy(settings_strategy="standard"),
            131072,
            services,
        )
        context_first = classify_model_family(
            {"current_model": "model-pro"},
            ProviderContextPolicy(
                settings_strategy="standard", context_family_before_size_markers=True
            ),
            131072,
            services,
        )
        self.assertEqual("large", regular)
        self.assertEqual("long-context", context_first)
        self.assertEqual("long-context-128k", recommended_preset(context_first, 131072))

    def test_required_context_uses_provider_profile(self):
        ollama = ProviderContextPolicy(preset_context_profile="ollama")
        nvidia = ProviderContextPolicy(preset_context_profile="nvidia")
        default = ProviderContextPolicy()
        self.assertEqual(524288, required_context_for_preset("humanities-researcher", ollama))
        self.assertEqual(262144, required_context_for_preset("reasoning", nvidia))
        self.assertEqual(65536, required_context_for_preset("large-output", default))


if __name__ == "__main__":
    unittest.main()
