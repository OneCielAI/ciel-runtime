import copy
import unittest

import ciel_runtime


class ZaiProviderTests(unittest.TestCase):
    def test_documented_provider_bases_are_registered(self):
        self.assertEqual(
            "https://api.z.ai/api/anthropic",
            ciel_runtime.default_base_url("zai"),
        )
        self.assertEqual(
            "https://api.z.ai/api/paas/v4",
            ciel_runtime.default_base_url("zai-api"),
        )
        self.assertEqual(
            "https://api.z.ai/api/coding/paas/v4",
            ciel_runtime.default_base_url("zai-coding-plan"),
        )

    def test_coding_plan_routes_each_protocol_to_its_published_base(self):
        config = ciel_runtime.DEFAULT_CONFIG["providers"]["zai-coding-plan"]
        self.assertEqual(
            "https://api.z.ai/api/anthropic/v1/messages",
            ciel_runtime.provider_endpoint(
                "zai-coding-plan", config, "anthropic_messages"
            ),
        )
        self.assertEqual(
            "https://api.z.ai/api/coding/paas/v4/chat/completions",
            ciel_runtime.provider_endpoint("zai-coding-plan", config, "openai_chat"),
        )
        self.assertEqual(
            "https://api.z.ai/api/v1/responses",
            ciel_runtime.provider_endpoint(
                "zai-coding-plan", config, "openai_responses"
            ),
        )

    def test_codex_responses_passthrough_uses_the_protocol_specific_endpoint(self):
        config = ciel_runtime.DEFAULT_CONFIG["providers"]["zai-coding-plan"]
        passthrough = ciel_runtime.codex_backend_context().responses_passthrough()
        self.assertEqual(
            "https://api.z.ai/api/v1/responses",
            passthrough._endpoint(
                "zai-coding-plan", config, "openai_responses"
            ),
        )
        self.assertEqual(
            "https://api.z.ai/api/v1/responses/compact",
            passthrough._endpoint(
                "zai-coding-plan", config, "openai_responses_compact"
            ),
        )

    def test_coding_plan_preserves_the_runtime_preferred_protocol(self):
        config = ciel_runtime.DEFAULT_CONFIG["providers"]["zai-coding-plan"]
        for protocol in (
            "anthropic_messages",
            "openai_chat",
            "openai_responses",
        ):
            with self.subTest(protocol=protocol):
                self.assertEqual(
                    protocol,
                    ciel_runtime.select_provider_protocol(
                        "zai-coding-plan", config, protocol, "glm-5.3"
                    ),
                )

    def test_coding_plan_auth_uses_configured_key_without_fabrication(self):
        config = copy.deepcopy(
            ciel_runtime.DEFAULT_CONFIG["providers"]["zai-coding-plan"]
        )
        config["api_key"] = "coding-plan-key"
        headers = ciel_runtime.provider_headers("zai-coding-plan", config)
        self.assertEqual("Bearer coding-plan-key", headers["authorization"])
        self.assertEqual("coding-plan-key", headers["x-api-key"])

    def test_glm_53_parameters_match_published_constraints(self):
        config = copy.deepcopy(
            ciel_runtime.DEFAULT_CONFIG["providers"]["zai-coding-plan"]
        )
        body = {
            "model": "glm-5.3",
            "thinking": {"type": "disabled"},
            "reasoning_effort": "xhigh",
            "temperature": 0.2,
        }
        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "zai-coding-plan", config, body, protocol="openai_responses"
        )
        self.assertEqual({"type": "enabled"}, normalized["thinking"])
        self.assertEqual("max", normalized["reasoning_effort"])
        self.assertEqual(1.0, normalized["temperature"])

        adapter = ciel_runtime.configured_provider_adapter("zai-coding-plan", config)
        profile, _notice = adapter.model_configuration_profile(
            ciel_runtime.provider_contract_config("zai-coding-plan", config)
        )
        self.assertEqual(1_000_000, profile["context_window"])
        self.assertEqual(131_072, profile["max_output_tokens"])

    def test_glm_51_and_52_published_context_profiles(self):
        adapter = ciel_runtime.configured_provider_adapter(
            "zai-coding-plan",
            ciel_runtime.DEFAULT_CONFIG["providers"]["zai-coding-plan"],
        )
        for model, expected in (("glm-5.1", 200_000), ("glm-5.2", 1_000_000)):
            config = copy.deepcopy(
                ciel_runtime.DEFAULT_CONFIG["providers"]["zai-coding-plan"]
            )
            config["current_model"] = model
            profile, _notice = adapter.model_configuration_profile(
                ciel_runtime.provider_contract_config("zai-coding-plan", config)
            )
            self.assertEqual(expected, profile["context_window"])
            self.assertEqual(131_072, profile["max_output_tokens"])

    def test_start_plan_uses_installed_zcode_anthropic_contract(self):
        config = copy.deepcopy(
            ciel_runtime.DEFAULT_CONFIG["providers"]["zai-start-plan"]
        )
        config["api_key"] = "preserved-existing-value"
        adapter = ciel_runtime.configured_provider_adapter("zai-start-plan", config)
        contract = ciel_runtime.provider_contract_config("zai-start-plan", config)
        blocker = adapter.launch_api_key_error(
            contract
        )
        self.assertIsNone(blocker)
        self.assertEqual(
            "https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages",
            ciel_runtime.provider_endpoint(
                "zai-start-plan", config, "anthropic_messages"
            ),
        )
        headers = adapter.build_headers(contract, "start-plan-jwt")
        self.assertEqual("Bearer start-plan-jwt", headers["authorization"])
        self.assertEqual("start-plan-jwt", headers["x-api-key"])
        self.assertEqual("https://zcode.z.ai", headers["HTTP-Referer"])
        self.assertEqual("ZCode/3.9.1", headers["User-Agent"])


if __name__ == "__main__":
    unittest.main()
