import copy
import unittest
from unittest import mock

import ciel_runtime


class FireworksProviderTests(unittest.TestCase):
    def fireworks_cfg(self, **overrides):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["fireworks"])
        pcfg.update(overrides)
        return {
            "current_provider": "fireworks",
            "providers": {
                "fireworks": pcfg,
            },
        }

    def test_provider_is_registered(self):
        self.assertEqual("fireworks", ciel_runtime.PROVIDER_ALIASES["fireworks.ai"])
        self.assertEqual("fireworks", ciel_runtime.PROVIDER_ALIASES["fw"])
        self.assertEqual("Fireworks.ai", ciel_runtime.PROVIDER_LABELS["fireworks"])
        self.assertEqual(ciel_runtime.FIREWORKS_INFERENCE_BASE_URL, ciel_runtime.default_base_url("fireworks"))

    def test_default_config_matches_fireworks_anthropic_docs(self):
        pcfg = ciel_runtime.DEFAULT_CONFIG["providers"]["fireworks"]
        self.assertEqual("https://api.fireworks.ai/inference", pcfg["base_url"])
        self.assertEqual("https://api.fireworks.ai", pcfg["model_api_base_url"])
        self.assertEqual("fireworks", pcfg["account_id"])
        self.assertEqual("accounts/fireworks/models/kimi-k2p5", pcfg["current_model"])
        self.assertTrue(pcfg["native_compat"])

    def test_native_base_url_strips_v1_suffix(self):
        pcfg = self.fireworks_cfg(base_url="https://api.fireworks.ai/inference/v1")["providers"]["fireworks"]

        self.assertEqual("https://api.fireworks.ai/inference", ciel_runtime.native_anthropic_base_url("fireworks", pcfg))

    def test_management_base_url_derives_from_custom_inference_base(self):
        pcfg = self.fireworks_cfg(base_url="https://fw-proxy.local/inference")["providers"]["fireworks"]

        self.assertEqual("https://fw-proxy.local", ciel_runtime.fireworks_management_base_url(pcfg))

    def test_account_id_can_be_inferred_from_model_resource_name(self):
        pcfg = self.fireworks_cfg(account_id="", current_model="accounts/acme/models/custom-chat")["providers"]["fireworks"]

        self.assertEqual("acme", ciel_runtime.fireworks_account_id(pcfg))

    def test_provider_headers_include_fireworks_api_key(self):
        pcfg = self.fireworks_cfg(api_key="fw-test-key")["providers"]["fireworks"]

        headers = ciel_runtime.provider_headers("fireworks", pcfg)

        self.assertEqual("Bearer fw-test-key", headers["authorization"])
        self.assertEqual("fw-test-key", headers["x-api-key"])
        self.assertEqual("claude-cli", headers["user-agent"])

    def test_model_info_extracts_context_tools_vision_and_parameters(self):
        data = {
            "models": [
                {
                    "name": "accounts/fireworks/models/test-model",
                    "displayName": "Test Model",
                    "description": "A test Fireworks model",
                    "contextLength": 131072,
                    "supportsTools": True,
                    "supportsImageInput": False,
                    "supportsServerless": True,
                    "importedFrom": "hf/test/model",
                    "baseModelDetails": {
                        "parameterCount": "123000000000",
                        "worldSize": 8,
                        "checkpointFormat": "SAFETENSORS",
                        "modelType": "decoder",
                        "defaultPrecision": "BF16",
                    },
                }
            ]
        }

        self.assertEqual(["accounts/fireworks/models/test-model"], ciel_runtime.model_ids_from_response(data))
        info = ciel_runtime.model_info_from_response("fireworks", data)

        model_info = info["accounts/fireworks/models/test-model"]
        self.assertEqual(131072, model_info["max_model_len"])
        self.assertEqual("123000000000", model_info["parameter_count"])
        self.assertEqual(8, model_info["world_size"])
        self.assertEqual("SAFETENSORS", model_info["checkpoint_format"])
        self.assertTrue(model_info["supports_tool_call"])
        self.assertFalse(model_info["supports_vision"])
        self.assertTrue(model_info["supports_serverless"])

    def test_model_panel_shows_fireworks_context_and_parameter_count(self):
        model = "accounts/fireworks/models/test-model"
        pcfg = self.fireworks_cfg(current_model=model)["providers"]["fireworks"]

        with (
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=[model]),
            mock.patch.object(ciel_runtime, "read_model_info_cache", return_value={
                model: {
                    "max_model_len": 131072,
                    "parameter_count": "123000000000",
                }
            }),
        ):
            rows, values = ciel_runtime.model_panel_rows("fireworks", pcfg, fetch=False)

        self.assertIn(model, values)
        selected_row = next(row for row in rows if model in row)
        self.assertIn("[ctx 128K]", selected_row)
        self.assertIn("[123B params]", selected_row)

    def test_upstream_model_ids_uses_account_models_api_and_caches_metadata(self):
        pcfg = self.fireworks_cfg(api_key="fw-test-key", custom_models=[])["providers"]["fireworks"]
        first_page = {
            "models": [
                {
                    "name": "accounts/fireworks/models/kimi-k2p5",
                    "contextLength": 262144,
                    "supportsTools": True,
                    "baseModelDetails": {"parameterCount": "1000000000000"},
                }
            ],
            "nextPageToken": "next",
        }
        second_page = {
            "models": [
                {
                    "name": "accounts/fireworks/models/llama-v3p1-8b-instruct",
                    "contextLength": 131072,
                    "supportsTools": True,
                    "baseModelDetails": {"parameterCount": "8000000000"},
                }
            ]
        }

        with (
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=None),
            mock.patch.object(ciel_runtime, "http_json", side_effect=[first_page, second_page]) as http_json,
            mock.patch.object(ciel_runtime, "write_model_list_cache") as write_cache,
        ):
            models = ciel_runtime.upstream_model_ids("fireworks", pcfg)

        self.assertIn("accounts/fireworks/models/kimi-k2p5", models)
        self.assertIn("accounts/fireworks/models/llama-v3p1-8b-instruct", models)
        urls = [call.args[0] for call in http_json.call_args_list]
        self.assertIn("/v1/accounts/fireworks/models?pageSize=200", urls[0])
        self.assertIn("pageToken=next", urls[1])
        write_cache.assert_called_once()
        metadata = write_cache.call_args.args[3]
        self.assertEqual("fireworks:fireworks", metadata["source"])
        cached_info = metadata["model_info"]
        self.assertEqual(262144, cached_info["accounts/fireworks/models/kimi-k2p5"]["max_model_len"])
        self.assertEqual("8000000000", cached_info["accounts/fireworks/models/llama-v3p1-8b-instruct"]["parameter_count"])

    def test_model_specs_drive_context_capacity_and_preset(self):
        model = "accounts/fireworks/models/context-window"
        pcfg = self.fireworks_cfg(current_model=model, context_window=32768)["providers"]["fireworks"]

        with (
            mock.patch.object(ciel_runtime, "read_model_info_cache", return_value={model: {"max_model_len": 1048576}}),
            mock.patch.object(ciel_runtime, "upstream_model_context_limit", return_value=None),
        ):
            messages = ciel_runtime.apply_current_model_specs_to_provider("fireworks", pcfg)
            capacity = ciel_runtime.provider_model_context_capacity("fireworks", pcfg)
            preset = ciel_runtime.recommended_preset_id("fireworks", pcfg)

        self.assertEqual(1048576, pcfg["max_model_len"])
        self.assertEqual(1048576, capacity)
        self.assertEqual("million-context-1m", preset)
        self.assertTrue(any("Model context size from provider specs" in message for message in messages))

    def test_provider_options_can_set_fireworks_model_api_fields(self):
        pcfg = self.fireworks_cfg()["providers"]["fireworks"]

        ciel_runtime.apply_provider_option("fireworks", pcfg, "account_id=acme")
        ciel_runtime.apply_provider_option("fireworks", pcfg, "model_api_base_url=https://fw.example")

        self.assertEqual("acme", pcfg["account_id"])
        self.assertEqual("https://fw.example", pcfg["model_api_base_url"])


if __name__ == "__main__":
    unittest.main()
