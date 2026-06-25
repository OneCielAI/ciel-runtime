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
        self.assertEqual(262144, pcfg["context_window"])
        self.assertEqual(32768, pcfg["max_output_tokens"])
        self.assertEqual(32768, pcfg["context_reserve_tokens"])
        self.assertEqual(600000, pcfg["request_timeout_ms"])
        self.assertTrue(pcfg["native_compat"])
        self.assertTrue(pcfg["preserve_anthropic_thinking"])
        self.assertTrue(pcfg["normalize_anthropic_tool_use"])
        self.assertFalse(pcfg["supports_tool_choice"])
        self.assertIn("thinking", pcfg["claude_code_supported_capabilities"])

    def test_kimi_aliases_normalize_to_documented_model_id(self):
        for raw in (
            "kimi-code/kimi-for-coding",
            "moonshot/kimi-for-coding",
            "kimi-k2.7-code",
            "k2.7-code",
        ):
            self.assertEqual("kimi-for-coding", ciel_runtime.normalize_model_id("kimi", raw))

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

    def test_kimi_strips_forced_tool_choice_by_default(self):
        pcfg = self.kimi_cfg()["providers"]["kimi"]
        body = ciel_runtime.compatibility_tool_request("kimi-for-coding")

        out = ciel_runtime.normalize_tool_choice_for_provider("kimi", pcfg, body)

        self.assertIn("tool_choice", body)
        self.assertNotIn("tool_choice", out)

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
        self.assertEqual("ciel-runtime-kimi-kimi-for-coding[1m]", env["ANTHROPIC_MODEL"])
        self.assertEqual("8192", env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"])
        self.assertEqual("262144", env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"])
        self.assertIn("thinking", env["ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES"])

    def test_launch_requires_kimi_api_key(self):
        with mock.patch.object(ciel_runtime, "base_url_status_line", return_value="Base URL: Kimi.com configured"):
            errors = ciel_runtime.launch_readiness_errors(self.kimi_cfg(api_key=""))
        self.assertTrue(any("Kimi.com requires" in err for err in errors))
        self.assertTrue(ciel_runtime.launch_blockers_require_api_key(errors))


if __name__ == "__main__":
    unittest.main()
