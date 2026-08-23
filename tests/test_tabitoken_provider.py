import copy
import unittest

import ciel_runtime
from ciel_runtime_support.config_repository import deep_merge


class TabitokenProviderTests(unittest.TestCase):
    def tabitoken_pcfg(self, **overrides):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["tabitoken"])
        pcfg.update(overrides)
        return pcfg

    def test_provider_is_registered_with_aliases_and_official_models(self):
        self.assertEqual("tabitoken", ciel_runtime.normalize_provider("tabi"))
        self.assertEqual("tabitoken", ciel_runtime.normalize_provider("tabiai"))
        self.assertEqual(
            "TaBiAI (Tabitoken.com)",
            ciel_runtime.PROVIDER_LABELS["tabitoken"],
        )
        self.assertEqual(
            [
                "claude-opus-4-8",
                "claude-opus-4-8-thinking",
                "claude-opus-5",
                "claude-opus-5-thinking",
            ],
            ciel_runtime.DEFAULT_CONFIG["providers"]["tabitoken"]["custom_models"],
        )

    def test_official_openai_and_anthropic_routes_are_selected(self):
        pcfg = self.tabitoken_pcfg(api_key="tabi-test")
        self.assertEqual(
            "https://tabitoken.com/v1/chat/completions",
            ciel_runtime.provider_endpoint("tabitoken", pcfg, "openai_chat"),
        )
        self.assertEqual(
            "https://tabitoken.com/v1/messages",
            ciel_runtime.join_url(
                ciel_runtime.native_anthropic_base_url("tabitoken", pcfg),
                "/v1/messages",
            ),
        )
        self.assertEqual(
            "anthropic_messages",
            ciel_runtime.select_provider_protocol(
                "tabitoken", pcfg, "anthropic_messages", "claude-opus-5"
            ),
        )
        self.assertEqual(
            "openai_chat",
            ciel_runtime.select_provider_protocol(
                "tabitoken", pcfg, "openai_responses", "claude-opus-5"
            ),
        )

    def test_bearer_auth_matches_documented_all_endpoint_authentication(self):
        headers = ciel_runtime.provider_headers(
            "tabitoken", self.tabitoken_pcfg(api_key="tabi-test")
        )
        self.assertEqual("Bearer tabi-test", headers["Authorization"])
        self.assertNotIn("x-api-key", headers)

    def test_thinking_models_forward_codex_reasoning_effort(self):
        pcfg = self.tabitoken_pcfg(current_model="claude-opus-5-thinking")
        request = ciel_runtime.openai_compatible_chat_request(
            "tabitoken",
            "claude-opus-5-thinking",
            {
                "model": "claude-opus-5-thinking",
                "messages": [{"role": "user", "content": "inspect"}],
                "metadata": {"ciel_runtime_reasoning_effort": "HIGH"},
            },
            pcfg,
            stream=True,
        )
        self.assertEqual("high", request["reasoning_effort"])

    def test_non_thinking_models_do_not_receive_reasoning_effort(self):
        pcfg = self.tabitoken_pcfg(current_model="claude-opus-5")
        request = ciel_runtime.openai_compatible_chat_request(
            "tabitoken",
            "claude-opus-5",
            {
                "model": "claude-opus-5",
                "messages": [{"role": "user", "content": "inspect"}],
                "metadata": {"ciel_runtime_reasoning_effort": "high"},
            },
            pcfg,
            stream=True,
        )
        self.assertNotIn("reasoning_effort", request)

    def test_existing_config_load_merge_adds_provider_defaults(self):
        cfg = deep_merge(ciel_runtime.DEFAULT_CONFIG, {"providers": {}})
        self.assertIn("tabitoken", cfg["providers"])
        self.assertEqual(
            "claude-opus-4-8",
            cfg["providers"]["tabitoken"]["current_model"],
        )


if __name__ == "__main__":
    unittest.main()
