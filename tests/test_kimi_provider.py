import copy
import io
import unittest
from unittest import mock

import ciel_runtime


class KimiProviderTests(unittest.TestCase):
    def kimi_cfg(self, **overrides):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["kimi"])
        pcfg.update(overrides)
        return {
            "current_provider": "kimi",
            "providers": {
                "kimi": pcfg,
            },
        }

    def test_provider_is_registered(self):
        self.assertEqual("kimi", ciel_runtime.PROVIDER_ALIASES["kimi.com"])
        self.assertEqual("kimi", ciel_runtime.PROVIDER_ALIASES["kimi-code"])
        self.assertEqual("kimi", ciel_runtime.PROVIDER_ALIASES["moonshot"])
        self.assertEqual("Kimi.com", ciel_runtime.PROVIDER_LABELS["kimi"])
        self.assertEqual(ciel_runtime.KIMI_CODING_BASE_URL, ciel_runtime.default_base_url("kimi"))

    def test_default_config_matches_kimi_third_party_agent_docs(self):
        pcfg = ciel_runtime.DEFAULT_CONFIG["providers"]["kimi"]
        self.assertEqual("https://api.kimi.com/coding", pcfg["base_url"])
        self.assertEqual("kimi-for-coding", pcfg["current_model"])
        self.assertIn("k3", pcfg["custom_models"])
        self.assertIn("k3[1m]", pcfg["custom_models"])
        self.assertIn("kimi-for-coding-highspeed", pcfg["custom_models"])
        self.assertEqual(262144, pcfg["context_window"])
        self.assertEqual(32768, pcfg["max_output_tokens"])
        self.assertEqual(32768, pcfg["context_reserve_tokens"])
        self.assertEqual(600000, pcfg["request_timeout_ms"])
        self.assertTrue(pcfg["native_compat"])
        self.assertTrue(pcfg["preserve_anthropic_thinking"])
        self.assertTrue(pcfg["normalize_anthropic_tool_use"])
        self.assertTrue(pcfg["supports_tool_choice"])
        self.assertIn("thinking", pcfg["claude_code_supported_capabilities"])
        self.assertEqual("high", pcfg["effort_level"])

    def test_kimi_endpoint_policy_splits_claude_and_codex_protocols(self):
        pcfg = self.kimi_cfg()["providers"]["kimi"]

        self.assertTrue(ciel_runtime.provider_native_compat_enabled("kimi", pcfg))
        self.assertFalse(ciel_runtime.provider_openai_router_enabled("kimi", pcfg))
        self.assertTrue(ciel_runtime.codex_openai_router_enabled("kimi", pcfg))
        self.assertEqual(
            "https://api.kimi.com/coding/v1/messages",
            ciel_runtime.join_url(ciel_runtime.native_anthropic_base_url("kimi", pcfg), "/v1/messages"),
        )
        self.assertEqual(
            "https://api.kimi.com/coding/v1/chat/completions",
            ciel_runtime.join_url(ciel_runtime.provider_upstream_request_base("kimi", pcfg), "/v1/chat/completions"),
        )

    def test_launch_endpoint_policy_switches_kimi_for_selected_runtime(self):
        cfg = self.kimi_cfg()
        pcfg = cfg["providers"]["kimi"]
        pcfg["native_compat"] = False

        with (
            mock.patch.object(ciel_runtime, "save_config") as save,
            mock.patch.object(ciel_runtime, "clear_model_cache") as clear_cache,
        ):
            claude_lines = ciel_runtime.apply_launch_endpoint_policy(cfg, "claude")

        self.assertTrue(pcfg["native_compat"])
        self.assertTrue(any("Anthropic Messages" in line for line in claude_lines))
        save.assert_called_once_with(cfg)
        clear_cache.assert_called_once()

        with (
            mock.patch.object(ciel_runtime, "save_config") as save,
            mock.patch.object(ciel_runtime, "clear_model_cache") as clear_cache,
        ):
            codex_lines = ciel_runtime.apply_launch_endpoint_policy(cfg, "codex")

        self.assertFalse(pcfg["native_compat"])
        self.assertTrue(any("OpenAI Chat" in line for line in codex_lines))
        save.assert_called_once_with(cfg)
        clear_cache.assert_called_once()

    def test_kimi_aliases_normalize_to_documented_model_id(self):
        for raw in (
            "kimi-code/kimi-for-coding",
            "moonshot/kimi-for-coding",
            "kimi-k2.7-code",
            "k2.7-code",
        ):
            self.assertEqual("kimi-for-coding", ciel_runtime.normalize_model_id("kimi", raw))

        for raw in ("k3", "kimi-k3", "kimi/k3", "kimi-code/k3"):
            self.assertEqual("k3", ciel_runtime.normalize_model_id("kimi", raw))

        self.assertEqual("k3[1m]", ciel_runtime.normalize_model_id("kimi", "k3[1m]"))
        self.assertEqual("k3", ciel_runtime.upstream_api_model_id("kimi", "k3[1m]"))
        self.assertEqual(
            "kimi-for-coding-highspeed",
            ciel_runtime.normalize_model_id("kimi", "kimi-code/kimi-for-coding-highspeed"),
        )

    def test_k3_profile_defaults_to_documented_256k_and_high_effort(self):
        pcfg = self.kimi_cfg(current_model="k3")["providers"]["kimi"]

        messages = ciel_runtime.apply_kimi_model_profile("kimi", pcfg)

        self.assertEqual(262144, pcfg["context_window"])
        self.assertEqual(262144, pcfg["max_model_len"])
        self.assertEqual("high", pcfg["effort_level"])
        self.assertEqual(262144, ciel_runtime.provider_model_context_capacity("kimi", pcfg))
        self.assertIn("max_effort", ciel_runtime.claude_code_supported_capabilities("kimi", pcfg))
        self.assertTrue(any("256K context" in message for message in messages))
        self.assertTrue(any("new session" in message for message in messages))

    def test_k3_1m_profile_requires_explicit_context_variant(self):
        pcfg = self.kimi_cfg(current_model="k3[1m]")["providers"]["kimi"]

        messages = ciel_runtime.apply_kimi_model_profile("kimi", pcfg)

        self.assertEqual(1048576, pcfg["context_window"])
        self.assertEqual(1048576, pcfg["max_model_len"])
        self.assertEqual("high", pcfg["effort_level"])
        self.assertEqual(1048576, ciel_runtime.provider_model_context_capacity("kimi", pcfg))
        self.assertTrue(any("1M context" in message for message in messages))

    def test_highspeed_profile_is_visible_with_quota_and_plan_notice(self):
        pcfg = self.kimi_cfg(current_model="kimi-for-coding-highspeed")["providers"]["kimi"]

        messages = ciel_runtime.apply_kimi_model_profile("kimi", pcfg)
        ciel_runtime.apply_provider_model_selection_updates(
            "kimi", pcfg, "kimi-for-coding-highspeed"
        )

        self.assertEqual(262144, pcfg["context_window"])
        self.assertEqual(262144, pcfg["max_model_len"])
        self.assertTrue(any("Allegretto" in message and "3x quota" in message for message in messages))
        self.assertEqual("kimi-for-coding-highspeed", pcfg["subagent_model"])

    def test_k3_anthropic_request_preserves_official_effort_mapping(self):
        pcfg = self.kimi_cfg(current_model="k3")["providers"]["kimi"]
        body = {
            "model": "ciel-runtime-kimi-k3",
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "enabled", "effort": "high"},
        }

        out = ciel_runtime.normalize_request_for_provider_wire("kimi", pcfg, body)

        self.assertEqual("high", out["thinking"]["effort"])
        self.assertEqual("high", body["thinking"]["effort"])

        for source, expected in (("low", "low"), ("medium", "high"), ("xhigh", "max"), ("unknown", "high")):
            mapped = ciel_runtime.normalize_request_for_provider_wire(
                "kimi", pcfg, {"model": "ciel-runtime-kimi-k3", "thinking": {"type": "enabled", "effort": source}}
            )
            self.assertEqual(expected, mapped["thinking"]["effort"])

    def test_k3_openai_request_uses_selected_reasoning_effort(self):
        pcfg = self.kimi_cfg(current_model="k3")["providers"]["kimi"]
        body = {"messages": [{"role": "user", "content": "hello"}]}

        request = ciel_runtime.openai_compatible_chat_request("kimi", "k3", body, pcfg)

        self.assertEqual("high", request["reasoning_effort"])
        pcfg["effort_level"] = "xhigh"
        request = ciel_runtime.openai_compatible_chat_request("kimi", "k3", body, pcfg)
        self.assertEqual("max", request["reasoning_effort"])

    def test_kimi_protects_thinking_and_removes_fixed_sampling_overrides(self):
        pcfg = self.kimi_cfg(current_model="k3", temperature=0.2, top_p=0.8)["providers"]["kimi"]
        body = {
            "model": "ciel-runtime-kimi-k3",
            "messages": [],
            "thinking": {"type": "disabled"},
            "temperature": 0.1,
            "top_p": 0.5,
            "n": 2,
        }

        normalized = ciel_runtime.normalize_request_for_provider_wire("kimi", pcfg, body)
        request = ciel_runtime.openai_compatible_chat_request("kimi", "k3", normalized, pcfg)

        self.assertEqual("enabled", normalized["thinking"]["type"])
        self.assertEqual("high", normalized["thinking"]["effort"])
        for key in ("temperature", "top_p", "n"):
            self.assertNotIn(key, normalized)
            self.assertNotIn(key, request)

    def test_provider_headers_include_kimi_api_key(self):
        pcfg = self.kimi_cfg(api_key="sk-kimi-test")["providers"]["kimi"]

        headers = ciel_runtime.provider_headers("kimi", pcfg)

        self.assertEqual("Bearer sk-kimi-test", headers["authorization"])
        self.assertEqual("sk-kimi-test", headers["x-api-key"])
        self.assertEqual("2023-06-01", headers["anthropic-version"])
        self.assertEqual("claude-cli", headers["user-agent"])

    def test_model_list_fetches_kimi_coding_models_and_caches_specs(self):
        pcfg = self.kimi_cfg(api_key="sk-kimi-test", custom_models=[])["providers"]["kimi"]
        response = {
            "data": [
                {
                    "id": "kimi-for-coding",
                    "context_length": 262144,
                    "owned_by": "kimi-code",
                }
            ]
        }

        with (
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=None),
            mock.patch.object(ciel_runtime, "http_json", return_value=response) as http_json,
            mock.patch.object(ciel_runtime, "write_model_list_cache") as write_cache,
        ):
            models = ciel_runtime.upstream_model_ids("kimi", pcfg)

        self.assertEqual(["kimi-for-coding"], models)
        self.assertTrue(http_json.call_args.args[0].endswith("/coding/v1/models"))
        write_cache.assert_called_once()
        metadata = write_cache.call_args.args[3]
        self.assertEqual(262144, metadata["model_info"]["kimi-for-coding"]["max_model_len"])

    def test_model_list_falls_back_to_configured_kimi_model_without_network(self):
        pcfg = self.kimi_cfg(api_key="", custom_models=[])["providers"]["kimi"]

        with (
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=None),
            mock.patch.object(ciel_runtime, "http_json", side_effect=RuntimeError("network down")),
            mock.patch.object(ciel_runtime, "write_model_list_cache") as write_cache,
        ):
            models = ciel_runtime.upstream_model_ids("kimi", pcfg)

        self.assertEqual(["kimi-for-coding"], models)
        write_cache.assert_called_once()

    def test_kimi_context_capacity_and_preset_follow_docs(self):
        pcfg = self.kimi_cfg()["providers"]["kimi"]

        self.assertEqual(262144, ciel_runtime.provider_model_context_capacity("kimi", pcfg))
        self.assertEqual("long-context-256k", ciel_runtime.recommended_preset_id("kimi", pcfg))
        self.assertIn("window 256K", ciel_runtime.context_setting_status("kimi", pcfg))

        ciel_runtime.apply_llm_preset_to_provider("kimi", pcfg, "long-context-256k", "en")

        self.assertEqual(262144, pcfg["context_window"])
        self.assertEqual(32768, pcfg["context_reserve_tokens"])
        self.assertEqual(8192, pcfg["max_output_tokens"])
        self.assertEqual(600000, pcfg["request_timeout_ms"])
        self.assertTrue(pcfg["native_compat"])

    def test_kimi_downgrades_forced_tool_choice_to_auto_by_default(self):
        pcfg = self.kimi_cfg()["providers"]["kimi"]
        body = ciel_runtime.compatibility_tool_request("kimi-for-coding")

        out = ciel_runtime.normalize_tool_choice_for_provider("kimi", pcfg, body)

        self.assertIsNot(out, body)
        self.assertEqual({"type": "auto"}, out["tool_choice"])

    def test_kimi_tool_choice_can_still_be_disabled(self):
        pcfg = self.kimi_cfg(supports_tool_choice=False)["providers"]["kimi"]
        body = ciel_runtime.compatibility_tool_request("kimi-for-coding")

        out = ciel_runtime.normalize_tool_choice_for_provider("kimi", pcfg, body)

        self.assertIn("tool_choice", body)
        self.assertNotIn("tool_choice", out)

    def test_kimi_migration_forwards_tool_choice_for_existing_configs(self):
        cfg = self.kimi_cfg(supports_tool_choice=False)
        cfg["migrations"] = {"kimi_tool_choice_auto_only_20260625": True}

        ciel_runtime.apply_config_migrations(cfg)

        pcfg = cfg["providers"]["kimi"]
        self.assertTrue(pcfg["normalize_anthropic_tool_use"])
        self.assertTrue(pcfg["supports_tool_choice"])
        self.assertTrue(cfg["migrations"]["kimi_forward_tool_choice_20260628"])

    def test_kimi_migration_uses_safe_k3_defaults_and_adds_model_variants(self):
        cfg = self.kimi_cfg(
            current_model="k3",
            context_window=1048576,
            max_model_len=1048576,
            effort_level="max",
            custom_models=["k3", "kimi-for-coding"],
        )

        ciel_runtime.apply_config_migrations(cfg)

        pcfg = cfg["providers"]["kimi"]
        self.assertEqual(262144, pcfg["context_window"])
        self.assertEqual(262144, pcfg["max_model_len"])
        self.assertEqual("high", pcfg["effort_level"])
        self.assertIn("k3[1m]", pcfg["custom_models"])
        self.assertIn("kimi-for-coding-highspeed", pcfg["custom_models"])

    def test_kimi_claude_path_sends_mcp_tools_with_auto_tool_choice(self):
        pcfg = self.kimi_cfg()["providers"]["kimi"]
        body = {
            "model": "ciel-runtime-kimi-kimi-for-coding",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "read ai-net"}]}],
            "tools": [
                {
                    "name": "mcp__ai-net-http__get_messages",
                    "description": "read messages",
                    "input_schema": {"type": "object", "properties": {"room_id": {"type": "string"}}},
                }
            ],
            "tool_choice": {"type": "any"},
        }

        normalized = ciel_runtime.normalize_request_for_provider_wire("kimi", pcfg, body)
        req = ciel_runtime.openai_compatible_chat_request("kimi", "kimi-for-coding", normalized, pcfg)

        self.assertEqual("auto", req["tool_choice"])
        self.assertEqual("mcp__ai-net-http__get_messages", req["tools"][0]["function"]["name"])

    def test_kimi_codex_responses_path_sends_mcp_tools_with_auto_tool_choice(self):
        pcfg = self.kimi_cfg()["providers"]["kimi"]
        body = {
            "model": "kimi-for-coding",
            "input": "read ai-net",
            "tools": [
                {
                    "type": "function",
                    "name": "mcp__ai-net-http__get_messages",
                    "description": "read messages",
                    "parameters": {"type": "object", "properties": {"room_id": {"type": "string"}}},
                }
            ],
            "tool_choice": "required",
            "reasoning": {"effort": "low"},
        }

        anthropic = ciel_runtime.openai_responses_to_anthropic_messages(body, "kimi-for-coding")
        normalized = ciel_runtime.normalize_request_for_provider_wire("kimi", pcfg, anthropic)
        req = ciel_runtime.openai_compatible_chat_request("kimi", "kimi-for-coding", normalized, pcfg)

        self.assertEqual("auto", req["tool_choice"])
        self.assertEqual("mcp__ai-net-http__get_messages", req["tools"][0]["function"]["name"])
        self.assertNotIn("reasoning_effort", req)

        body["model"] = "k3"
        anthropic = ciel_runtime.openai_responses_to_anthropic_messages(body, "k3")
        normalized = ciel_runtime.normalize_request_for_provider_wire("kimi", pcfg, anthropic)
        req = ciel_runtime.openai_compatible_chat_request("kimi", "k3", normalized, pcfg)
        self.assertEqual("low", req["reasoning_effort"])

    def test_kimi_codex_responses_collection_uses_openai_compatible_endpoint(self):
        class Handler:
            headers = {}
            path = "/v1/responses"

        pcfg = self.kimi_cfg(api_key="sk-kimi-test")["providers"]["kimi"]
        body = {
            "model": "kimi-for-coding",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "read ai-net"}]}],
            "tools": [],
            "stream": False,
        }
        response = {
            "id": "chatcmpl-test",
            "choices": [{"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

        with (
            mock.patch.object(ciel_runtime, "post_json_with_rate_retry", return_value=response) as post_json,
            mock.patch.object(ciel_runtime, "open_provider_request_with_key_retry") as anthropic_request,
        ):
            message = ciel_runtime.collect_provider_message_for_responses(Handler(), "kimi", pcfg, body)

        self.assertEqual("OK", message["content"][0]["text"])
        self.assertEqual("https://api.kimi.com/coding/v1/chat/completions", post_json.call_args.args[0])
        anthropic_request.assert_not_called()

    def test_kimi_preserves_thinking_while_normalizing_tool_use_stream(self):
        class FakeHandler:
            def __init__(self):
                self.wfile = io.BytesIO()

        def sse(event_name, payload):
            return f"event: {event_name}\ndata: {ciel_runtime.json.dumps(payload, ensure_ascii=False)}\n\n".encode()

        chunks = [
            sse("message_start", {"type": "message_start", "message": {"id": "msg", "type": "message", "role": "assistant", "content": [], "model": "kimi-for-coding"}}),
            sse("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
            sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "private reasoning"}}),
            sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
            sse("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_bad", "name": "Bash", "input": {}}}),
            sse("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "echo hello"}}),
            sse("content_block_stop", {"type": "content_block_stop", "index": 1}),
            sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None}, "usage": {"output_tokens": 3}}),
            sse("message_stop", {"type": "message_stop"}),
        ]
        handler = FakeHandler()
        pcfg = self.kimi_cfg()["providers"]["kimi"]

        ciel_runtime._rebatch_anthropic_sse_text(
            handler,
            io.BytesIO(b"".join(chunks)),
            "kimi-for-coding",
            source_body={"tools": [{"name": "Bash", "input_schema": {"type": "object"}}]},
            preserve_thinking=ciel_runtime.preserves_anthropic_thinking_contract("kimi", pcfg),
            normalize_tool_use=ciel_runtime.should_normalize_anthropic_stream_tool_use("kimi", pcfg),
            provider="kimi",
        )

        output = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("private reasoning", output)
        payloads = []
        for event_block in output.split("\n\n"):
            data_lines = [line[5:].strip() for line in event_block.splitlines() if line.startswith("data:")]
            if data_lines:
                payloads.append(ciel_runtime.json.loads("\n".join(data_lines)))
        tool_deltas = [
            payload
            for payload in payloads
            if payload.get("type") == "content_block_delta"
            and isinstance(payload.get("delta"), dict)
            and payload["delta"].get("type") == "input_json_delta"
        ]
        emitted_input = ciel_runtime.json.loads(tool_deltas[0]["delta"]["partial_json"])
        self.assertEqual("echo hello", emitted_input["command"])

    def test_env_vars_route_kimi_through_ciel_runtime_router(self):
        cfg = self.kimi_cfg(api_key="sk-kimi-test")
        with mock.patch.object(ciel_runtime, "upstream_model_ids", return_value=["kimi-for-coding"]):
            env = ciel_runtime.env_vars(cfg)

        self.assertEqual("kimi", env["CIEL_RUNTIME_PROVIDER"])
        self.assertEqual(ciel_runtime.ROUTER_BASE, env["ANTHROPIC_BASE_URL"])
        self.assertEqual("sk-kimi-test", env["ANTHROPIC_AUTH_TOKEN"])
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual("ciel-runtime-kimi-kimi-for-coding", env["ANTHROPIC_MODEL"])
        self.assertEqual("8192", env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"])
        self.assertEqual("262144", env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"])
        self.assertIn("thinking", env["ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES"])

    def test_k3_claude_environment_matches_selected_context_and_effort(self):
        cfg = self.kimi_cfg(api_key="sk-kimi-test", current_model="k3")
        pcfg = cfg["providers"]["kimi"]
        ciel_runtime.apply_kimi_model_profile("kimi", pcfg)
        with mock.patch.object(ciel_runtime, "upstream_model_ids", return_value=["k3"]):
            env = ciel_runtime.env_vars(cfg)

        self.assertNotIn("[1m]", env["ANTHROPIC_MODEL"])
        self.assertEqual("high", env["CLAUDE_CODE_EFFORT_LEVEL"])
        self.assertEqual("262144", env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"])
        self.assertEqual("262144", env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"])

        pcfg["current_model"] = "k3[1m]"
        ciel_runtime.apply_kimi_model_profile("kimi", pcfg)
        with mock.patch.object(ciel_runtime, "upstream_model_ids", return_value=["k3"]):
            env = ciel_runtime.env_vars(cfg)
        self.assertIn("[1m]", env["ANTHROPIC_MODEL"])
        self.assertEqual("1048576", env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"])

    def test_launch_requires_kimi_api_key(self):
        with mock.patch.object(ciel_runtime, "base_url_status_line", return_value="Base URL: Kimi.com configured"):
            errors = ciel_runtime.launch_readiness_errors(self.kimi_cfg(api_key=""))
        self.assertTrue(any("Kimi.com requires" in err for err in errors))
        self.assertTrue(ciel_runtime.launch_blockers_require_api_key(errors))


if __name__ == "__main__":
    unittest.main()
