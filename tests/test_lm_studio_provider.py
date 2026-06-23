import unittest
from unittest import mock

import claude_any


class LMStudioProviderTests(unittest.TestCase):
    def test_provider_is_registered_with_local_defaults(self):
        for alias in ("lm-studio", "lmstudio", "lm"):
            self.assertEqual("lm-studio", claude_any.PROVIDER_ALIASES[alias])
        self.assertEqual("LM Studio", claude_any.PROVIDER_LABELS["lm-studio"])
        pcfg = claude_any.DEFAULT_CONFIG["providers"]["lm-studio"]
        self.assertEqual("http://127.0.0.1:1234/v1", pcfg["base_url"])
        self.assertEqual("local-model", pcfg["current_model"])
        self.assertTrue(claude_any.provider_native_compat_enabled("lm-studio", pcfg))

    def test_default_base_url_points_to_lm_studio_api(self):
        self.assertEqual("http://127.0.0.1:1234/v1", claude_any.default_base_url("lm-studio"))
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["lm-studio"])
        self.assertEqual("http://127.0.0.1:1234", claude_any.native_anthropic_base_url("lm-studio", pcfg))
        self.assertEqual(
            "http://127.0.0.1:1234/v1/messages",
            claude_any.join_url(claude_any.native_anthropic_base_url("lm-studio", pcfg), "/v1/messages"),
        )
        self.assertEqual(
            "http://127.0.0.1:1234/v1/chat/completions",
            claude_any.join_url(claude_any.default_base_url("lm-studio"), "/v1/chat/completions"),
        )
        self.assertEqual(
            "http://127.0.0.1:1234/v1/models",
            claude_any.join_url(claude_any.default_base_url("lm-studio"), "/v1/models"),
        )

    def test_headers_do_not_send_dummy_auth_for_local_server(self):
        headers = claude_any.provider_headers("lm-studio", {"api_key": ""})
        self.assertNotIn("authorization", headers)
        self.assertNotIn("x-api-key", headers)
        self.assertEqual("claude-cli", headers["user-agent"])

        keyed = claude_any.provider_headers("lm-studio", {"api_key": "lm-key"})
        self.assertEqual("Bearer lm-key", keyed["authorization"])
        self.assertEqual("lm-key", keyed["x-api-key"])
        self.assertEqual("claude-cli", keyed["user-agent"])

    def test_openai_compatible_request_for_lm_studio(self):
        body = {
            "model": "claude-any-lm-studio-local-model",
            "max_tokens": 1234,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
        }
        pcfg = {"context_window": 32768, "temperature": 0.2, "top_p": 0.9}

        request = claude_any.openai_compatible_chat_request("lm-studio", "local-model", body, pcfg, stream=False)

        self.assertEqual("local-model", request["model"])
        self.assertFalse(request["stream"])
        self.assertEqual(1234, request["max_tokens"])
        self.assertEqual(0.2, request["temperature"])
        self.assertEqual(0.9, request["top_p"])
        self.assertTrue(any(message.get("role") == "user" for message in request["messages"]))

    def test_lm_studio_qwen36_id_uses_qwen36_27b_preset(self):
        self.assertEqual(
            claude_any.MODEL_PRESETS["qwen3.6:27b"],
            claude_any.model_preset("qwen3.6-27b-mtp"),
        )
        self.assertEqual(
            claude_any.MODEL_PRESETS["qwen3.6:27b"],
            claude_any.model_preset("wyvern-qwen36-27b"),
        )

    def test_lm_studio_qwen36_id_uses_catalog_context_alias(self):
        catalog = {
            "source": "test-catalog",
            "models": {
                "qwen3.6": {
                    "context_windows": {"27b": 262144},
                    "recommended_timeout_ms_by_tag": {"27b": 120000},
                }
            },
        }

        with mock.patch.object(claude_any, "load_ollama_model_catalog", return_value=catalog):
            context, matched, source = claude_any.ollama_catalog_context_for_model("qwen3.6-27b-mtp")
            timeout = claude_any.ollama_catalog_timeout_for_model("qwen3.6-27b-mtp")

        self.assertEqual(262144, context)
        self.assertEqual("qwen3.6:27b", matched)
        self.assertEqual("test-catalog", source)
        self.assertEqual(120000, timeout)

    def test_lm_studio_runtime_info_reads_loaded_context_from_api_v0(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["lm-studio"])
        pcfg["base_url"] = "http://lmstudio.local:1234/v1"
        pcfg["current_model"] = "qwen3.6-35b-a3b-mtp@bf16"
        payload = {
            "data": [
                {
                    "id": "qwen3.6-35b-a3b-mtp@bf16",
                    "state": "loaded",
                    "max_context_length": 262144,
                    "loaded_context_length": 4096,
                    "capabilities": ["tool_use"],
                }
            ]
        }

        with mock.patch.object(claude_any, "http_json", return_value=payload) as http_json:
            info = claude_any.upstream_model_runtime_info("lm-studio", pcfg)

        self.assertEqual("http://lmstudio.local:1234/api/v0/models", http_json.call_args.args[0])
        self.assertEqual(262144, info["max_model_len"])
        self.assertEqual(4096, info["loaded_context_len"])
        self.assertEqual("loaded", info["state"])

    def test_lm_studio_model_panel_uses_cache_without_fetching(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["lm-studio"])
        pcfg["current_model"] = "qwen3.6-35b-a3b-mtp@q4_k_m"
        pcfg["custom_models"] = ["wyvern-qwen36-27b"]

        with (
            mock.patch.object(claude_any, "read_model_list_cache", return_value=None),
            mock.patch.object(claude_any, "http_json") as http_json,
        ):
            rows, values = claude_any.model_panel_rows("lm-studio", pcfg, fetch=False)

        http_json.assert_not_called()
        self.assertEqual("__refresh_models__", values[0])
        self.assertIn("qwen3.6-35b-a3b-mtp@q4_k_m", values)
        self.assertIn("wyvern-qwen36-27b", values)
        self.assertTrue(any("Refresh provider model list" in row for row in rows))

    def test_ollama_cloud_model_panel_uses_catalog_without_fetching(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["ollama-cloud"])
        pcfg["current_model"] = ""
        pcfg["custom_models"] = []
        catalog = {
            "models": {
                "deepseek-v4-pro": {
                    "id": "deepseek-v4-pro",
                    "models": ["deepseek-v4-pro:cloud"],
                    "tags": ["cloud"],
                },
                "glm-5.1": {
                    "id": "glm-5.1",
                    "models": ["glm-5.1:cloud"],
                    "tags": ["cloud"],
                },
            }
        }

        with (
            mock.patch.object(claude_any, "read_model_list_cache", return_value=None),
            mock.patch.object(claude_any, "load_ollama_model_catalog", return_value=catalog),
            mock.patch.object(claude_any, "http_json") as http_json,
        ):
            rows, values = claude_any.model_panel_rows("ollama-cloud", pcfg, fetch=False)

        http_json.assert_not_called()
        self.assertEqual("__refresh_models__", values[0])
        self.assertIn("deepseek-v4-pro", values)
        self.assertIn("glm-5.1", values)
        self.assertNotIn("deepseek-v4-pro:cloud", values)
        self.assertTrue(any("Refresh provider model list" in row for row in rows))

    def test_ollama_cloud_model_refresh_falls_back_to_catalog(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["ollama-cloud"])
        pcfg["current_model"] = ""
        pcfg["custom_models"] = []
        catalog = {
            "models": {
                "deepseek-v4-pro": {
                    "id": "deepseek-v4-pro",
                    "models": ["deepseek-v4-pro:cloud"],
                    "tags": ["cloud"],
                }
            }
        }

        with (
            mock.patch.object(claude_any, "read_model_list_cache", return_value=None),
            mock.patch.object(claude_any, "load_ollama_model_catalog", return_value=catalog),
            mock.patch.object(claude_any, "write_model_list_cache") as write_cache,
            mock.patch.object(claude_any, "http_json", side_effect=RuntimeError("offline")),
        ):
            models = claude_any.upstream_model_ids("ollama-cloud", pcfg)

        self.assertIn("deepseek-v4-pro", models)
        write_cache.assert_called_once()

    def test_ollama_cloud_launch_env_sets_auth_token_for_claude_login_gate(self):
        cfg = {
            "current_provider": "ollama-cloud",
            "providers": {"ollama-cloud": dict(claude_any.DEFAULT_CONFIG["providers"]["ollama-cloud"])},
        }
        cfg["providers"]["ollama-cloud"]["api_key"] = "ollama-cloud-key"

        env = claude_any.env_vars(cfg)

        self.assertEqual(claude_any.ROUTER_BASE, env["ANTHROPIC_BASE_URL"])
        self.assertEqual("ollama-cloud-key", env["ANTHROPIC_AUTH_TOKEN"])
        self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_lm_studio_set_model_does_not_fetch_model_list(self):
        cfg = {
            "current_provider": "lm-studio",
            "providers": {"lm-studio": dict(claude_any.DEFAULT_CONFIG["providers"]["lm-studio"])},
        }
        with (
            mock.patch.object(claude_any, "load_config", return_value=cfg),
            mock.patch.object(claude_any, "save_config"),
            mock.patch.object(claude_any, "clear_model_cache"),
            mock.patch.object(claude_any, "read_model_list_cache", return_value=None),
            mock.patch.object(claude_any, "http_json") as http_json,
        ):
            messages = claude_any.set_model_config("qwen3.6-35b-a3b-mtp@q4_k_m")

        http_json.assert_not_called()
        self.assertEqual("qwen3.6-35b-a3b-mtp@q4_k_m", cfg["providers"]["lm-studio"]["current_model"])
        self.assertTrue(any("Model for lm-studio set" in message for message in messages))

    def test_lm_studio_model_list_prefers_fast_api_v0_endpoint(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["lm-studio"])
        pcfg["base_url"] = "http://lmstudio.local:1234/v1"
        payload = {"data": [{"id": "qwen3.6-27b-mtp"}, {"id": "wyvern-qwen36-27b"}]}

        with (
            mock.patch.object(claude_any, "read_model_list_cache", return_value=None),
            mock.patch.object(claude_any, "write_model_list_cache"),
            mock.patch.object(claude_any, "http_json", return_value=payload) as http_json,
        ):
            models = claude_any.upstream_model_ids("lm-studio", pcfg)

        self.assertEqual("http://lmstudio.local:1234/api/v0/models", http_json.call_args.args[0])
        self.assertIn("qwen3.6-27b-mtp", models)
        self.assertIn("wyvern-qwen36-27b", models)

    def test_lm_studio_loaded_context_guard_defers_reload_during_menu_selection(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["lm-studio"])
        pcfg["current_model"] = "qwen3.6-35b-a3b-mtp@bf16"
        pcfg["native_compat"] = True
        pcfg["context_window"] = 65536
        with (
            mock.patch.object(claude_any, "http_json") as http_json,
            mock.patch.object(claude_any, "post_json") as post_json,
        ):
            messages = claude_any.apply_lm_studio_loaded_context_guard(pcfg)

        self.assertTrue(pcfg["native_compat"])
        self.assertEqual(65536, pcfg["context_window"])
        http_json.assert_not_called()
        post_json.assert_not_called()
        self.assertTrue(any("will prepare" in message for message in messages))

    def test_lm_studio_loaded_context_guard_can_reload_when_requested(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["lm-studio"])
        pcfg["current_model"] = "qwen3.6-35b-a3b-mtp@bf16"
        pcfg["native_compat"] = True
        pcfg["context_window"] = 65536
        payload = {
            "data": [
                {
                    "id": "qwen3.6-35b-a3b-mtp@bf16",
                    "state": "loaded",
                    "max_context_length": 262144,
                    "loaded_context_length": 4096,
                }
            ]
        }

        with (
            mock.patch.object(claude_any, "http_json", return_value=payload),
            mock.patch.object(
                claude_any,
                "post_json",
                return_value={"status": "loaded", "load_config": {"context_length": 65536}},
            ) as post_json,
        ):
            messages = claude_any.apply_lm_studio_loaded_context_guard(pcfg, load=True)

        self.assertTrue(pcfg["native_compat"])
        self.assertEqual(65536, pcfg["context_window"])
        self.assertEqual("http://127.0.0.1:1234/api/v1/models/load", post_json.call_args.args[0])
        self.assertEqual(65536, post_json.call_args.args[1]["context_length"])
        self.assertTrue(any("auto-reloading" in message for message in messages))

    def test_lm_studio_loaded_context_guard_keeps_native_when_large_enough(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["lm-studio"])
        pcfg["current_model"] = "qwen3.6-35b-a3b-mtp@bf16"
        pcfg["native_compat"] = False
        pcfg["context_window"] = 65536
        payload = {
            "data": [
                {
                    "id": "qwen3.6-35b-a3b-mtp@bf16",
                    "state": "loaded",
                    "max_context_length": 262144,
                    "loaded_context_length": 65536,
                }
            ]
        }

        with mock.patch.object(claude_any, "http_json", return_value=payload):
            claude_any.apply_lm_studio_loaded_context_guard(pcfg)

        self.assertTrue(pcfg["native_compat"])
        self.assertEqual(65536, pcfg["context_window"])

    def test_lm_studio_loaded_context_guard_loads_when_not_loaded(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["lm-studio"])
        pcfg["current_model"] = "qwen3.6-27b-mtp"
        pcfg["native_compat"] = True
        pcfg["context_window"] = 65536
        payload = {
            "data": [
                {
                    "id": "qwen3.6-27b-mtp",
                    "state": "not-loaded",
                    "max_context_length": 262144,
                }
            ]
        }

        with (
            mock.patch.object(claude_any, "http_json", return_value=payload),
            mock.patch.object(
                claude_any,
                "post_json",
                return_value={"status": "loaded", "load_config": {"context_length": 65536}},
            ) as post_json,
        ):
            messages = claude_any.apply_lm_studio_loaded_context_guard(pcfg, load=True)

        self.assertTrue(pcfg["native_compat"])
        self.assertEqual(65536, pcfg["context_window"])
        self.assertEqual("qwen3.6-27b-mtp", post_json.call_args.args[1]["model"])
        self.assertTrue(any("auto-loading" in message for message in messages))

    def test_lm_studio_launch_self_heals_small_loaded_context(self):
        cfg = {
            "current_provider": "lm-studio",
            "providers": {"lm-studio": dict(claude_any.DEFAULT_CONFIG["providers"]["lm-studio"])},
        }
        cfg["providers"]["lm-studio"]["current_model"] = "qwen3.6-35b-a3b-mtp@bf16"
        cfg["providers"]["lm-studio"]["context_window"] = 65536
        small_payload = {
            "data": [
                {
                    "id": "qwen3.6-35b-a3b-mtp@bf16",
                    "state": "loaded",
                    "max_context_length": 262144,
                    "loaded_context_length": 4096,
                }
            ]
        }
        healed_payload = {
            "data": [
                {
                    "id": "qwen3.6-35b-a3b-mtp@bf16",
                    "state": "loaded",
                    "max_context_length": 262144,
                    "loaded_context_length": 65536,
                }
            ]
        }

        with (
            mock.patch.object(claude_any, "load_config", return_value=cfg),
            mock.patch.object(claude_any, "base_url_status_line", return_value="Base URL: model list reachable (/v1/models)"),
            mock.patch.object(claude_any, "http_json", side_effect=[small_payload, healed_payload]),
            mock.patch.object(
                claude_any,
                "post_json",
                return_value={"status": "loaded", "load_config": {"context_length": 65536}},
            ),
            mock.patch.object(claude_any, "save_config"),
        ):
            errors = claude_any.launch_readiness_errors()

        self.assertEqual([], errors)

    def test_lm_studio_options_are_provider_specific(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["lm-studio"])

        status = claude_any.llm_options_status("lm-studio", pcfg)
        self.assertIn("context_window=32768", status)
        self.assertIn("reserve=1024", status)
        self.assertIn("stream=on", status)
        self.assertIn("native=True", status)

        with mock.patch.object(claude_any, "load_config", return_value={}):
            rows, values = claude_any.llm_option_panel_rows("lm-studio", pcfg, "en")
        self.assertIn("context_window", values)
        self.assertIn("context_reserve_tokens", values)
        self.assertIn("temperature", values)
        self.assertIn("stream_enabled", values)
        self.assertIn("native_compat", values)
        self.assertTrue(any("Context window" in row for row in rows))

        context_rows, context_values = claude_any.context_setup_panel_rows("lm-studio", pcfg, "en")
        self.assertTrue(any(value.startswith("context-") for value in context_values))
        self.assertFalse(any("managed by Claude Code" in row for row in context_rows))

    def test_lm_studio_launch_env_routes_through_claude_any_router(self):
        cfg = {
            "current_provider": "lm-studio",
            "providers": {"lm-studio": dict(claude_any.DEFAULT_CONFIG["providers"]["lm-studio"])},
        }
        pcfg = cfg["providers"]["lm-studio"]

        env = claude_any.env_vars(cfg)

        self.assertEqual(claude_any.ROUTER_BASE, env["ANTHROPIC_BASE_URL"])
        self.assertEqual("not-used", env["ANTHROPIC_AUTH_TOKEN"])
        self.assertEqual(claude_any.current_alias(cfg), env["ANTHROPIC_MODEL"])
        self.assertNotEqual(claude_any.native_anthropic_base_url("lm-studio", pcfg), env["ANTHROPIC_BASE_URL"])
        self.assertEqual("lm-studio", env["CLAUDE_ANY_PROVIDER"])

    def test_lm_studio_routes_through_openai_compatible_forwarder_when_native_disabled(self):
        cfg = {
            "current_provider": "lm-studio",
            "providers": {"lm-studio": dict(claude_any.DEFAULT_CONFIG["providers"]["lm-studio"])},
            "router_debug_message_preview_chars": 0,
        }
        cfg["providers"]["lm-studio"]["native_compat"] = False
        handler = object.__new__(claude_any.RouterHandler)
        handler.path = "/v1/messages"
        handler.headers = {"content-length": "2"}
        handler.rfile = mock.Mock()
        handler.rfile.read.return_value = b"{}"

        with (
            mock.patch.object(claude_any, "load_config", return_value=cfg),
            mock.patch.object(claude_any, "reject_external_router_request", return_value=False),
            mock.patch.object(claude_any, "handle_llm_config_post", return_value=False),
            mock.patch.object(claude_any, "handle_channel_mcp_post", return_value=False),
            mock.patch.object(claude_any, "handle_chat_post", return_value=False),
            mock.patch.object(claude_any, "handle_plan_post", return_value=False),
            mock.patch.object(claude_any, "maybe_handle_plan_mode_tool_choice", return_value=False),
            mock.patch.object(claude_any, "filter_blocked_tools", side_effect=lambda _p, _c, b: b),
            mock.patch.object(claude_any, "write_context_usage"),
            mock.patch.object(claude_any, "maybe_handle_router_debug_request", return_value=False),
            mock.patch.object(claude_any, "maybe_handle_advisor_request", return_value=False),
            mock.patch.object(
                claude_any,
                "body_with_pending_channel_messages",
                side_effect=lambda b: {**b, "messages": [{"role": "user", "content": "channel notice"}]},
            ) as inject_channels,
            mock.patch.object(claude_any, "dump_request_for_trace"),
            mock.patch.object(claude_any, "forward_openai_compatible_chat") as forward,
        ):
            handler.do_POST()

        forward.assert_called_once()
        inject_channels.assert_called_once()
        self.assertIs(forward.call_args.args[0], handler)
        self.assertEqual("lm-studio", forward.call_args.args[1])
        self.assertEqual([{"role": "user", "content": "channel notice"}], forward.call_args.args[3]["messages"])


if __name__ == "__main__":
    unittest.main()
