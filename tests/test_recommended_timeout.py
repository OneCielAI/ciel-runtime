import unittest
from unittest import mock

import claude_any


class RecommendedTimeoutTests(unittest.TestCase):
    def test_context_size_maps_to_timeout(self):
        self.assertEqual(300000, claude_any.recommended_timeout_ms_for_context(None))
        self.assertEqual(300000, claude_any.recommended_timeout_ms_for_context(1048576))
        self.assertEqual(180000, claude_any.recommended_timeout_ms_for_context(524288))
        self.assertEqual(120000, claude_any.recommended_timeout_ms_for_context(262144))
        self.assertEqual(120000, claude_any.recommended_timeout_ms_for_context(65536))

    def test_ollama_cloud_large_model_applies_weighted_timeout(self):
        pcfg = {"current_model": "deepseek-v4-flash", "num_ctx": "auto", "num_ctx_max": 1048576}

        with mock.patch.object(claude_any, "ollama_catalog_timeout_for_model", return_value=None):
            messages = claude_any.apply_recommended_timeout_for_model_context("ollama-cloud", pcfg)

        self.assertEqual(510000, pcfg["request_timeout_ms"])
        self.assertEqual(300000, pcfg["stream_idle_timeout_ms"])
        self.assertTrue(any("510000" in message for message in messages))

    def test_half_million_context_applies_five_minute_timeout(self):
        pcfg = {"current_model": "custom-large", "context_window": 524288}

        claude_any.apply_recommended_timeout_for_model_context("vllm", pcfg)

        self.assertEqual(300000, pcfg["request_timeout_ms"])
        self.assertEqual(300000, pcfg["stream_idle_timeout_ms"])

    def test_quarter_million_ollama_cloud_context_applies_weighted_timeout(self):
        pcfg = {"current_model": "kimi-k2.6", "num_ctx": "auto", "num_ctx_max": 262144}

        with mock.patch.object(claude_any, "ollama_catalog_timeout_for_model", return_value=None):
            claude_any.apply_recommended_timeout_for_model_context("ollama-cloud", pcfg)

        self.assertEqual(360000, pcfg["request_timeout_ms"])
        self.assertEqual(300000, pcfg["stream_idle_timeout_ms"])

    def test_unknown_model_change_falls_back_to_five_minutes(self):
        pcfg = {"current_model": "unknown-private-model", "num_ctx": "auto", "num_ctx_max": 262144}

        claude_any.apply_recommended_timeout_for_model_context("ollama-cloud", pcfg, use_context_fallback=False)

        self.assertEqual(300000, pcfg["request_timeout_ms"])
        self.assertEqual(300000, pcfg["stream_idle_timeout_ms"])

    def test_explicit_context_change_still_uses_context_recommendation(self):
        pcfg = {"current_model": "unknown-private-model", "num_ctx": "auto", "num_ctx_max": 262144}

        with mock.patch.object(claude_any, "ollama_catalog_timeout_for_model", return_value=None):
            claude_any.apply_recommended_timeout_for_model_context("ollama-cloud", pcfg, use_context_fallback=True)

        self.assertEqual(360000, pcfg["request_timeout_ms"])
        self.assertEqual(300000, pcfg["stream_idle_timeout_ms"])

    def test_active_128k_preset_is_not_lowered_by_ollama_catalog_timeout(self):
        pcfg = {
            "current_model": "glm-5.1",
            "llm_preset": "long-context-128k",
            "num_ctx": "auto",
            "num_ctx_min": 65536,
            "num_ctx_max": 131072,
        }

        with mock.patch.object(claude_any, "ollama_catalog_timeout_for_model", return_value=120000):
            claude_any.apply_recommended_timeout_for_model_context("ollama-cloud", pcfg, use_context_fallback=False)

        self.assertEqual(600000, pcfg["request_timeout_ms"])
        self.assertEqual(300000, pcfg["stream_idle_timeout_ms"])

    def test_ollama_catalog_timeout_is_not_used_for_non_ollama_providers(self):
        pcfg = {"current_model": "glm-5.1", "context_window": 131072}

        with mock.patch.object(claude_any, "ollama_catalog_timeout_for_model", return_value=120000) as catalog_timeout:
            claude_any.apply_recommended_timeout_for_model_context("opencode", pcfg, use_context_fallback=False)

        catalog_timeout.assert_not_called()
        self.assertEqual(300000, pcfg["request_timeout_ms"])
        self.assertEqual(300000, pcfg["stream_idle_timeout_ms"])

    def test_llm_preset_application_sets_slow_128k_timeout(self):
        pcfg = {"current_model": "glm-5.1"}

        with mock.patch.object(claude_any, "sync_ollama_library_context_limit", return_value=[]), mock.patch.object(
            claude_any, "ollama_catalog_timeout_for_model", return_value=120000
        ):
            claude_any.apply_llm_preset_to_provider("ollama-cloud", pcfg, "long-context-128k", "en")

        self.assertEqual("long-context-128k", pcfg["llm_preset"])
        self.assertEqual(600000, pcfg["request_timeout_ms"])
        self.assertEqual(300000, pcfg["stream_idle_timeout_ms"])

    def test_hosted_output_and_context_are_combined(self):
        pcfg = {"current_model": "custom-model", "context_window": 131072, "max_output_tokens": 8192}

        claude_any.apply_recommended_timeout_for_model_context("opencode", pcfg, use_context_fallback=True)

        self.assertEqual(360000, pcfg["request_timeout_ms"])
        self.assertEqual(300000, pcfg["stream_idle_timeout_ms"])

    def test_auto_timeout_is_capped_at_ten_minutes(self):
        pcfg = {"current_model": "huge-model", "context_window": 1048576, "max_output_tokens": 32768, "llm_preset": "million-context-1m"}

        claude_any.apply_recommended_timeout_for_model_context("opencode", pcfg, use_context_fallback=True)

        self.assertEqual(600000, pcfg["request_timeout_ms"])
        self.assertEqual(300000, pcfg["stream_idle_timeout_ms"])


if __name__ == "__main__":
    unittest.main()
