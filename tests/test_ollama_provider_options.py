import json
import unittest
from unittest import mock

import ciel_runtime
from ciel_runtime_support.providers.ollama_runtime import (
    OllamaRuntimeApi,
    OllamaRuntimeService,
)


class _FakeOllamaStreamResponse:
    def __init__(self, lines):
        self.lines = list(lines)
        self.closed = False

    def readline(self):
        if not self.lines:
            return b""
        return self.lines.pop(0)

    def close(self):
        self.closed = True


class _CaptureWrite:
    def __init__(self):
        self.data = bytearray()

    def write(self, data):
        self.data.extend(data)

    def flush(self):
        pass


class _FakeSSEHandler:
    headers = {}
    connection = None

    def __init__(self):
        self.wfile = _CaptureWrite()

    def send_response(self, _status):
        pass

    def send_header(self, _name, _value):
        pass

    def end_headers(self):
        pass


class OllamaProviderOptionTests(unittest.TestCase):
    def test_runtime_api_explicitly_delegates_public_queries(self):
        service = mock.create_autospec(OllamaRuntimeService, instance=True)
        service.api_base.side_effect = ["local-base", "cloud-base"]
        service.show_parameters.return_value = {"num_ctx": "8192"}
        service.fetch_model_specs.return_value = {"max_model_len": 8192}
        service.model_id_matches.return_value = True
        service.runtime_info.return_value = {"loaded_context_len": 65536}
        service.output_cap.return_value = 4096
        api = OllamaRuntimeApi(lambda: service)
        config = {"current_model": "model"}

        self.assertEqual("local-base", api.api_base(config))
        self.assertEqual("cloud-base", api.provider_api_base("ollama-cloud", config))
        self.assertEqual({"num_ctx": "8192"}, api.show_parameters({}))
        self.assertEqual(
            {"max_model_len": 8192},
            api.fetch_model_specs("ollama", config, "model"),
        )
        self.assertTrue(api.model_id_matches("model", "model:latest"))
        self.assertEqual(
            {"loaded_context_len": 65536}, api.runtime_info(config)
        )
        self.assertEqual(4096, api.output_cap(65536))

    def test_generic_context_window_maps_to_ollama_num_ctx(self):
        pcfg = {"num_ctx": "auto", "num_ctx_min": 32768, "num_ctx_max": 131072}

        ciel_runtime.apply_provider_option("ollama-cloud", pcfg, "context_window=1048576")

        self.assertEqual(1048576, pcfg["context_window"])
        self.assertEqual("auto", pcfg["num_ctx"])
        self.assertEqual(1048576, pcfg["num_ctx_max"])
        self.assertEqual(65536, pcfg["num_ctx_min"])

    def test_generic_max_output_tokens_maps_to_ollama_num_predict(self):
        pcfg = {"ollama_options": {}}

        ciel_runtime.apply_provider_option("ollama-cloud", pcfg, "max_output_tokens=8192")

        self.assertEqual(8192, pcfg["max_output_tokens"])
        self.assertEqual(8192, pcfg["ollama_options"]["num_predict"])

    def test_generic_sampling_options_stay_in_ollama_options(self):
        pcfg = {"ollama_options": {}}

        ciel_runtime.apply_provider_option("ollama-cloud", pcfg, "temperature=0.7")
        ciel_runtime.apply_provider_option("ollama-cloud", pcfg, "top_p=0.9")

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

        status = ciel_runtime.provider_options_status("ollama-cloud", pcfg)

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

        self.assertEqual(262144, ciel_runtime.ollama_num_ctx_for_payload(pcfg, payload))
        self.assertEqual(262144, ciel_runtime.ollama_context_limit_for_budget(pcfg))
        self.assertIn("provider 262,144", ciel_runtime.ollama_num_ctx_status(pcfg))

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

        self.assertEqual(65536, ciel_runtime.ollama_num_ctx_for_payload(pcfg, payload))

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

        self.assertEqual(32768, ciel_runtime.ollama_num_ctx_for_payload(pcfg, payload))

    def test_ollama_provider_context_beats_model_name_hint(self):
        pcfg = {
            "current_model": "qwen3.6:35b-a3b-mtp-2gpu-ctx256k",
            "num_ctx": "auto",
            "num_ctx_min": 65536,
            "num_ctx_max": 262144,
            "model_context_model": "qwen3.6:35b-a3b-mtp-2gpu-ctx256k",
            "model_context_max": 131072,
        }

        self.assertEqual(131072, ciel_runtime.provider_model_context_capacity("ollama", pcfg))
        self.assertEqual(131072, ciel_runtime.ollama_context_limit_for_budget(pcfg))

    def test_ollama_show_parameters_parse_parameters_and_modelfile(self):
        data = {
            "parameters": "num_ctx 262144\nnum_predict 4096\n",
            "modelfile": "FROM base\nPARAMETER num_gpu 999\nPARAMETER temperature 0.7\n",
        }

        params = ciel_runtime.ollama_show_parameters(data)

        self.assertEqual("262144", params["num_ctx"])
        self.assertEqual("4096", params["num_predict"])
        self.assertEqual("999", params["num_gpu"])
        self.assertEqual("0.7", params["temperature"])

    def test_model_context_field_reads_dotted_ollama_model_info(self):
        self.assertEqual(262144, ciel_runtime.model_context_field({"qwen3.context_length": 262144}))

    def test_sync_ollama_context_prefers_api_show_specs(self):
        pcfg = {
            "current_model": "custom-model:latest",
            "num_ctx": "auto",
            "num_ctx_min": 32768,
            "num_ctx_max": 65536,
        }

        with (
            mock.patch.object(ciel_runtime, "fetch_ollama_api_model_specs", return_value={"max_model_len": 262144}),
            mock.patch.object(ciel_runtime, "load_ollama_model_catalog") as load_catalog,
        ):
            messages = ciel_runtime.sync_ollama_library_context_limit("ollama", pcfg, "custom-model:latest")

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
            mock.patch.object(ciel_runtime, "fetch_ollama_api_model_specs", return_value={"max_model_len": 1000000}),
            mock.patch.object(ciel_runtime, "load_ollama_model_catalog") as load_catalog,
        ):
            ciel_runtime.sync_ollama_library_context_limit("ollama-cloud", pcfg, "glm-5.2")

        load_catalog.assert_not_called()
        self.assertEqual(1000000, pcfg["model_context_max"])
        self.assertEqual(524288, pcfg["num_ctx_max"])
        self.assertEqual(524288, ciel_runtime.ollama_context_limit_for_budget(pcfg))
        self.assertEqual(524288, ciel_runtime.ollama_num_ctx_for_payload(pcfg, {"messages": []}))
        self.assertIn("model max 1,000,000", ciel_runtime.ollama_num_ctx_status(pcfg))

    def test_large_ollama_context_uses_dynamic_reserve_when_unset(self):
        pcfg = {
            "current_model": "glm-5.2",
            "num_ctx": "auto",
            "num_ctx_min": 262144,
            "num_ctx_max": 524288,
            "max_output_tokens": 8192,
        }

        self.assertEqual(16384, ciel_runtime.context_guard_reserve_tokens(pcfg, 524288))

        messages = [{"role": "user", "content": "x" * 3000} for _ in range(4000)]
        tools = []
        context_limit = ciel_runtime.ollama_context_limit_for_budget(pcfg)
        budget = context_limit - pcfg["max_output_tokens"] - ciel_runtime.context_guard_reserve_tokens(pcfg, context_limit)
        compacted = ciel_runtime.compact_ollama_messages_for_budget(messages, tools, budget, provider="ollama-cloud", model="glm-5.2")

        self.assertLessEqual(ciel_runtime.estimate_tokens({"messages": compacted, "tools": tools}), budget)
        self.assertEqual(499712, budget)

    def test_ollama_compact_hard_caps_oversized_first_user_message(self):
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "initial paste " + ("x" * 180000)},
            {"role": "assistant", "content": "old answer " + ("y" * 4000)},
            {"role": "user", "content": "current task must survive"},
        ]
        budget = 8192

        with mock.patch.object(ciel_runtime, "write_context_compact_activity") as write_compact:
            compacted = ciel_runtime.compact_ollama_messages_for_budget(
                messages,
                [],
                budget,
                provider="ollama",
                model="qwen3.6:35b-a3b-mtp-2gpu-ctx256k",
            )

        self.assertLessEqual(ciel_runtime.estimate_tokens({"messages": compacted, "tools": []}), budget)
        self.assertIn("current task must survive", compacted[-1]["content"])
        self.assertIn("Latest retained message compacted", compacted[-1]["content"])
        self.assertLess(len(compacted), len(messages))
        write_compact.assert_called_once()

    def test_glm_52_uses_ollama_thinking_when_configured_on(self):
        pcfg = {
            "current_model": "glm-5.2",
            "think": True,
            "num_ctx": "auto",
            "num_ctx_min": 32768,
            "num_ctx_max": 1048576,
            "ollama_options": {"num_predict": 1024},
        }
        body = {"messages": [{"role": "user", "content": "hello"}], "tools": []}

        with mock.patch.object(ciel_runtime, "write_context_usage"):
            request = ciel_runtime.ollama_chat_request("glm-5.2", body, pcfg, stream=False, provider="ollama-cloud")

        self.assertTrue(request["think"])
        self.assertEqual("True", ciel_runtime.ollama_think_status("glm-5.2", pcfg))
        self.assertEqual("reasoning", ciel_runtime.infer_preset_id_from_options("ollama-cloud", pcfg))

    def test_glm_52_ollama_preset_matches_provider_api_context(self):
        for model in ("glm-5.2", "glm-5.2:cloud"):
            preset = ciel_runtime.MODEL_PRESETS[model]
            self.assertTrue(preset["thinking"])
            self.assertEqual(1000000, preset["num_ctx_max"])

    def test_glm_52_ollama_cloud_migration_enables_thinking_and_provider_context(self):
        cfg = {
            "providers": {
                "ollama-cloud": {
                    "current_model": "glm-5.2:cloud",
                    "think": False,
                    "num_ctx_max": 131072,
                }
            },
            "migrations": {},
        }

        ciel_runtime.apply_config_migrations(cfg)

        pcfg = cfg["providers"]["ollama-cloud"]
        self.assertTrue(pcfg["think"])
        self.assertEqual(1000000, pcfg["num_ctx_max"])
        self.assertEqual(1000000, pcfg["model_context_max"])

    def test_glm_52_migration_removes_legacy_auto_512k_cap(self):
        cfg = {
            "providers": {
                "ollama-cloud": {
                    "current_model": "glm-5.2",
                    "llm_preset": "long-context-512k",
                    "num_ctx": "auto",
                    "num_ctx_min": 262144,
                    "num_ctx_max": 524288,
                    "model_context_max": 1000000,
                    "model_context_model": "glm-5.2",
                    "think": False,
                }
            },
            "migrations": {"ollama_cloud_glm52_thinking_context_20260711": True},
        }

        ciel_runtime.apply_config_migrations(cfg)

        pcfg = cfg["providers"]["ollama-cloud"]
        self.assertNotIn("llm_preset", pcfg)
        self.assertEqual("auto", pcfg["num_ctx"])
        self.assertEqual(1000000, pcfg["num_ctx_max"])
        self.assertEqual(1000000, pcfg["model_context_max"])
        self.assertTrue(pcfg["think"])

    def test_non_glm_52_ollama_think_still_respects_config(self):
        pcfg = {
            "current_model": "qwen3-coder",
            "think": True,
            "num_ctx": "auto",
            "num_ctx_min": 32768,
            "num_ctx_max": 131072,
            "ollama_options": {"num_predict": 1024},
        }
        body = {"messages": [{"role": "user", "content": "hello"}], "tools": []}

        with mock.patch.object(ciel_runtime, "write_context_usage"):
            request = ciel_runtime.ollama_chat_request("qwen3-coder", body, pcfg, stream=False, provider="ollama")

        self.assertTrue(request["think"])
        self.assertEqual("True", ciel_runtime.ollama_think_status("qwen3-coder", pcfg))

    def test_ollama_context_error_retry_config_compacts_to_reported_n_ctx(self):
        raw = '{"error":"request (58940 tokens) exceeds the available context size (32768 tokens), try increasing the context length","n_ctx":32768}'
        pcfg = {
            "current_model": "qwen3.6:35b-a3b-mtp-2gpu-ctx256k",
            "num_ctx": "auto",
            "num_ctx_min": 32768,
            "num_ctx_max": 262144,
            "model_context_model": "qwen3.6:35b-a3b-mtp-2gpu-ctx256k",
            "model_context_max": 262144,
            "max_output_tokens": 32768,
            "ollama_options": {"num_predict": 32768},
        }
        body = {
            "model": "qwen3.6:35b-a3b-mtp-2gpu-ctx256k",
            "max_tokens": 32768,
            "messages": [
                *[
                    {"role": "user" if idx % 2 == 0 else "assistant", "content": f"history {idx} " + ("x" * 4000)}
                    for idx in range(80)
                ],
                {"role": "user", "content": "current task must survive"},
            ],
        }

        self.assertEqual(32768, ciel_runtime.ollama_context_error_limit(raw))
        retry_pcfg = ciel_runtime.ollama_context_retry_config(pcfg, 32768)
        with mock.patch.object(ciel_runtime, "write_context_usage"):
            normal_req = ciel_runtime.ollama_chat_request(body["model"], body, pcfg, stream=True)
            retry_req = ciel_runtime.ollama_chat_request(body["model"], body, retry_pcfg, stream=True)

        self.assertEqual(262144, normal_req["options"]["num_ctx"])
        self.assertGreater(ciel_runtime.estimate_tokens(normal_req), 32768)
        self.assertEqual(32768, retry_req["options"]["num_ctx"])
        self.assertLessEqual(retry_req["options"]["num_predict"], 2048)
        self.assertLessEqual(ciel_runtime.estimate_tokens(retry_req), 32768)
        self.assertIn("current task must survive", retry_req["messages"][-1]["content"])

    def test_compact_request_uses_segmented_llm_compaction(self):
        calls = []
        original = ciel_runtime.context_compact_request_summary

        def fake_summary(provider, model, pcfg, prompt, *, wire, budget_tokens):
            calls.append((provider, model, wire, prompt, budget_tokens))
            return f"summary {len(calls)}"

        ciel_runtime.context_compact_request_summary = fake_summary
        try:
            pcfg = {
                "base_url": "https://ollama.com",
                "context_compact_chunk_tokens": 512,
                "context_compact_summary_tokens": 512,
            }
            messages = [
                {"role": "user", "content": "history " + ("x" * 10000)}
                for _ in range(4)
            ]
            messages.append(
                {
                    "role": "user",
                    "content": "<command-name>/compact</command-name>\nCreate a detailed summary of the conversation.",
                }
            )

            compacted = ciel_runtime.compact_ollama_messages_for_budget(
                messages,
                [],
                8192,
                provider="ollama-cloud",
                model="deepseek-v4-flash",
                pcfg=pcfg,
                full_compact_request=True,
                wire="ollama",
            )

            self.assertGreaterEqual(len(calls), 2)
            self.assertEqual(1, len(compacted))
            self.assertIn("[ciel-runtime segmented compact]", compacted[0]["content"])
            self.assertIn("summary 1", compacted[0]["content"])
            self.assertIn("Claude Code compact instruction", compacted[0]["content"])
        finally:
            ciel_runtime.context_compact_request_summary = original

    def test_sync_ollama_context_caps_explicit_preset_above_provider_max(self):
        pcfg = {
            "current_model": "small-model",
            "llm_preset": "million-context-1m",
            "num_ctx": "auto",
            "num_ctx_min": 262144,
            "num_ctx_max": 1048576,
        }

        with mock.patch.object(ciel_runtime, "fetch_ollama_api_model_specs", return_value={"max_model_len": 262144}):
            ciel_runtime.sync_ollama_library_context_limit("ollama-cloud", pcfg, "small-model")

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

        with mock.patch.object(ciel_runtime, "read_model_info_cache", return_value={"glm-5.2": {"max_model_len": 1000000}}):
            ciel_runtime.apply_current_model_specs_to_provider("ollama-cloud", pcfg)

        self.assertEqual(1000000, pcfg["model_context_max"])
        self.assertEqual(524288, pcfg["num_ctx_max"])
        self.assertEqual(524288, ciel_runtime.context_limit_for_status("ollama-cloud", pcfg))

    def test_unset_generic_ollama_aliases_clears_effective_options(self):
        pcfg = {
            "context_window": 1048576,
            "max_output_tokens": 8192,
            "num_ctx": "auto",
            "num_ctx_max": 1048576,
            "ollama_options": {"num_predict": 8192},
        }

        ciel_runtime.apply_provider_option("ollama-cloud", pcfg, "unset:context_window")
        ciel_runtime.apply_provider_option("ollama-cloud", pcfg, "unset:max_output_tokens")

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
            ciel_runtime,
            "ollama_runtime_info",
            return_value={"runtime_model": "gemma4:12b", "loaded_context_len": 65536},
        ):
            messages = ciel_runtime.apply_ollama_runtime_output_guard("ollama", pcfg)

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
            ciel_runtime,
            "ollama_runtime_info",
            return_value={"runtime_model": "large-model", "loaded_context_len": 131072},
        ):
            messages = ciel_runtime.apply_ollama_runtime_output_guard("ollama", pcfg)

        self.assertEqual(8192, pcfg["ollama_options"]["num_predict"])
        self.assertEqual(8192, pcfg["max_output_tokens"])
        self.assertEqual([], messages)

    def test_ollama_chat_to_anthropic_suppresses_visible_thinking_markup(self):
        data = {
            "message": {
                "content": "Kevin이 assignment를 생성했습니다.</think>Kevin이 assignment를 생성했습니다. 읽겠습니다.",
                "tool_calls": [
                    {
                        "function": {
                            "name": "Read",
                            "arguments": {"file_path": "/tmp/task.txt"},
                        }
                    }
                ],
            },
            "done": True,
        }
        body = {"tools": [{"name": "Read", "input_schema": {"type": "object", "properties": {}}}], "messages": []}

        out = ciel_runtime.ollama_chat_to_anthropic(data, "glm-5.2", source_body=body)

        text = ciel_runtime.anthropic_content_to_text(out["content"])
        self.assertNotIn("</think>", text)
        self.assertEqual("Kevin이 assignment를 생성했습니다.Kevin이 assignment를 생성했습니다. 읽겠습니다.", text)
        self.assertEqual("tool_use", out["stop_reason"])

    def test_ollama_stream_suppresses_split_visible_thinking_markup(self):
        chunks = [
            {"message": {"content": "<thi"}, "done": False},
            {"message": {"content": "nk>private reasoning</thi"}, "done": False},
            {"message": {"content": "nk>visible answer"}, "done": False},
            {"message": {"content": ""}, "done": True, "done_reason": "stop", "eval_count": 4},
        ]
        resp = _FakeOllamaStreamResponse(
            [(json.dumps(chunk, ensure_ascii=False) + "\n").encode("utf-8") for chunk in chunks]
        )
        handler = _FakeSSEHandler()

        with mock.patch.object(ciel_runtime, "write_router_activity"):
            ciel_runtime._ollama_stream_to_anthropic_sse(handler, resp, "glm-5.2", idle_timeout=30.0)

        output = handler.wfile.data.decode("utf-8")
        self.assertIn("visible answer", output)
        self.assertNotIn("private reasoning", output)
        self.assertNotIn("<think", output)
        self.assertNotIn("</think", output)
        self.assertTrue(resp.closed)

    def test_ollama_stream_never_overlaps_text_and_tool_content_blocks(self):
        chunks = [
            {"message": {"content": "Read 1 file"}, "done": False},
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "Read",
                                "arguments": {"file_path": "/tmp/one.txt"},
                            }
                        },
                        {
                            "function": {
                                "name": "Read",
                                "arguments": {"file_path": "/tmp/two.txt"},
                            }
                        },
                    ],
                },
                "done": False,
            },
            {"message": {"content": "Continuing after tools"}, "done": False},
            {
                "message": {"content": ""},
                "done": True,
                "done_reason": "stop",
                "eval_count": 12,
            },
        ]
        resp = _FakeOllamaStreamResponse(
            [(json.dumps(chunk) + "\n").encode("utf-8") for chunk in chunks]
        )
        handler = _FakeSSEHandler()

        with mock.patch.object(ciel_runtime, "write_router_activity"):
            ciel_runtime._ollama_stream_to_anthropic_sse(
                handler,
                resp,
                "glm-5.2",
                provider="ollama-cloud",
                source_body={
                    "messages": [],
                    "tools": [
                        {
                            "name": "Read",
                            "input_schema": {"type": "object", "properties": {}},
                        }
                    ],
                },
                idle_timeout=30.0,
            )

        frames = []
        for raw_frame in handler.wfile.data.decode("utf-8").split("\n\n"):
            lines = raw_frame.splitlines()
            event_line = next((line for line in lines if line.startswith("event: ")), "")
            data_line = next((line for line in lines if line.startswith("data: ")), "")
            if event_line and data_line:
                frames.append((event_line.removeprefix("event: "), json.loads(data_line.removeprefix("data: "))))

        open_index = None
        started_blocks = []
        for event_name, payload in frames:
            if event_name == "content_block_start":
                self.assertIsNone(open_index, f"content block {open_index} was still open")
                open_index = payload["index"]
                started_blocks.append(
                    (payload["index"], payload["content_block"]["type"])
                )
            elif event_name == "content_block_delta":
                self.assertEqual(open_index, payload["index"])
            elif event_name == "content_block_stop":
                self.assertEqual(open_index, payload["index"])
                open_index = None
            elif event_name in {"message_delta", "message_stop"}:
                self.assertIsNone(open_index)

        self.assertIsNone(open_index)
        self.assertEqual(
            [(0, "text"), (1, "tool_use"), (2, "tool_use"), (3, "text")],
            started_blocks,
        )

    def test_visible_thinking_filter_drops_trailing_partial_tag(self):
        filter_state = ciel_runtime.VisibleThinkingMarkupFilter()

        self.assertEqual("visible ", filter_state.feed("visible <thi"))
        self.assertEqual("", filter_state.finish())


if __name__ == "__main__":
    unittest.main()
