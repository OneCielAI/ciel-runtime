import json
import unittest
from unittest import mock

import claude_any


class VllmProviderTests(unittest.TestCase):
    def test_context_guard_summary_chunks_scale_with_large_budget(self):
        omitted = [{"role": "user", "content": f"old message {idx}"} for idx in range(9)]

        with mock.patch.object(claude_any, "estimate_tokens", return_value=32768):
            self.assertEqual(9, claude_any.context_guard_chunk_count(omitted))
            self.assertEqual(3, claude_any.context_guard_chunk_count(omitted, 499712))

        with mock.patch.object(claude_any, "estimate_tokens", return_value=32768):
            summary = claude_any.build_chunked_context_guard_summary(omitted, 499712)

        self.assertIn("Chunk 3/3", summary)
        self.assertNotIn("Chunk 4/3", summary)

    def test_vllm_native_base_url_strips_v1_suffix(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])
        pcfg["base_url"] = "http://vllm.local:8000/v1"

        self.assertEqual("http://vllm.local:8000", claude_any.native_anthropic_base_url("vllm", pcfg))

    def test_vllm_native_false_routes_through_openai_compatible_forwarder(self):
        cfg = {
            "current_provider": "vllm",
            "providers": {"vllm": dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])},
            "router_debug_message_preview_chars": 0,
        }
        cfg["providers"]["vllm"]["native_compat"] = False
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
            mock.patch.object(claude_any, "body_with_pending_channel_messages", side_effect=lambda b: b),
            mock.patch.object(claude_any, "dump_request_for_trace"),
            mock.patch.object(claude_any, "forward_openai_compatible_chat") as forward,
        ):
            handler.do_POST()

        forward.assert_called_once()
        self.assertEqual("vllm", forward.call_args.args[1])

    def test_vllm_compatibility_probe_native_false_uses_chat_completions(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])
        pcfg["base_url"] = "http://vllm.local:8000/v1"
        pcfg["native_compat"] = False

        url, req_body, _headers = claude_any.compatibility_api_key_probe_request(
            "vllm",
            pcfg,
            "test-model",
            {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
        )

        self.assertEqual("http://vllm.local:8000/v1/chat/completions", url)
        self.assertEqual("test-model", req_body["model"])

    def test_system_role_messages_move_to_top_level_system(self):
        body = {
            "system": "Original system",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "system", "content": [{"type": "text", "text": "Runtime state"}]},
                {"role": "assistant", "content": "ok"},
            ],
        }

        normalized = claude_any.normalize_anthropic_system_role_messages(body)

        self.assertEqual(["user", "assistant"], [message["role"] for message in normalized["messages"]])
        system_text = claude_any.anthropic_content_to_text(normalized["system"])
        self.assertIn("Original system", system_text)
        self.assertIn("Runtime state", system_text)

    def test_vllm_native_router_normalizes_system_role_before_upstream(self):
        cfg = {
            "current_provider": "vllm",
            "providers": {"vllm": dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])},
            "router_debug_message_preview_chars": 0,
        }
        pcfg = cfg["providers"]["vllm"]
        pcfg["base_url"] = "http://vllm.local:8000"
        pcfg["native_compat"] = True
        pcfg["current_model"] = "test-model"
        handler = object.__new__(claude_any.RouterHandler)
        handler.path = "/v1/messages"
        handler.headers = {"content-length": "2"}
        handler.rfile = mock.Mock()
        handler.rfile.read.return_value = b"{}"
        handler.wfile = mock.Mock()
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()

        captured: dict[str, object] = {}

        class Response:
            status = 200
            headers = {"content-type": "application/json"}

            def read(self):
                return b'{"id":"msg","type":"message","role":"assistant","content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1}}'

        def request_spy(url, data=None, headers=None, method=None):
            captured["body"] = json.loads(data.decode("utf-8"))
            return mock.Mock()

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
            mock.patch.object(claude_any, "body_with_pending_channel_messages", side_effect=lambda b: b),
            mock.patch.object(claude_any, "body_with_pending_channel_summaries", side_effect=lambda b: b),
            mock.patch.object(claude_any, "body_with_channel_tool_result_context", side_effect=lambda b: b),
            mock.patch.object(claude_any, "dump_request_for_trace"),
            mock.patch.object(claude_any, "parse_json_body", return_value={
                "model": "claude-any-vllm-test-model",
                "max_tokens": 32,
                "stream": False,
                "system": "Original system",
                "messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "system", "content": [{"type": "text", "text": "Runtime state"}]},
                ],
            }),
            mock.patch.object(claude_any.urllib.request, "Request", side_effect=request_spy),
            mock.patch.object(claude_any.urllib.request, "urlopen", return_value=Response()),
            mock.patch.object(claude_any, "write_anthropic_message_response"),
        ):
            handler.do_POST()

        upstream_body = captured["body"]
        self.assertEqual(["user"], [message["role"] for message in upstream_body["messages"]])
        system_text = claude_any.anthropic_content_to_text(upstream_body["system"])
        self.assertIn("Original system", system_text)
        self.assertIn("Runtime state", system_text)

    def test_set_base_url_autodetects_openai_only_endpoint(self):
        cfg = {
            "current_provider": "vllm",
            "providers": {"vllm": dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])},
        }

        def route_exists(url, _headers, timeout=1.5):
            if url.endswith("/v1/messages"):
                return False
            if url.endswith("/v1/chat/completions"):
                return True
            return None

        with (
            mock.patch.object(claude_any, "load_config", return_value=cfg),
            mock.patch.object(claude_any, "save_config") as save,
            mock.patch.object(claude_any, "clear_model_cache"),
            mock.patch.object(claude_any, "endpoint_route_exists", side_effect=route_exists),
        ):
            lines = claude_any.set_base_url_config("vllm", "http://vllm.local:8000/v1")

        self.assertFalse(cfg["providers"]["vllm"]["native_compat"])
        self.assertTrue(any("Native compatibility disabled" in line for line in lines))
        save.assert_called_once()

    def test_set_base_url_keeps_anthropic_default_when_detection_is_inconclusive(self):
        cfg = {
            "current_provider": "vllm",
            "providers": {"vllm": dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])},
        }
        cfg["providers"]["vllm"]["native_compat"] = False

        with (
            mock.patch.object(claude_any, "load_config", return_value=cfg),
            mock.patch.object(claude_any, "save_config"),
            mock.patch.object(claude_any, "clear_model_cache"),
            mock.patch.object(claude_any, "endpoint_route_exists", return_value=None),
        ):
            lines = claude_any.set_base_url_config("vllm", "http://vllm.local:9000")

        self.assertTrue(cfg["providers"]["vllm"]["native_compat"])
        self.assertTrue(any("Anthropic default" in line for line in lines))

    def test_long_context_128k_preset_configures_vllm_range(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])

        lines = claude_any.apply_llm_preset_to_provider("vllm", pcfg, "long-context-128k", "en")

        self.assertEqual(131072, pcfg["context_window"])
        self.assertEqual(8192, pcfg["context_reserve_tokens"])
        self.assertEqual(8192, pcfg["max_output_tokens"])
        self.assertEqual("long-context-128k", pcfg["llm_preset"])
        self.assertTrue(any("Long context 128K" in line for line in lines))

    def test_long_context_256k_preset_configures_vllm_range(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])

        lines = claude_any.apply_llm_preset_to_provider("vllm", pcfg, "long-context-256k", "en")

        self.assertEqual(262144, pcfg["context_window"])
        self.assertEqual(8192, pcfg["context_reserve_tokens"])
        self.assertEqual(8192, pcfg["max_output_tokens"])
        self.assertEqual("long-context-256k", pcfg["llm_preset"])
        self.assertTrue(any("Long context 256K" in line for line in lines))

    def test_long_context_300k_preset_configures_vllm_range(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])

        lines = claude_any.apply_llm_preset_to_provider("vllm", pcfg, "long-context-300k", "en")

        self.assertEqual(307200, pcfg["context_window"])
        self.assertEqual(8192, pcfg["context_reserve_tokens"])
        self.assertEqual(8192, pcfg["max_output_tokens"])
        self.assertEqual("long-context-300k", pcfg["llm_preset"])
        self.assertTrue(any("Long context 300K" in line for line in lines))

    def test_long_context_512k_preset_configures_vllm_range(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])

        lines = claude_any.apply_llm_preset_to_provider("vllm", pcfg, "long-context-512k", "en")

        self.assertEqual(524288, pcfg["context_window"])
        self.assertEqual(16384, pcfg["context_reserve_tokens"])
        self.assertEqual(8192, pcfg["max_output_tokens"])
        self.assertEqual("long-context-512k", pcfg["llm_preset"])
        self.assertTrue(any("Long context 512K" in line for line in lines))

    def test_long_context_128k_preset_is_visible_even_when_capacity_lower(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])
        pcfg["max_model_len"] = 65536

        rows, values = claude_any.llm_preset_panel_rows("vllm", pcfg, "en")

        self.assertIn("long-context-128k", values)
        row = rows[values.index("long-context-128k")]
        self.assertIn("Long context 128K", row)
        self.assertIn("requires 128K", row)
        self.assertIn("server", row)

    def test_stored_preset_status_is_preserved_even_when_capacity_lower(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])
        pcfg["llm_preset"] = "long-context-128k"
        pcfg["max_model_len"] = 65536

        self.assertEqual("long-context-128k", claude_any.applied_preset_id("vllm", pcfg))

    def test_qwen36_35b_does_not_inherit_27b_65k_hint(self):
        self.assertIsNone(claude_any.model_context_hint_from_model_id("qwen36-35b-a3b-mtp-nvfp4"))

    def test_vllm_runtime_context_limit_overrides_model_hint(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])
        pcfg["current_model"] = "qwen36-35b-a3b-mtp-nvfp4"

        with mock.patch.object(claude_any, "upstream_model_context_limit", return_value=131072):
            self.assertEqual(131072, claude_any.provider_model_context_capacity("vllm", pcfg))
            claude_any.apply_llm_preset_to_provider("vllm", pcfg, "long-context-128k", "en")

        self.assertEqual(131072, pcfg["context_window"])

    def test_vllm_saved_max_model_len_overrides_model_hint_when_runtime_unavailable(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])
        pcfg["current_model"] = "qwen36-35b-a3b-mtp-nvfp4"
        pcfg["max_model_len"] = 131072

        with mock.patch.object(claude_any, "upstream_model_context_limit", return_value=None):
            self.assertEqual(131072, claude_any.provider_model_context_capacity("vllm", pcfg))
            claude_any.apply_llm_preset_to_provider("vllm", pcfg, "long-context-128k", "en")

        self.assertEqual(131072, pcfg["context_window"])

    def test_context_capacity_recommends_256k_300k_and_512k_presets(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])
        pcfg["max_model_len"] = 262144

        self.assertEqual("long-context-256k", claude_any.recommended_preset_id("vllm", pcfg))

        pcfg["max_model_len"] = 307200
        self.assertEqual("long-context-300k", claude_any.recommended_preset_id("vllm", pcfg))

        pcfg["max_model_len"] = 524288
        self.assertEqual("long-context-512k", claude_any.recommended_preset_id("vllm", pcfg))

    def test_model_info_from_response_extracts_context_size(self):
        data = {
            "data": [
                {
                    "id": "qwen36-35b-a3b-mtp-nvfp4",
                    "max_model_len": 131072,
                    "owned_by": "vllm",
                }
            ]
        }

        info = claude_any.model_info_from_response("vllm", data)

        self.assertEqual(131072, info["qwen36-35b-a3b-mtp-nvfp4"]["max_model_len"])
        self.assertEqual("vllm", info["qwen36-35b-a3b-mtp-nvfp4"]["owned_by"])

    def test_model_panel_shows_cached_context_size(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])
        model = "qwen36-35b-a3b-mtp-nvfp4"

        with (
            mock.patch.object(claude_any, "read_model_list_cache", return_value=[model]),
            mock.patch.object(claude_any, "read_model_info_cache", return_value={model: {"max_model_len": 131072}}),
        ):
            rows, values = claude_any.model_panel_rows("vllm", pcfg, fetch=False)

        row = rows[values.index(model)]
        self.assertIn("[ctx 128K]", row)

    def test_set_model_config_stores_cached_context_size(self):
        model = "qwen36-35b-a3b-mtp-nvfp4"
        cfg = {
            "current_provider": "vllm",
            "language": "en",
            "providers": {"vllm": dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])},
        }

        with (
            mock.patch.object(claude_any, "load_config", return_value=cfg),
            mock.patch.object(claude_any, "save_config"),
            mock.patch.object(claude_any, "clear_model_cache"),
            mock.patch.object(claude_any, "read_model_list_cache", return_value=[model]),
            mock.patch.object(claude_any, "read_model_info_cache", return_value={model: {"max_model_len": 131072}}),
            mock.patch.object(claude_any, "upstream_model_context_limit", return_value=None),
        ):
            messages = claude_any.set_model_config(model)

        pcfg = cfg["providers"]["vllm"]
        self.assertEqual(131072, pcfg["max_model_len"])
        self.assertTrue(any("Model context size: 128K" in message for message in messages))

    def test_long_context_128k_preset_configures_ollama_range(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["ollama-cloud"])

        lines = claude_any.apply_llm_preset_to_provider(
            "ollama-cloud",
            pcfg,
            "long-context-128k",
            "en",
            sync_ollama_context=False,
        )

        self.assertEqual("auto", pcfg["num_ctx"])
        self.assertEqual(65536, pcfg["num_ctx_min"])
        self.assertEqual(131072, pcfg["num_ctx_max"])
        self.assertEqual(8192, pcfg["ollama_options"]["num_predict"])
        self.assertEqual("long-context-128k", pcfg["llm_preset"])
        self.assertTrue(any("Long context 128K" in line for line in lines))

    def test_long_context_256k_300k_and_512k_presets_configure_ollama_ranges(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["ollama-cloud"])
        pcfg["current_model"] = "custom-ctx-model"

        claude_any.apply_llm_preset_to_provider(
            "ollama-cloud",
            pcfg,
            "long-context-256k",
            "en",
            sync_ollama_context=False,
        )
        self.assertEqual("auto", pcfg["num_ctx"])
        self.assertEqual(131072, pcfg["num_ctx_min"])
        self.assertEqual(262144, pcfg["num_ctx_max"])
        self.assertEqual(8192, pcfg["ollama_options"]["num_predict"])

        claude_any.apply_llm_preset_to_provider(
            "ollama-cloud",
            pcfg,
            "long-context-300k",
            "en",
            sync_ollama_context=False,
        )
        self.assertEqual("auto", pcfg["num_ctx"])
        self.assertEqual(131072, pcfg["num_ctx_min"])
        self.assertEqual(307200, pcfg["num_ctx_max"])
        self.assertEqual(8192, pcfg["ollama_options"]["num_predict"])

        claude_any.apply_llm_preset_to_provider(
            "ollama-cloud",
            pcfg,
            "long-context-512k",
            "en",
            sync_ollama_context=False,
        )
        self.assertEqual("auto", pcfg["num_ctx"])
        self.assertEqual(262144, pcfg["num_ctx_min"])
        self.assertEqual(524288, pcfg["num_ctx_max"])
        self.assertEqual(8192, pcfg["ollama_options"]["num_predict"])

    def test_vllm_tool_choice_is_off_by_default(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])
        body = {
            "model": "qwen36-35b-a3b-mtp-nvfp4",
            "tools": [{"name": "Bash", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "auto"},
        }

        out = claude_any.normalize_tool_choice_for_provider("vllm", pcfg, body)

        self.assertIn("tool_choice", body)
        self.assertNotIn("tool_choice", out)
        self.assertIn("tools", out)

    def test_vllm_tool_choice_can_be_enabled_for_server_parser(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])
        claude_any.apply_provider_option("vllm", pcfg, "supports_tool_choice=true")
        body = {
            "model": "qwen36-35b-a3b-mtp-nvfp4",
            "tools": [{"name": "Bash", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "auto"},
        }

        out = claude_any.normalize_tool_choice_for_provider("vllm", pcfg, body)

        self.assertIs(out, body)
        self.assertIn("tool_choice", out)

    def test_vllm_tool_choice_option_visible_in_llm_menu(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])

        rows, values = claude_any.llm_option_panel_rows("vllm", pcfg, "en")

        self.assertIn("supports_tool_choice", values)
        self.assertIn("off", rows[values.index("supports_tool_choice")])

    def test_small_context_output_cap_for_vllm_launch_env(self):
        cfg = {
            "current_provider": "vllm",
            "providers": {"vllm": dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])},
        }
        pcfg = cfg["providers"]["vllm"]
        pcfg["context_window"] = 262144
        pcfg["max_model_len"] = 262144
        pcfg["max_output_tokens"] = 32768

        env = claude_any.env_vars(cfg)

        self.assertEqual("8192", env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"])

    def test_small_context_output_cap_rounds_down_for_200k_custom_model(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])
        pcfg["context_window"] = 200000
        pcfg["max_model_len"] = 200000
        pcfg["max_output_tokens"] = 32768

        self.assertEqual(6144, claude_any.claude_code_output_token_limit("vllm", pcfg))

    def test_vllm_context_budget_prefers_runtime_model_limit_over_stale_window(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])
        pcfg["current_model"] = "qwen36-35b-a3b-mtp-nvfp4"
        pcfg["context_window"] = 200000
        pcfg.pop("max_model_len", None)

        with mock.patch.object(claude_any, "upstream_model_context_limit", return_value=262144):
            self.assertEqual(262144, claude_any.openai_context_limit_for_budget("vllm", pcfg))
            self.assertEqual(262144, claude_any.context_limit_for_status("vllm", pcfg))
            self.assertEqual(8192, claude_any.claude_code_output_token_limit("vllm", {**pcfg, "max_output_tokens": 32768}))
            self.assertEqual(262144, claude_any.claude_code_auto_compact_window("vllm", pcfg))

    def test_vllm_anthropic_body_cap_uses_runtime_model_limit_over_stale_window(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])
        pcfg["current_model"] = "qwen36-35b-a3b-mtp-nvfp4"
        pcfg["context_window"] = 200000
        pcfg.pop("max_model_len", None)
        pcfg["max_output_tokens"] = 32768
        body = {
            "model": "qwen36-35b-a3b-mtp-nvfp4",
            "max_tokens": 32768,
            "messages": [{"role": "user", "content": "hi"}],
        }

        with mock.patch.object(claude_any, "upstream_model_context_limit", return_value=262144):
            out = claude_any.cap_anthropic_body_for_provider("vllm", pcfg, body)

        self.assertEqual(8192, out["max_tokens"])

    def test_vllm_native_compacts_large_anthropic_history_before_upstream(self):
        pcfg = dict(claude_any.DEFAULT_CONFIG["providers"]["vllm"])
        pcfg["context_window"] = 32768
        pcfg["max_model_len"] = 32768
        pcfg["max_output_tokens"] = 32768
        old_messages = []
        for idx in range(60):
            old_messages.append({"role": "user", "content": f"old user {idx} " + ("x" * 4000)})
            old_messages.append({"role": "assistant", "content": f"old assistant {idx} " + ("y" * 4000)})
        body = {
            "model": "qwen36-35b-a3b-mtp-nvfp4",
            "system": "system prompt",
            "max_tokens": 32768,
            "messages": old_messages + [{"role": "user", "content": "current task"}],
        }

        with mock.patch.object(claude_any, "write_context_compact_activity") as write_compact:
            out = claude_any.cap_anthropic_body_for_provider("vllm", pcfg, body)

        self.assertLess(len(out["messages"]), len(body["messages"]))
        self.assertEqual({"role": "user", "content": "current task"}, out["messages"][-1])
        self.assertEqual(2048, out["max_tokens"])
        self.assertLessEqual(claude_any.estimate_tokens(out), 32768)
        system_text = claude_any.anthropic_content_to_text(out["system"])
        self.assertIn("claude-any context guard", system_text)
        self.assertIn("Chunk", system_text)
        write_compact.assert_called_once()
        self.assertEqual("vllm", write_compact.call_args.args[0])
        self.assertGreater(write_compact.call_args.kwargs["chunks"], 0)
        self.assertEqual(1, write_compact.call_args.kwargs["parallel_sessions"])


if __name__ == "__main__":
    unittest.main()
