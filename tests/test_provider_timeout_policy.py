import unittest

from ciel_runtime_support.architecture import ProviderContextPolicy
from ciel_runtime_support.provider_timeout_policy import (
    ProviderTimeoutPolicy,
    ProviderTimeoutPorts,
    ProviderTimeoutSettings,
)


def positive_int(value):
    try:
        fixed = int(value)
    except (TypeError, ValueError):
        return None
    return fixed if fixed > 0 else None


class ProviderTimeoutPolicyTests(unittest.TestCase):
    def policy(self, context_policy=None):
        context_policy = context_policy or ProviderContextPolicy(
            settings_strategy="standard"
        )
        return ProviderTimeoutPolicy(
            ProviderTimeoutSettings(
                default_ms=300000,
                minimum_ms=120000,
                maximum_ms=600000,
                round_ms=30000,
                idle_max_ms=300000,
                preset_timeouts={"slow": 600000},
            ),
            ProviderTimeoutPorts(
                positive_int=positive_int,
                context_policy=lambda _provider, _config: context_policy,
                context_capacity=lambda _provider, _config: 262144,
                output_token_cap=lambda _provider, _config, value: value,
                ollama_options=lambda config: config.get("ollama_options", {}),
                catalog_timeout=lambda _model: None,
                model_preset=lambda _model: {},
                timeout_for_context=lambda _context: 120000,
                format_context=lambda value: f"{value:,}" if value else "unknown",
            ),
        )

    def test_configured_context_uses_adapter_setting_strategy(self):
        policy = self.policy(
            ProviderContextPolicy(settings_strategy="ollama")
        )
        config = {"num_ctx": "auto", "num_ctx_max": 131072}

        self.assertEqual(262144, policy.configured_context("ollama", config))
        config["num_ctx"] = 65536
        self.assertEqual(65536, policy.configured_context("ollama", config))

    def test_calculation_combines_context_output_and_hosted_weight(self):
        policy = self.policy(
            ProviderContextPolicy(
                settings_strategy="standard",
                hosted_timeout=True,
                timeout_weight=1.5,
            )
        )

        timeout = policy.calculated(
            "hosted",
            {"context_window": 262144, "max_output_tokens": 8192},
        )

        self.assertEqual(600000, timeout)

    def test_active_preset_timeout_is_a_floor(self):
        policy = self.policy()

        timeout = policy.recommended(
            "provider",
            {"current_model": "model", "llm_preset": "slow"},
        )

        self.assertEqual(600000, timeout)

    def test_apply_updates_timeout_and_is_idempotent(self):
        policy = self.policy()
        config = {"current_model": "model", "context_window": 131072}

        messages = policy.apply("provider", config)

        self.assertEqual(180000, config["request_timeout_ms"])
        self.assertEqual(180000, config["stream_idle_timeout_ms"])
        self.assertEqual(2, len(messages))
        self.assertEqual([], policy.apply("provider", config))


if __name__ == "__main__":
    unittest.main()
