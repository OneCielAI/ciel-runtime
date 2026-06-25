import copy
import io
import unittest
from unittest import mock

import ciel_runtime


class OpenCodeProviderTests(unittest.TestCase):
    def opencode_cfg(self, **overrides):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["opencode"])
        pcfg.update(overrides)
        return {
            "current_provider": "opencode",
            "providers": {
                "opencode": pcfg,
            },
        }

    def opencode_go_cfg(self, **overrides):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["opencode-go"])
        pcfg.update(overrides)
        return {
            "current_provider": "opencode-go",
            "providers": {
                "opencode-go": pcfg,
            },
        }

    def test_provider_is_registered(self):
        self.assertEqual("opencode", ciel_runtime.PROVIDER_ALIASES["opencode"])
        self.assertEqual("opencode", ciel_runtime.PROVIDER_ALIASES["opencode.ai"])
        self.assertEqual("opencode", ciel_runtime.PROVIDER_ALIASES["zen"])
        self.assertEqual("opencode-go", ciel_runtime.PROVIDER_ALIASES["opencode-go"])
        self.assertEqual("opencode-go", ciel_runtime.PROVIDER_ALIASES["opencode.go"])
        self.assertEqual("OpenCode Zen", ciel_runtime.PROVIDER_LABELS["opencode"])
        self.assertEqual("OpenCode Go", ciel_runtime.PROVIDER_LABELS["opencode-go"])
        self.assertEqual("https://opencode.ai/zen", ciel_runtime.default_base_url("opencode"))
        self.assertEqual("https://opencode.ai/zen/go", ciel_runtime.default_base_url("opencode-go"))

    def test_default_config_matches_zen_docs(self):
        pcfg = ciel_runtime.DEFAULT_CONFIG["providers"]["opencode"]
        self.assertEqual("https://opencode.ai/zen", pcfg["base_url"])
        self.assertEqual("claude-sonnet-4-6", pcfg["current_model"])
        self.assertEqual("claude-haiku-4-5", pcfg["haiku_model"])
        self.assertEqual("claude-sonnet-4-6", pcfg["subagent_model"])
        self.assertIn("qwen3.6-plus-free", pcfg["custom_models"])
        self.assertEqual("ipv6-preferred", pcfg["ip_family"])
        self.assertTrue(pcfg["native_compat"])

    def test_go_default_config_matches_go_docs(self):
        pcfg = ciel_runtime.DEFAULT_CONFIG["providers"]["opencode-go"]
        self.assertEqual("https://opencode.ai/zen/go", pcfg["base_url"])
        self.assertEqual("qwen3.6-plus", pcfg["current_model"])
        self.assertEqual("qwen3.5-plus", pcfg["haiku_model"])
        self.assertEqual("qwen3.6-plus", pcfg["subagent_model"])
        self.assertEqual(1048576, pcfg["context_window"])
        self.assertEqual("ipv6-preferred", pcfg["ip_family"])
        self.assertTrue(pcfg["native_compat"])

    def test_llm_options_expose_ip_family(self):
        pcfg = self.opencode_cfg()["providers"]["opencode"]

        rows, values = ciel_runtime.llm_option_panel_rows("opencode", pcfg, "en")

        self.assertIn("ip_family", values)
        row = rows[values.index("ip_family")]
        self.assertIn("IP family", row)
        self.assertIn("ipv6-preferred", row)
        self.assertEqual("ipv6-preferred", ciel_runtime.llm_option_prompt_default("opencode", pcfg, "ip_family"))

    def test_llm_options_can_set_ip_family(self):
        cfg = self.opencode_cfg(ip_family="ipv6-preferred")
        pcfg = cfg["providers"]["opencode"]

        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "save_config") as save_config,
            mock.patch.object(ciel_runtime, "clear_model_cache"),
        ):
            messages = ciel_runtime.set_llm_option_config("opencode", "ip_family", "ipv4")

        self.assertEqual("ipv4", pcfg["ip_family"])
        self.assertIn("ip_family: ipv4", messages)
        save_config.assert_called_once_with(cfg)

    def test_qwen36_plus_models_are_million_context(self):
        models = ["qwen3.6-plus", "qwen3.6-plus-free", "Qwen3.6 Plus", "Qwen3.6 Plus Free"]
        for model in models:
            with self.subTest(model=model):
                pcfg = self.opencode_go_cfg(current_model=model, context_window=262144)["providers"]["opencode-go"]

                with mock.patch.object(ciel_runtime, "upstream_model_context_limit", return_value=None):
                    self.assertEqual(1048576, ciel_runtime.model_context_hint_from_model_id(model))
                    self.assertEqual(1048576, ciel_runtime.provider_model_context_capacity("opencode-go", pcfg))
                    self.assertEqual("million-context", ciel_runtime.model_option_family("opencode-go", pcfg))
                    self.assertEqual("million-context-1m", ciel_runtime.recommended_preset_id("opencode-go", pcfg))

    def test_qwen36_non_plus_models_remain_quarter_million_context(self):
        pcfg = self.opencode_go_cfg(current_model="qwen3.6-27b-mtp", context_window=262144)["providers"]["opencode-go"]

        with mock.patch.object(ciel_runtime, "upstream_model_context_limit", return_value=None):
            self.assertEqual(262144, ciel_runtime.model_context_hint_from_model_id("qwen3.6-27b-mtp"))
            self.assertEqual("long-context", ciel_runtime.model_option_family("opencode-go", pcfg))
            self.assertEqual("long-context-256k", ciel_runtime.recommended_preset_id("opencode-go", pcfg))

    def test_qwen36_plus_auto_preset_applies_one_million_context(self):
        pcfg = self.opencode_go_cfg(current_model="qwen3.6-plus-free", context_window=262144)["providers"]["opencode-go"]

        with mock.patch.object(ciel_runtime, "upstream_model_context_limit", return_value=None):
            messages = ciel_runtime.auto_apply_recommended_llm_preset_for_model("opencode-go", pcfg, "en")

        self.assertEqual("million-context-1m", pcfg["llm_preset"])
        self.assertEqual(1048576, pcfg["context_window"])
        self.assertEqual(16384, pcfg["context_reserve_tokens"])
        self.assertEqual(8192, pcfg["max_output_tokens"])
        self.assertTrue(any("Ultra context 1M" in message for message in messages))

    def test_zen_qwen36_plus_free_auto_preset_applies_one_million_context(self):
        pcfg = self.opencode_cfg(current_model="qwen3.6-plus-free", context_window=200000)["providers"]["opencode"]

        with mock.patch.object(ciel_runtime, "upstream_model_context_limit", return_value=None):
            messages = ciel_runtime.auto_apply_recommended_llm_preset_for_model("opencode", pcfg, "en")

        self.assertEqual("million-context-1m", pcfg["llm_preset"])
        self.assertEqual(1048576, pcfg["context_window"])
        self.assertEqual(16384, pcfg["context_reserve_tokens"])
        self.assertEqual(8192, pcfg["max_output_tokens"])
        self.assertTrue(any("Ultra context 1M" in message for message in messages))

    def test_provider_capacity_prefers_refreshed_model_specs_over_stale_context_window(self):
        pcfg = self.opencode_cfg(
            current_model="sample-model-128k",
            context_window=32768,
            max_model_len=131072,
        )["providers"]["opencode"]

        self.assertEqual(131072, ciel_runtime.provider_model_context_capacity("opencode", pcfg))

    def test_auto_llm_options_refreshes_model_specs_before_applying_preset(self):
        model = "sample-model-1m"
        cfg = self.opencode_cfg(current_model=model, context_window=32768)
        pcfg = cfg["providers"]["opencode"]

        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "save_config") as save_config,
            mock.patch.object(ciel_runtime, "invalidate_config_cache"),
            mock.patch.object(ciel_runtime, "upstream_model_ids", return_value=[model]) as upstream,
            mock.patch.object(ciel_runtime, "read_model_info_cache", return_value={model: {"max_model_len": 1048576}}),
            mock.patch.object(ciel_runtime, "sync_ollama_library_context_limit", return_value=[]),
        ):
            messages = ciel_runtime.apply_auto_llm_options_config()

        upstream.assert_called_once_with("opencode", pcfg, force_refresh=True)
        save_config.assert_called_once()
        self.assertEqual(1048576, pcfg["max_model_len"])
        self.assertEqual(1048576, pcfg["context_window"])
        self.assertEqual("million-context-1m", pcfg["llm_preset"])
        self.assertTrue(any("Model specs refreshed" in message for message in messages))
        self.assertTrue(any("Model context size from provider specs" in message for message in messages))

    def test_auto_llm_options_model_argument_reapplies_after_specs_refresh(self):
        model = "sample-model-128k"
        cfg = self.opencode_cfg(current_model=model, context_window=32768)
        pcfg = cfg["providers"]["opencode"]

        def set_model(value: str) -> list[str]:
            pcfg["current_model"] = value
            return [f"Model for opencode set to {value}."]

        with (
            mock.patch.object(ciel_runtime, "set_model_config", side_effect=set_model) as set_model_config,
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "save_config"),
            mock.patch.object(ciel_runtime, "invalidate_config_cache"),
            mock.patch.object(ciel_runtime, "upstream_model_ids", return_value=[model]) as upstream,
            mock.patch.object(ciel_runtime, "read_model_info_cache", return_value={model: {"max_model_len": 131072}}),
            mock.patch.object(ciel_runtime, "sync_ollama_library_context_limit", return_value=[]),
        ):
            messages = ciel_runtime.apply_auto_llm_options_config(model)

        set_model_config.assert_called_once_with(model)
        upstream.assert_called_once_with("opencode", pcfg, force_refresh=True)
        self.assertEqual(131072, pcfg["max_model_len"])
        self.assertEqual(131072, pcfg["context_window"])
        self.assertEqual("long-context-128k", pcfg["llm_preset"])
        self.assertTrue(messages[0].startswith("Model for opencode set to"))

    def test_migration_updates_old_qwen36_plus_default_context(self):
        cfg = {
            "migrations": {},
            "providers": {
                "opencode-go": {
                    "current_model": "qwen3.6-plus-free",
                    "context_window": 262144,
                }
            },
        }

        ciel_runtime.apply_config_migrations(cfg)

        self.assertEqual(1048576, cfg["providers"]["opencode-go"]["context_window"])
        self.assertTrue(cfg["migrations"]["opencode_go_qwen36_plus_context_1m_20260530"])
        self.assertTrue(cfg["migrations"]["opencode_qwen36_plus_parameters_20260614"])

    def test_migration_adds_zen_qwen36_plus_free_fallback_model(self):
        cfg = {
            "migrations": {},
            "providers": {
                "opencode": {
                    "current_model": "claude-sonnet-4-6",
                    "custom_models": ["claude-sonnet-4-6"],
                    "context_window": 200000,
                }
            },
        }

        ciel_runtime.apply_config_migrations(cfg)

        self.assertIn("qwen3.6-plus-free", cfg["providers"]["opencode"]["custom_models"])
        self.assertEqual(200000, cfg["providers"]["opencode"]["context_window"])
        self.assertTrue(cfg["migrations"]["opencode_zen_qwen36_plus_free_model_20260614"])

    def test_migration_updates_zen_qwen36_plus_free_parameters(self):
        cfg = {
            "migrations": {},
            "providers": {
                "opencode": {
                    "current_model": "qwen3.6-plus-free",
                    "custom_models": [],
                    "context_window": 200000,
                    "context_reserve_tokens": 8192,
                    "max_output_tokens": 4096,
                }
            },
        }

        ciel_runtime.apply_config_migrations(cfg)

        pcfg = cfg["providers"]["opencode"]
        self.assertEqual(1048576, pcfg["context_window"])
        self.assertEqual(16384, pcfg["context_reserve_tokens"])
        self.assertEqual(8192, pcfg["max_output_tokens"])
        self.assertIn("qwen3.6-plus-free", pcfg["custom_models"])
        self.assertTrue(cfg["migrations"]["opencode_qwen36_plus_parameters_20260614"])

    def test_migration_keeps_non_plus_qwen36_context(self):
        cfg = {
            "migrations": {},
            "providers": {
                "opencode-go": {
                    "current_model": "qwen3.6-27b-mtp",
                    "context_window": 262144,
                }
            },
        }

        ciel_runtime.apply_config_migrations(cfg)

        self.assertEqual(262144, cfg["providers"]["opencode-go"]["context_window"])
        self.assertTrue(cfg["migrations"]["opencode_go_qwen36_plus_context_1m_20260530"])

    def test_env_vars_route_opencode_through_ciel_runtime_router(self):
        cfg = self.opencode_cfg(api_key="sk-opencode-test")
        pcfg = cfg["providers"]["opencode"]
        env = ciel_runtime.env_vars(cfg)
        self.assertEqual("opencode", env["CIEL_RUNTIME_PROVIDER"])
        self.assertEqual(ciel_runtime.ROUTER_BASE, env["ANTHROPIC_BASE_URL"])
        self.assertEqual("sk-opencode-test", env["ANTHROPIC_AUTH_TOKEN"])
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        expected_model = ciel_runtime.claude_code_context_model_alias("opencode", pcfg, ciel_runtime.current_alias(cfg))
        self.assertEqual(expected_model, env["ANTHROPIC_MODEL"])

    def test_default_family_models_use_provider_family_candidates(self):
        cfg = self.opencode_cfg(
            api_key="sk-opencode-test",
            current_model="deepseek-v4-flash-free",
        )
        pcfg = cfg["providers"]["opencode"]
        models = [
            "deepseek-v4-flash-free",
            "claude-haiku-4-5",
            "claude-opus-4-8",
            "claude-sonnet-4-6",
        ]

        with mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=models):
            env = ciel_runtime.env_vars(cfg)

        self.assertEqual(
            ciel_runtime.claude_code_context_model_alias("opencode", pcfg, ciel_runtime.alias_for("opencode", "deepseek-v4-flash-free")),
            env["ANTHROPIC_MODEL"],
        )
        self.assertEqual(
            ciel_runtime.claude_code_context_model_alias("opencode", pcfg, ciel_runtime.alias_for("opencode", "claude-haiku-4-5")),
            env["ANTHROPIC_DEFAULT_HAIKU_MODEL"],
        )
        self.assertEqual(
            ciel_runtime.claude_code_context_model_alias("opencode", pcfg, ciel_runtime.alias_for("opencode", "claude-opus-4-8")),
            env["ANTHROPIC_DEFAULT_OPUS_MODEL"],
        )
        self.assertEqual(
            ciel_runtime.claude_code_context_model_alias("opencode", pcfg, ciel_runtime.alias_for("opencode", "claude-sonnet-4-6")),
            env["ANTHROPIC_DEFAULT_SONNET_MODEL"],
        )
        self.assertNotEqual(env["ANTHROPIC_MODEL"], env["ANTHROPIC_DEFAULT_OPUS_MODEL"])

    def test_workflow_env_advertises_inferred_claude_capabilities(self):
        cfg = self.opencode_cfg(
            api_key="sk-opencode-test",
            current_model="claude-opus-4-8",
            workflows_enabled=True,
        )

        env = ciel_runtime.env_vars(cfg)

        self.assertNotIn("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", env)
        self.assertEqual(env["ANTHROPIC_MODEL"], env["ANTHROPIC_CUSTOM_MODEL_OPTION"])
        caps = env["ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES"].split(",")
        self.assertIn("effort", caps)
        self.assertIn("xhigh_effort", caps)
        self.assertIn("max_effort", caps)
        self.assertIn("adaptive_thinking", caps)
        self.assertEqual(
            env["ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES"],
            env["ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES"],
        )
        self.assertEqual(
            env["ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES"],
            env["ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTS"],
        )

    def test_configured_capabilities_override_inference(self):
        cfg = self.opencode_cfg(
            api_key="sk-opencode-test",
            current_model="custom-model",
            claude_code_supported_capabilities=["effort", "max_effort", "unknown"],
        )

        env = ciel_runtime.env_vars(cfg)

        self.assertEqual("effort,max_effort", env["ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES"])

    def test_ultracode_launch_requires_xhigh_capability(self):
        cfg = self.opencode_cfg(
            api_key="sk-opencode-test",
            current_model="deepseek-v4-flash-free",
            ultracode_enabled=True,
        )
        with mock.patch.object(ciel_runtime, "base_url_status_line", return_value="Base URL: OK"):
            errors = ciel_runtime.launch_readiness_errors(cfg)

        self.assertTrue(any("ultracode requires" in error for error in errors))

    def test_ultracode_runtime_settings(self):
        pcfg = self.opencode_cfg(ultracode_enabled=True)["providers"]["opencode"]

        self.assertEqual({"ultracode": True}, ciel_runtime.claude_code_runtime_settings("opencode", pcfg))

    def test_ultracode_runtime_settings_args_are_appended(self):
        pcfg = self.opencode_cfg(ultracode_enabled=True)["providers"]["opencode"]
        extra_args: list[str] = []

        ciel_runtime.append_claude_code_runtime_settings_args(extra_args, [], "opencode", pcfg)

        self.assertEqual(["--settings", '{"ultracode":true}'], extra_args)

    def test_env_vars_route_opencode_go_through_ciel_runtime_router(self):
        cfg = self.opencode_go_cfg(api_key="sk-opencode-test")
        pcfg = cfg["providers"]["opencode-go"]
        env = ciel_runtime.env_vars(cfg)
        self.assertEqual("opencode-go", env["CIEL_RUNTIME_PROVIDER"])
        self.assertEqual(ciel_runtime.ROUTER_BASE, env["ANTHROPIC_BASE_URL"])
        self.assertEqual("sk-opencode-test", env["ANTHROPIC_AUTH_TOKEN"])
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        expected_model = ciel_runtime.claude_code_context_model_alias("opencode-go", pcfg, ciel_runtime.current_alias(cfg))
        self.assertEqual(expected_model, env["ANTHROPIC_MODEL"])

    def test_launch_requires_opencode_api_key(self):
        errors = ciel_runtime.launch_readiness_errors(self.opencode_cfg(api_key=""))
        self.assertTrue(any("OpenCode Zen requires" in err for err in errors))
        self.assertTrue(ciel_runtime.launch_blockers_require_api_key(errors))

    def test_launch_requires_opencode_go_api_key(self):
        errors = ciel_runtime.launch_readiness_errors(self.opencode_go_cfg(api_key=""))
        self.assertTrue(any("OpenCode Go requires" in err for err in errors))
        self.assertTrue(ciel_runtime.launch_blockers_require_api_key(errors))

    def test_model_list_reads_zen_v1_models(self):
        pcfg = self.opencode_cfg(api_key="sk-opencode-test")["providers"]["opencode"]
        response = {
            "object": "list",
            "data": [
                {"id": "claude-sonnet-4-6"},
                {"id": "qwen3.6-plus-free"},
                {"id": "glm-5.1"},
                {"id": "gpt-5.1"},
            ],
        }
        with (
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=None),
            mock.patch.object(ciel_runtime, "write_model_list_cache") as write_cache,
            mock.patch.object(ciel_runtime, "http_json", return_value=response) as http_json,
        ):
            models = ciel_runtime.upstream_model_ids("opencode", pcfg)
        http_json.assert_called_once()
        url = http_json.call_args.args[0]
        self.assertEqual("https://opencode.ai/zen/v1/models", url)
        headers = http_json.call_args.kwargs["headers"]
        self.assertEqual("Bearer sk-opencode-test", headers["authorization"])
        self.assertEqual("claude-cli", headers["user-agent"])
        self.assertIn("claude-sonnet-4-6", models)
        self.assertIn("qwen3.6-plus-free", models)
        self.assertIn("glm-5.1", models)
        self.assertIn("gpt-5.1", models)
        write_cache.assert_called_once()

    def test_model_list_retries_public_opencode_catalog_when_key_request_fails(self):
        pcfg = self.opencode_cfg(api_key="sk-opencode-test")["providers"]["opencode"]
        public_response = {
            "object": "list",
            "data": [
                {"id": "deepseek-v4-flash"},
                {"id": "qwen3.5-plus"},
            ],
        }
        with (
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=None),
            mock.patch.object(ciel_runtime, "write_model_list_cache") as write_cache,
            mock.patch.object(
                ciel_runtime,
                "http_json",
                side_effect=[
                    PermissionError("bad key"),
                    TimeoutError("fallback path missing"),
                    public_response,
                ],
            ) as http_json,
        ):
            models = ciel_runtime.upstream_model_ids("opencode", pcfg)

        self.assertIn("deepseek-v4-flash", models)
        self.assertIn("qwen3.5-plus", models)
        self.assertEqual(3, http_json.call_count)
        public_headers = http_json.call_args_list[2].kwargs["headers"]
        self.assertNotIn("authorization", public_headers)
        self.assertEqual("claude-cli", public_headers["user-agent"])
        write_cache.assert_called_once()

    def test_go_model_list_reads_go_v1_models(self):
        pcfg = self.opencode_go_cfg(api_key="sk-opencode-test")["providers"]["opencode-go"]
        response = {
            "object": "list",
            "data": [
                {"id": "qwen3.6-plus"},
                {"id": "glm-5.1"},
                {"id": "deepseek-v4-pro"},
            ],
        }
        with (
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=None),
            mock.patch.object(ciel_runtime, "write_model_list_cache") as write_cache,
            mock.patch.object(ciel_runtime, "http_json", return_value=response) as http_json,
        ):
            models = ciel_runtime.upstream_model_ids("opencode-go", pcfg)
        http_json.assert_called_once()
        url = http_json.call_args.args[0]
        self.assertEqual("https://opencode.ai/zen/go/v1/models", url)
        headers = http_json.call_args.kwargs["headers"]
        self.assertEqual("Bearer sk-opencode-test", headers["authorization"])
        self.assertEqual("claude-cli", headers["user-agent"])
        self.assertIn("qwen3.6-plus", models)
        self.assertIn("glm-5.1", models)
        self.assertIn("deepseek-v4-pro", models)
        write_cache.assert_called_once()

    def test_zen_advisor_panel_does_not_inject_global_deepseek_recommendation(self):
        pcfg = self.opencode_cfg(api_key="sk-opencode-test")["providers"]["opencode"]
        with mock.patch.object(
            ciel_runtime,
            "upstream_model_ids",
            return_value=["claude-sonnet-4-6", "deepseek-v4-flash-free", "glm-5.1"],
        ):
            rows, values = ciel_runtime.advisor_model_panel_rows("opencode", pcfg)

        self.assertNotIn("deepseek-v4-pro", values)
        self.assertFalse(any("deepseek-v4-pro" in row for row in rows))
        self.assertIn("deepseek-v4-flash-free", values)
        self.assertIn("__refresh_models__", values)

    def test_model_panel_keeps_refresh_action_after_fetch(self):
        pcfg = self.opencode_cfg(api_key="sk-opencode-test")["providers"]["opencode"]
        with mock.patch.object(ciel_runtime, "upstream_model_ids", return_value=["claude-sonnet-4-6"]):
            rows, values = ciel_runtime.model_panel_rows("opencode", pcfg, fetch=True, force_refresh=True)

        self.assertEqual("__refresh_models__", values[0])
        self.assertTrue(any("Refresh provider model list" in row for row in rows))

    def test_advisor_panel_can_force_refresh_provider_models(self):
        pcfg = self.opencode_go_cfg(api_key="sk-opencode-test")["providers"]["opencode-go"]
        with mock.patch.object(ciel_runtime, "upstream_model_ids", return_value=["deepseek-v4-pro"]) as upstream:
            rows, values = ciel_runtime.advisor_model_panel_rows(
                "opencode-go",
                pcfg,
                fetch=True,
                force_refresh=True,
            )

        upstream.assert_called_once_with("opencode-go", pcfg, force_refresh=True)
        self.assertIn("__refresh_models__", values)
        self.assertIn("deepseek-v4-pro", values)
        self.assertTrue(any("Refresh provider model list" in row for row in rows))

    def test_advisor_panel_keeps_preconfigured_custom_advisor_visible(self):
        pcfg = self.opencode_cfg(api_key="sk-opencode-test", advisor_model="custom-advisor")["providers"]["opencode"]
        with mock.patch.object(ciel_runtime, "upstream_model_ids", return_value=["claude-sonnet-4-6"]):
            rows, values = ciel_runtime.advisor_model_panel_rows("opencode", pcfg)

        self.assertIn("custom-advisor", values)
        self.assertTrue(any("custom-advisor" in row for row in rows))

    def test_model_list_falls_back_to_config_when_zen_unreachable(self):
        pcfg = self.opencode_cfg(
            current_model="glm-5.1",
            custom_models=["claude-sonnet-4-6", "glm-5.1"],
        )["providers"]["opencode"]
        with (
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=None),
            mock.patch.object(ciel_runtime, "write_model_list_cache") as write_cache,
            mock.patch.object(ciel_runtime, "http_json", side_effect=TimeoutError("offline")),
        ):
            models = ciel_runtime.upstream_model_ids("opencode", pcfg)
        self.assertEqual(["claude-sonnet-4-6", "glm-5.1"], models)
        write_cache.assert_called_once()

    def test_provider_headers_include_opencode_api_key(self):
        pcfg = self.opencode_cfg(api_key="sk-opencode-test")["providers"]["opencode"]
        headers = ciel_runtime.provider_headers("opencode", pcfg)
        self.assertEqual("Bearer sk-opencode-test", headers["authorization"])
        self.assertEqual("sk-opencode-test", headers["x-api-key"])
        self.assertEqual("2023-06-01", headers["anthropic-version"])
        self.assertEqual("claude-cli", headers["user-agent"])

    def test_provider_headers_include_opencode_go_api_key(self):
        pcfg = self.opencode_go_cfg(api_key="sk-opencode-test")["providers"]["opencode-go"]
        headers = ciel_runtime.provider_headers("opencode-go", pcfg)
        self.assertEqual("Bearer sk-opencode-test", headers["authorization"])
        self.assertEqual("sk-opencode-test", headers["x-api-key"])
        self.assertEqual("2023-06-01", headers["anthropic-version"])
        self.assertEqual("claude-cli", headers["user-agent"])

    def test_zen_endpoint_family_mapping(self):
        self.assertEqual("anthropic-messages", ciel_runtime.opencode_zen_endpoint_kind("claude-sonnet-4-6"))
        self.assertEqual("anthropic-messages", ciel_runtime.opencode_zen_endpoint_kind("qwen3.6-plus"))
        self.assertEqual("anthropic-messages", ciel_runtime.opencode_zen_endpoint_kind("qwen3.6-plus-free"))
        self.assertEqual("openai-chat", ciel_runtime.opencode_zen_endpoint_kind("glm-5.1"))
        self.assertEqual("openai-responses", ciel_runtime.opencode_zen_endpoint_kind("gpt-5.1"))
        self.assertEqual("google-generative", ciel_runtime.opencode_zen_endpoint_kind("gemini-3.1-pro"))
        self.assertEqual("openai-chat", ciel_runtime.opencode_zen_endpoint_kind("north-mini-code-free"))
        self.assertEqual("anthropic-messages", ciel_runtime.opencode_zen_endpoint_kind("new-custom-model"))

    def test_go_endpoint_family_mapping(self):
        self.assertEqual("anthropic-messages", ciel_runtime.opencode_go_endpoint_kind("qwen3.6-plus"))
        self.assertEqual("anthropic-messages", ciel_runtime.opencode_go_endpoint_kind("minimax-m2.7"))
        self.assertEqual("openai-chat", ciel_runtime.opencode_go_endpoint_kind("glm-5.1"))
        self.assertEqual("openai-chat", ciel_runtime.opencode_go_endpoint_kind("kimi-k2.6"))
        self.assertEqual("openai-chat", ciel_runtime.opencode_go_endpoint_kind("deepseek-v4-pro"))
        self.assertEqual("openai-chat", ciel_runtime.opencode_go_endpoint_kind("mimo-v2.5-pro"))
        self.assertEqual("anthropic-messages", ciel_runtime.opencode_go_endpoint_kind("new-custom-model"))

    def test_native_compat_depends_on_zen_endpoint_family(self):
        claude_cfg = self.opencode_cfg(current_model="claude-sonnet-4-6")["providers"]["opencode"]
        glm_cfg = self.opencode_cfg(current_model="glm-5.1")["providers"]["opencode"]
        self.assertTrue(ciel_runtime.provider_native_compat_enabled("opencode", claude_cfg))
        self.assertFalse(ciel_runtime.provider_native_compat_enabled("opencode", glm_cfg))

    def test_endpoint_override_takes_precedence_over_fallback(self):
        pcfg = self.opencode_go_cfg(
            current_model="glm-5.1",
            model_endpoints={"glm-5.1": "messages"},
        )["providers"]["opencode-go"]
        self.assertEqual("anthropic-messages", ciel_runtime.opencode_endpoint_kind("opencode-go", "glm-5.1", pcfg))
        self.assertTrue(ciel_runtime.provider_native_compat_enabled("opencode-go", pcfg))

    def test_provider_option_sets_endpoint_override(self):
        pcfg = self.opencode_go_cfg()["providers"]["opencode-go"]
        ciel_runtime.apply_provider_option("opencode-go", pcfg, "endpoint:custom-model=chat")
        self.assertEqual("openai-chat", pcfg["model_endpoints"]["custom-model"])
        self.assertEqual("openai-chat", ciel_runtime.opencode_endpoint_kind("opencode-go", "custom-model", pcfg))
        ciel_runtime.apply_provider_option("opencode-go", pcfg, "unset:endpoint:custom-model")
        self.assertNotIn("custom-model", pcfg["model_endpoints"])

    def test_go_native_compat_depends_on_endpoint_family(self):
        qwen_cfg = self.opencode_go_cfg(current_model="qwen3.6-plus")["providers"]["opencode-go"]
        glm_cfg = self.opencode_go_cfg(current_model="glm-5.1")["providers"]["opencode-go"]
        self.assertTrue(ciel_runtime.provider_native_compat_enabled("opencode-go", qwen_cfg))
        self.assertFalse(ciel_runtime.provider_native_compat_enabled("opencode-go", glm_cfg))

    def test_model_object_reports_zen_endpoint_metadata(self):
        obj = ciel_runtime.model_object("opencode", "gpt-5.1")
        self.assertEqual("openai-responses", obj["ciel_runtime"]["opencode_endpoint"])
        self.assertFalse(obj["ciel_runtime"]["router_supported"])

    def test_go_model_object_reports_endpoint_metadata(self):
        obj = ciel_runtime.model_object("opencode-go", "glm-5.1")
        self.assertEqual("openai-chat", obj["ciel_runtime"]["opencode_endpoint"])
        self.assertTrue(obj["ciel_runtime"]["router_supported"])

    def test_zen_deepseek_chat_omits_forced_tool_choice(self):
        pcfg = self.opencode_cfg(api_key="sk-opencode-test")["providers"]["opencode"]
        body = ciel_runtime.compatibility_tool_request("deepseek-v4-flash-free")

        request = ciel_runtime.openai_compatible_chat_request(
            "opencode",
            "deepseek-v4-flash-free",
            body,
            pcfg,
            stream=False,
        )

        self.assertIn("tools", request)
        self.assertNotIn("tool_choice", request)

    def test_go_deepseek_chat_omits_forced_tool_choice(self):
        pcfg = self.opencode_go_cfg(api_key="sk-opencode-test")["providers"]["opencode-go"]
        body = ciel_runtime.compatibility_tool_request("deepseek-v4-pro")

        request = ciel_runtime.openai_compatible_chat_request(
            "opencode-go",
            "deepseek-v4-pro",
            body,
            pcfg,
            stream=False,
        )

        self.assertIn("tools", request)
        self.assertNotIn("tool_choice", request)

    def test_non_deepseek_chat_preserves_forced_tool_choice(self):
        pcfg = self.opencode_cfg(api_key="sk-opencode-test")["providers"]["opencode"]
        body = ciel_runtime.compatibility_tool_request("glm-5.1")

        request = ciel_runtime.openai_compatible_chat_request(
            "opencode",
            "glm-5.1",
            body,
            pcfg,
            stream=False,
        )

        self.assertEqual(
            {"type": "function", "function": {"name": ciel_runtime.COMPAT_TOOL_NAME}},
            request.get("tool_choice"),
        )

    def test_zen_deepseek_roundtrips_reasoning_content(self):
        pcfg = self.opencode_cfg(
            api_key="sk-opencode-test",
            current_model="deepseek-v4-flash-free",
        )["providers"]["opencode"]
        data = {
            "choices": [
                {
                    "message": {
                        "reasoning_content": "private chain",
                        "content": "visible answer",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "Bash", "arguments": "{\"command\":\"echo hi\"}"},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        message = ciel_runtime.openai_chat_to_anthropic(data, "deepseek-v4-flash-free")
        self.assertEqual("thinking", message["content"][0]["type"])
        self.assertEqual("private chain", message["content"][0]["thinking"])

        body = {
            "model": "ciel-runtime-opencode-deepseek-v4-flash-free",
            "thinking": {"type": "enabled"},
            "messages": [{"role": "assistant", "content": message["content"]}],
        }
        normalized = ciel_runtime.normalize_thinking_for_non_anthropic_provider("opencode", pcfg, body)
        self.assertNotIn("thinking", normalized)
        self.assertEqual("thinking", normalized["messages"][0]["content"][0]["type"])

        converted = ciel_runtime.anthropic_messages_to_openai(normalized)
        assistant = [item for item in converted if item.get("role") == "assistant"][-1]
        self.assertEqual("private chain", assistant["reasoning_content"])
        self.assertEqual("visible answer", assistant["content"])
        self.assertEqual("Bash", assistant["tool_calls"][0]["function"]["name"])

    def test_zen_deepseek_backfills_empty_reasoning_for_legacy_history(self):
        pcfg = self.opencode_cfg(
            api_key="sk-opencode-test",
            current_model="deepseek-v4-flash-free",
        )["providers"]["opencode"]
        body = {
            "model": "ciel-runtime-opencode-deepseek-v4-flash-free",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "legacy answer"},
                        {"type": "tool_use", "id": "call_1", "name": "Read", "input": {"file_path": "a.txt"}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call_1", "content": "ok"},
                    ],
                },
            ],
            "tools": [
                {
                    "name": "Read",
                    "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}},
                }
            ],
        }

        request = ciel_runtime.openai_compatible_chat_request(
            "opencode",
            "deepseek-v4-flash-free",
            body,
            pcfg,
            stream=False,
        )

        assistant = [item for item in request["messages"] if item.get("role") == "assistant"][-1]
        self.assertIn("reasoning_content", assistant)
        self.assertEqual("", assistant["reasoning_content"])
        self.assertEqual("legacy answer", assistant["content"])

    def test_openai_request_repairs_missing_historical_tool_result(self):
        pcfg = self.opencode_cfg(
            api_key="sk-opencode-test",
            current_model="deepseek-v4-flash-free",
        )["providers"]["opencode"]
        body = {
            "model": "ciel-runtime-opencode-deepseek-v4-flash-free",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "checking"},
                        {"type": "tool_use", "id": "call_missing", "name": "Read", "input": {"file_path": "a.txt"}},
                    ],
                },
                {"role": "user", "content": "계속"},
            ],
            "tools": [
                {
                    "name": "Read",
                    "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}},
                }
            ],
        }

        request = ciel_runtime.openai_compatible_chat_request(
            "opencode",
            "deepseek-v4-flash-free",
            body,
            pcfg,
            stream=False,
        )

        messages = request["messages"]
        assistant_index = next(i for i, item in enumerate(messages) if item.get("tool_calls"))
        self.assertEqual("assistant", messages[assistant_index]["role"])
        self.assertEqual("tool", messages[assistant_index + 1]["role"])
        self.assertEqual("call_missing", messages[assistant_index + 1]["tool_call_id"])
        self.assertIn("not present in the retained Claude Code transcript", messages[assistant_index + 1]["content"])
        self.assertEqual("user", messages[assistant_index + 2]["role"])
        self.assertEqual("계속", messages[assistant_index + 2]["content"])

    def test_openai_request_demotes_orphan_tool_message(self):
        messages = ciel_runtime.repair_openai_tool_call_adjacency(
            [
                {"role": "user", "content": "hello"},
                {"role": "tool", "tool_call_id": "call_orphan", "content": "late result"},
            ]
        )

        self.assertEqual("user", messages[1]["role"])
        self.assertIn("Historical tool message without a retained assistant tool call", messages[1]["content"])
        self.assertIn("late result", messages[1]["content"])

    def test_non_deepseek_openai_chat_still_strips_anthropic_thinking(self):
        pcfg = self.opencode_cfg(api_key="sk-opencode-test", current_model="glm-5.1")["providers"]["opencode"]
        body = {
            "model": "ciel-runtime-opencode-glm-5-1",
            "thinking": {"type": "enabled"},
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "private", "signature": "sig"},
                        {"type": "text", "text": "visible"},
                    ],
                }
            ],
        }

        normalized = ciel_runtime.normalize_thinking_for_non_anthropic_provider("opencode", pcfg, body)

        self.assertNotIn("thinking", normalized)
        self.assertEqual([{"type": "text", "text": "visible"}], normalized["messages"][0]["content"])

    def test_zen_deepseek_stream_emits_reasoning_block(self):
        class FakeHandler:
            def __init__(self):
                self.wfile = io.BytesIO()

        def sse(payload):
            return f"data: {ciel_runtime.json.dumps(payload, ensure_ascii=False)}\n\n".encode()

        chunks = [
            sse({"choices": [{"delta": {"reasoning_content": "private "}}]}),
            sse({"choices": [{"delta": {"reasoning_content": "chain"}}]}),
            sse({"choices": [{"delta": {"content": "visible"}}]}),
            sse({"choices": [{"finish_reason": "stop", "delta": {}}], "usage": {"completion_tokens": 4}}),
            b"data: [DONE]\n\n",
        ]
        handler = FakeHandler()

        ok = ciel_runtime.stream_openai_chat_to_anthropic_sse(
            handler,
            io.BytesIO(b"".join(chunks)),
            "deepseek-v4-flash-free",
            "opencode",
            source_body={"messages": [{"role": "user", "content": "hello"}]},
        )

        self.assertTrue(ok)
        output = handler.wfile.getvalue().decode("utf-8")
        self.assertIn('"type": "thinking"', output)
        self.assertIn('"type": "thinking_delta"', output)
        self.assertIn("private ", output)
        self.assertIn("chain", output)
        self.assertIn('"type": "signature_delta"', output)
        self.assertIn('"type": "text_delta"', output)
        self.assertIn("visible", output)


if __name__ == "__main__":
    unittest.main()
