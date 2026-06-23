import unittest
from unittest import mock

import claude_any


class OllamaProviderOptionTests(unittest.TestCase):
    def test_generic_context_window_maps_to_ollama_num_ctx(self):
        pcfg = {"num_ctx": "auto", "num_ctx_min": 32768, "num_ctx_max": 131072}

        claude_any.apply_provider_option("ollama-cloud", pcfg, "context_window=1048576")

        self.assertEqual(1048576, pcfg["context_window"])
        self.assertEqual("auto", pcfg["num_ctx"])
        self.assertEqual(1048576, pcfg["num_ctx_max"])
        self.assertEqual(65536, pcfg["num_ctx_min"])

    def test_generic_max_output_tokens_maps_to_ollama_num_predict(self):
        pcfg = {"ollama_options": {}}

        claude_any.apply_provider_option("ollama-cloud", pcfg, "max_output_tokens=8192")

        self.assertEqual(8192, pcfg["max_output_tokens"])
        self.assertEqual(8192, pcfg["ollama_options"]["num_predict"])

    def test_generic_sampling_options_stay_in_ollama_options(self):
        pcfg = {"ollama_options": {}}

        claude_any.apply_provider_option("ollama-cloud", pcfg, "temperature=0.7")
        claude_any.apply_provider_option("ollama-cloud", pcfg, "top_p=0.9")

        self.assertEqual(0.7, pcfg["ollama_options"]["temperature"])
        self.assertEqual(0.9, pcfg["ollama_options"]["top_p"])

    def test_ollama_provider_options_status_shows_effective_context(self):
        pcfg = {
            "num_ctx": "auto",
            "num_ctx_min": 65536,
            "num_ctx_max": 1048576,
            "ollama_options": {"num_predict": 8192},
            "rate_limit_rpm": 0,
        }

        status = claude_any.provider_options_status("ollama-cloud", pcfg)

        self.assertIn("num_ctx=auto (65536-1048576)", status)
        self.assertIn("ollama_options=num_predict=8192", status)

    def test_ollama_auto_num_ctx_uses_provider_model_context(self):
        pcfg = {
            "current_model": "qwen3.6:35b-a3b-mtp-2gpu-ctx256k",
            "num_ctx": "auto",
            "num_ctx_min": 65536,
            "num_ctx_max": 131072,
            "model_context_model": "qwen3.6:35b-a3b-mtp-2gpu-ctx256k",
            "model_context_max": 262144,
        }

        payload = {"messages": [{"role": "user", "content": "hello"}], "tools": []}

        self.assertEqual(262144, claude_any.ollama_num_ctx_for_payload(pcfg, payload))
        self.assertEqual(262144, claude_any.ollama_context_limit_for_budget(pcfg))
        self.assertIn("provider 262,144", claude_any.ollama_num_ctx_status(pcfg))

    def test_ollama_fixed_num_ctx_overrides_provider_model_context(self):
        pcfg = {
            "current_model": "qwen3.6:35b-a3b-mtp-2gpu-ctx256k",
            "num_ctx": 65536,
            "num_ctx_min": 65536,
            "num_ctx_max": 131072,
            "model_context_model": "qwen3.6:35b-a3b-mtp-2gpu-ctx256k",
            "model_context_max": 262144,
        }

        payload = {"messages": [{"role": "user", "content": "hello"}], "tools": []}

        self.assertEqual(65536, claude_any.ollama_num_ctx_for_payload(pcfg, payload))

    def test_ollama_auto_num_ctx_ignores_stale_provider_model_context(self):
        pcfg = {
            "current_model": "small-model",
            "num_ctx": "auto",
            "num_ctx_min": 32768,
            "num_ctx_max": 65536,
            "model_context_model": "different-model",
            "model_context_max": 262144,
        }

        payload = {"messages": [{"role": "user", "content": "hello"}], "tools": []}

        self.assertEqual(32768, claude_any.ollama_num_ctx_for_payload(pcfg, payload))

    def test_ollama_provider_context_beats_model_name_hint(self):
        pcfg = {
            "current_model": "qwen3.6:35b-a3b-mtp-2gpu-ctx256k",
            "num_ctx": "auto",
            "num_ctx_min": 65536,
            "num_ctx_max": 262144,
            "model_context_model": "qwen3.6:35b-a3b-mtp-2gpu-ctx256k",
            "model_context_max": 131072,
        }

        self.assertEqual(131072, claude_any.provider_model_context_capacity("ollama", pcfg))
        self.assertEqual(131072, claude_any.ollama_context_limit_for_budget(pcfg))

    def test_ollama_show_parameters_parse_parameters_and_modelfile(self):
        data = {
            "parameters": "num_ctx 262144\nnum_predict 4096\n",
            "modelfile": "FROM base\nPARAMETER num_gpu 999\nPARAMETER temperature 0.7\n",
        }

        params = claude_any.ollama_show_parameters(data)

        self.assertEqual("262144", params["num_ctx"])
        self.assertEqual("4096", params["num_predict"])
        self.assertEqual("999", params["num_gpu"])
        self.assertEqual("0.7", params["temperature"])

    def test_model_context_field_reads_dotted_ollama_model_info(self):
        self.assertEqual(262144, claude_any.model_context_field({"qwen3.context_length": 262144}))

    def test_sync_ollama_context_prefers_api_show_specs(self):
        pcfg = {
            "current_model": "custom-model:latest",
            "num_ctx": "auto",
            "num_ctx_min": 32768,
            "num_ctx_max": 65536,
        }

        with (
            mock.patch.object(claude_any, "fetch_ollama_api_model_specs", return_value={"max_model_len": 262144}),
            mock.patch.object(claude_any, "load_ollama_model_catalog") as load_catalog,
        ):
            messages = claude_any.sync_ollama_library_context_limit("ollama", pcfg, "custom-model:latest")

        load_catalog.assert_not_called()
        self.assertEqual(262144, pcfg["model_context_max"])
        self.assertEqual(262144, pcfg["num_ctx_max"])
        self.assertTrue(any("/api/show" in message for message in messages))

    def test_sync_ollama_context_preserves_explicit_preset_cap_below_provider_max(self):
        pcfg = {
            "current_model": "glm-5.2",
            "llm_preset": "long-context-512k",
            "num_ctx": "auto",
            "num_ctx_min": 262144,
            "num_ctx_max": 524288,
        }

        with (
            mock.patch.object(claude_any, "fetch_ollama_api_model_specs", return_value={"max_model_len": 1000000}),
            mock.patch.object(claude_any, "load_ollama_model_catalog") as load_catalog,
        ):
            claude_any.sync_ollama_library_context_limit("ollama-cloud", pcfg, "glm-5.2")

        load_catalog.assert_not_called()
        self.assertEqual(1000000, pcfg["model_context_max"])
        self.assertEqual(524288, pcfg["num_ctx_max"])
        self.assertEqual(524288, claude_any.ollama_context_limit_for_budget(pcfg))
        self.assertEqual(524288, claude_any.ollama_num_ctx_for_payload(pcfg, {"messages": []}))
        self.assertIn("model max 1,000,000", claude_any.ollama_num_ctx_status(pcfg))

    def test_sync_ollama_context_caps_explicit_preset_above_provider_max(self):
        pcfg = {
            "current_model": "small-model",
            "llm_preset": "million-context-1m",
            "num_ctx": "auto",
            "num_ctx_min": 262144,
            "num_ctx_max": 1048576,
        }

        with mock.patch.object(claude_any, "fetch_ollama_api_model_specs", return_value={"max_model_len": 262144}):
            claude_any.sync_ollama_library_context_limit("ollama-cloud", pcfg, "small-model")

        self.assertEqual(262144, pcfg["model_context_max"])
        self.assertEqual(262144, pcfg["num_ctx_max"])
        self.assertEqual(262144, pcfg["num_ctx_min"])

    def test_current_model_specs_preserve_explicit_ollama_preset_cap(self):
        pcfg = {
            "current_model": "glm-5.2",
            "llm_preset": "long-context-512k",
            "num_ctx": "auto",
            "num_ctx_min": 262144,
            "num_ctx_max": 524288,
        }

        with mock.patch.object(claude_any, "read_model_info_cache", return_value={"glm-5.2": {"max_model_len": 1000000}}):
            claude_any.apply_current_model_specs_to_provider("ollama-cloud", pcfg)

        self.assertEqual(1000000, pcfg["model_context_max"])
        self.assertEqual(524288, pcfg["num_ctx_max"])
        self.assertEqual(524288, claude_any.context_limit_for_status("ollama-cloud", pcfg))

    def test_unset_generic_ollama_aliases_clears_effective_options(self):
        pcfg = {
            "context_window": 1048576,
            "max_output_tokens": 8192,
            "num_ctx": "auto",
            "num_ctx_max": 1048576,
            "ollama_options": {"num_predict": 8192},
        }

        claude_any.apply_provider_option("ollama-cloud", pcfg, "unset:context_window")
        claude_any.apply_provider_option("ollama-cloud", pcfg, "unset:max_output_tokens")

        self.assertNotIn("context_window", pcfg)
        self.assertNotIn("num_ctx_max", pcfg)
        self.assertNotIn("max_output_tokens", pcfg)
        self.assertNotIn("num_predict", pcfg["ollama_options"])

    def test_ollama_output_cap_uses_runtime_context(self):
        pcfg = {
            "current_model": "gemma4:12b",
            "ollama_options": {"num_predict": 8192},
            "max_output_tokens": 8192,
        }

        with mock.patch.object(
            claude_any,
            "ollama_runtime_info",
            return_value={"runtime_model": "gemma4:12b", "loaded_context_len": 65536},
        ):
            messages = claude_any.apply_ollama_runtime_output_guard("ollama", pcfg)

        self.assertEqual(4096, pcfg["ollama_options"]["num_predict"])
        self.assertEqual(4096, pcfg["max_output_tokens"])
        self.assertTrue(any("runtime context 64K" in message for message in messages))

    def test_ollama_output_cap_keeps_128k_runtime_at_8k(self):
        pcfg = {
            "current_model": "large-model",
            "ollama_options": {"num_predict": 8192},
            "max_output_tokens": 8192,
        }

        with mock.patch.object(
            claude_any,
            "ollama_runtime_info",
            return_value={"runtime_model": "large-model", "loaded_context_len": 131072},
        ):
            messages = claude_any.apply_ollama_runtime_output_guard("ollama", pcfg)

        self.assertEqual(8192, pcfg["ollama_options"]["num_predict"])
        self.assertEqual(8192, pcfg["max_output_tokens"])
        self.assertEqual([], messages)


if __name__ == "__main__":
    unittest.main()
