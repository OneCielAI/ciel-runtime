import copy
import io
import json
import unittest
import urllib.error
from http.client import IncompleteRead
from types import SimpleNamespace
from unittest import mock

import ciel_runtime
from ciel_runtime_support import openai_responses_router
from ciel_runtime_support.context_compaction import (
    AutomaticContextCompactionCompleted,
)
from ciel_runtime_support.provider_responses_passthrough import (
    ProviderResponsesPassthrough,
    ProviderResponsesPassthroughPorts,
)
from ciel_runtime_support.upstream_error_policy import UpstreamStreamReadError
from ciel_runtime_support.provider_files_proxy import (
    META_FILE_UPLOAD_MAX_BYTES,
    META_FILE_UPLOAD_WIRE_MAX_BYTES,
    ProviderFilesProxy,
    ProviderFilesProxyPorts,
    is_provider_files_path,
)
from ciel_runtime_support.remote_bridge import remote_bridge_path_allowed


class MetaProviderTests(unittest.TestCase):
    def meta_cfg(self, **overrides):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["meta"])
        pcfg.update(overrides)
        return pcfg

    def test_provider_defaults_match_muse_spark_documentation(self):
        pcfg = self.meta_cfg()

        self.assertEqual("meta", ciel_runtime.PROVIDER_ALIASES["muse-spark"])
        self.assertEqual("Meta Model API", ciel_runtime.PROVIDER_LABELS["meta"])
        self.assertEqual("https://api.meta.ai/v1", pcfg["base_url"])
        self.assertEqual("muse-spark-1.3", pcfg["current_model"])
        self.assertEqual(
            [
                "muse-spark-1.3",
                "muse-spark-1.3-contributor",
                "muse-spark-1.2",
                "muse-spark-1.2-contributor",
                "muse-spark-1.1",
            ],
            pcfg["custom_models"],
        )
        self.assertEqual(1_048_576, pcfg["context_window"])
        self.assertEqual(1_048_576, pcfg["max_model_len"])
        self.assertEqual(900_000, pcfg["auto_compact_window"])
        self.assertEqual("high", pcfg["effort_level"])
        self.assertTrue(pcfg["enable_tool_search"])
        self.assertTrue(pcfg["responses_custom_tools_as_functions"])
        self.assertEqual("24h", pcfg["prompt_cache_retention"])
        self.assertNotIn("max_output_tokens", pcfg)
        self.assertIn("xhigh_effort", pcfg["claude_code_supported_capabilities"])
        self.assertEqual(
            ["minimal", "low", "medium", "high", "xhigh"],
            [
                item["effort"]
                for item in pcfg["codex_model_catalog"]["supported_reasoning_levels"]
            ],
        )

    def test_contributor_profile_preserves_model_and_warns_about_training(self):
        pcfg = self.meta_cfg(current_model="muse-spark-1.3-contributor")

        messages = ciel_runtime.apply_provider_model_profile("meta", pcfg)

        self.assertEqual("muse-spark-1.3-contributor-1m", pcfg["model_profile"])
        self.assertEqual(1_048_576, pcfg["context_window"])
        self.assertTrue(any("permits Meta to train" in message for message in messages))

    def test_new_model_aliases_resolve_to_exact_meta_api_ids(self):
        for model in ("muse-spark-1.3", "muse-spark-1.3-contributor"):
            with self.subTest(model=model):
                pcfg = self.meta_cfg(current_model=model)
                alias = ciel_runtime.alias_for("meta", model)
                self.assertEqual(
                    model,
                    ciel_runtime.resolve_requested_model(
                        "meta", pcfg, f"{alias}[1m]"
                    ),
                )

    def test_config_migration_adds_current_official_muse_spark_catalog(self):
        cfg = {
            "providers": {
                "meta": {
                    "current_model": "muse-spark-1.1",
                    "custom_models": ["muse-spark-1.1", "private-model"],
                }
            },
            "migrations": {},
        }

        ciel_runtime.apply_config_migrations(cfg)

        models = cfg["providers"]["meta"]["custom_models"]
        self.assertEqual(1, models.count("muse-spark-1.1"))
        self.assertIn("muse-spark-1.3", models)
        self.assertIn("muse-spark-1.3-contributor", models)
        self.assertIn("private-model", models)
        self.assertEqual("muse-spark-1.1", cfg["providers"]["meta"]["current_model"])
        self.assertTrue(cfg["migrations"]["meta_muse_spark_13_catalog_20260902"])

    def test_protocol_selection_preserves_each_native_wire_format(self):
        pcfg = self.meta_cfg()

        self.assertTrue(
            ciel_runtime.configured_provider_adapter(
                "meta", pcfg
            ).supports_server_web_tools(
                ciel_runtime.provider_contract_config("meta", pcfg)
            )
        )

        self.assertEqual(
            "anthropic_messages",
            ciel_runtime.select_provider_protocol(
                "meta", pcfg, "anthropic_messages", "muse-spark-1.1"
            ),
        )
        self.assertEqual(
            "openai_responses",
            ciel_runtime.select_provider_protocol(
                "meta", pcfg, "openai_responses", "muse-spark-1.1"
            ),
        )
        self.assertEqual(
            "openai_chat",
            ciel_runtime.select_provider_protocol(
                "meta", pcfg, "openai_chat", "muse-spark-1.3"
            ),
        )

    def test_muse_compatibility_probe_reserves_reasoning_output_budget(self):
        for model in ("muse-spark-1.3", "muse-spark-1.3-contributor"):
            with self.subTest(model=model):
                self.assertEqual(
                    4096,
                    ciel_runtime.compatibility_tool_request(model)["max_tokens"],
                )
                self.assertEqual(
                    4096,
                    ciel_runtime.compatibility_tool_result_request(
                        model, {"id": "tool-1", "input": {"text": "ping"}}
                    )["max_tokens"],
                )

    def test_headers_use_bearer_auth_without_anthropic_api_key_header(self):
        pcfg = self.meta_cfg(api_key="meta-test-key")

        headers = ciel_runtime.provider_responses_headers("meta", pcfg)

        self.assertEqual("Bearer meta-test-key", headers["authorization"])
        self.assertNotIn("x-api-key", headers)
        self.assertNotIn("anthropic-version", headers)

    def test_responses_normalization_preserves_encrypted_reasoning(self):
        pcfg = self.meta_cfg()
        body = {
            "model": "muse-spark-1.1",
            "input": [{"role": "user", "content": "hello"}],
            "truncation": "auto",
            "reasoning": {"effort": "ultra", "summary": "auto"},
        }

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "meta", pcfg, body
        )

        self.assertEqual("disabled", normalized["truncation"])
        self.assertEqual("xhigh", normalized["reasoning"]["effort"])
        self.assertEqual("auto", normalized["reasoning"]["summary"])
        self.assertIn("reasoning.encrypted_content", normalized["include"])
        self.assertNotIn("include", body)

        continued = ciel_runtime.apply_provider_adapter_request_policy(
            "meta",
            pcfg,
            {
                "model": "muse-spark-1.1",
                "input": [],
                "previous_response_id": "resp_123",
                "include": ["reasoning.encrypted_content"],
            },
        )
        self.assertNotIn("include", continued)

        fresh = ciel_runtime.apply_provider_adapter_request_policy(
            "meta",
            pcfg,
            {
                "model": "muse-spark-1.3-contributor",
                "input": "hello",
                "reasoning": {"effort": "minimal"},
            },
            "openai_responses",
        )
        self.assertEqual(
            {"effort": "minimal", "summary": "auto"}, fresh["reasoning"]
        )

    def test_responses_adds_configured_retention_for_codex_cache_key(self):
        body = {
            "model": "muse-spark-1.3-contributor",
            "input": "hello",
            "prompt_cache_key": "stable-thread-key",
        }

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "meta", self.meta_cfg(), body, "openai_responses"
        )

        self.assertEqual("24h", normalized["prompt_cache_retention"])
        self.assertNotIn("prompt_cache_retention", body)

        explicit = ciel_runtime.apply_provider_adapter_request_policy(
            "meta",
            self.meta_cfg(),
            {**body, "prompt_cache_retention": "in_memory"},
            "openai_responses",
        )
        self.assertEqual("in_memory", explicit["prompt_cache_retention"])

        without_key = ciel_runtime.apply_provider_adapter_request_policy(
            "meta", self.meta_cfg(), {"input": "hello"}, "openai_responses"
        )
        self.assertNotIn("prompt_cache_retention", without_key)

        disabled = ciel_runtime.apply_provider_adapter_request_policy(
            "meta",
            self.meta_cfg(prompt_cache_retention=""),
            body,
            "openai_responses",
        )
        self.assertNotIn("prompt_cache_retention", disabled)

    def test_codex_catalog_uses_meta_documented_compaction_limit(self):
        captured = {}
        cfg = {
            "current_provider": "meta",
            "providers": {"meta": self.meta_cfg()},
        }

        with mock.patch.object(
            ciel_runtime.CodexModelCatalogService,
            "write",
            autospec=True,
            side_effect=lambda _service, _codex, spec, _env: (
                captured.setdefault("spec", spec)
            ),
        ):
            ciel_runtime.write_codex_runtime_model_catalog("codex", cfg)

        spec = captured["spec"]
        self.assertEqual(1_048_576, spec.context_window)
        self.assertEqual(900_000, spec.auto_compact_token_limit)

    def test_messages_normalization_uses_supported_meta_options(self):
        pcfg = self.meta_cfg()
        body = {
            "model": "muse-spark-1.1",
            "messages": [],
            "thinking": {"type": "disabled"},
            "output_config": {"effort": "max"},
            "temperature": 0.5,
            "top_p": 0.8,
            "top_k": 10,
            "stop_sequences": ["stop"],
        }

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "meta", pcfg, body
        )

        self.assertEqual({"type": "adaptive"}, normalized["thinking"])
        self.assertEqual("xhigh", normalized["output_config"]["effort"])
        self.assertEqual(0.5, normalized["temperature"])
        for key in ("top_p", "top_k", "stop_sequences"):
            self.assertNotIn(key, normalized)

        minimal = ciel_runtime.apply_provider_adapter_request_policy(
            "meta",
            pcfg,
            {
                "model": "muse-spark-1.3-contributor",
                "messages": [],
                "output_config": {"effort": "minimal"},
            },
            "anthropic_messages",
        )
        self.assertEqual("low", minimal["output_config"]["effort"])

    def test_responses_preserves_hosted_search_and_deferred_tool_contracts(self):
        pcfg = self.meta_cfg()
        tools = [
            {"type": "web_search", "search_context_size": "high"},
            {"type": "tool_search"},
            {
                "type": "function",
                "name": "lookup_issue",
                "description": "Look up one issue",
                "parameters": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                    "additionalProperties": False,
                },
                "defer_loading": True,
            },
        ]
        body = {
            "model": "muse-spark-1.3-contributor",
            "input": "Find the current answer, then look up issue 42.",
            "tools": tools,
            "include": ["web_search_call.results"],
        }

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "meta", pcfg, body
        )

        self.assertEqual(tools[:2], normalized["tools"][:2])
        self.assertEqual("lookup_issue", normalized["tools"][2]["name"])
        self.assertTrue(normalized["tools"][2]["strict"])
        self.assertEqual(
            ["id"], normalized["tools"][2]["parameters"]["required"]
        )
        self.assertIn("web_search_call.results", normalized["include"])
        self.assertIn("reasoning.encrypted_content", normalized["include"])
        self.assertEqual(tools, body["tools"])

    def test_responses_strictifies_optional_function_tool_properties(self):
        pcfg = self.meta_cfg()
        tools = [
            {
                "type": "function",
                "name": "search_files",
                "description": "Search files",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                        "filters": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "hidden": {"type": "boolean"},
                            },
                            "required": ["path"],
                        },
                    },
                    "required": ["query"],
                },
            }
        ]
        body = {
            "model": "muse-spark-1.3-contributor",
            "input": "search",
            "tools": tools,
        }

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "meta", pcfg, body, "openai_responses"
        )

        projected = normalized["tools"][0]
        parameters = projected["parameters"]
        self.assertTrue(projected["strict"])
        self.assertEqual(["query", "limit", "filters"], parameters["required"])
        self.assertFalse(parameters["additionalProperties"])
        self.assertEqual(["integer", "null"], parameters["properties"]["limit"]["type"])
        filters = parameters["properties"]["filters"]
        self.assertEqual(["object", "null"], filters["type"])
        self.assertEqual(["path", "hidden"], filters["required"])
        self.assertEqual(["boolean", "null"], filters["properties"]["hidden"]["type"])
        self.assertEqual(tools, body["tools"])

    def test_responses_strictifies_hosted_tool_search_parameters(self):
        tool = {
            "type": "tool_search",
            "execution": "server",
            "description": "Search deferred tools",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer"},
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        }
        body = {
            "model": "muse-spark-1.3-contributor",
            "input": "find a tool",
            "tools": [tool],
        }

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "meta", self.meta_cfg(), body, "openai_responses"
        )

        projected = normalized["tools"][0]
        self.assertEqual("tool_search", projected["type"])
        self.assertNotIn("strict", projected)
        self.assertEqual(["limit", "query"], projected["parameters"]["required"])
        self.assertEqual(
            ["integer", "null"],
            projected["parameters"]["properties"]["limit"]["type"],
        )
        self.assertFalse(projected["parameters"]["additionalProperties"])
        self.assertEqual(tool, body["tools"][0])

    def test_responses_projects_custom_tool_and_replayed_items_to_functions(self):
        custom_tool = {
            "type": "custom",
            "name": "apply_patch",
            "description": "Apply a raw patch",
            "format": {
                "type": "grammar",
                "syntax": "lark",
                "definition": 'start: "*** Begin Patch"',
            },
        }
        body = {
            "model": "muse-spark-1.3-contributor",
            "tools": [custom_tool],
            "input": [
                {
                    "type": "custom_tool_call",
                    "call_id": "call_patch",
                    "name": "apply_patch",
                    "input": "*** Begin Patch",
                },
                {
                    "type": "custom_tool_call_output",
                    "call_id": "call_patch",
                    "output": "Done!",
                },
            ],
        }

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "meta", self.meta_cfg(), body, "openai_responses"
        )

        tool = normalized["tools"][0]
        self.assertEqual("function", tool["type"])
        self.assertTrue(tool["strict"])
        self.assertEqual(["input"], tool["parameters"]["required"])
        self.assertIn("Raw input must satisfy this grammar", tool["description"])
        self.assertEqual("function_call", normalized["input"][0]["type"])
        self.assertEqual(
            {"input": "*** Begin Patch"},
            json.loads(normalized["input"][0]["arguments"]),
        )
        self.assertEqual("function_call_output", normalized["input"][1]["type"])
        self.assertEqual(custom_tool, body["tools"][0])

    def test_native_meta_requests_preserve_document_image_video_and_audio_blocks(self):
        pcfg = self.meta_cfg()
        responses_content = [
            {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
            {"type": "input_file", "file_id": "file_pdf"},
            {"type": "input_video", "video_url": "https://example.test/a.mp4"},
            {"type": "input_audio", "audio_url": "data:audio/wav;base64,AAAA"},
        ]
        responses = ciel_runtime.apply_provider_adapter_request_policy(
            "meta",
            pcfg,
            {
                "model": "muse-spark-1.3-contributor",
                "input": [{"role": "user", "content": responses_content}],
            },
        )
        self.assertEqual(responses_content, responses["input"][0]["content"])

        chat_content = [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            {
                "type": "file",
                "file": {
                    "filename": "document.pdf",
                    "file_data": "data:application/pdf;base64,AAAA",
                },
            },
            {"type": "video_url", "video_url": {"url": "https://example.test/a.mp4"}},
            {
                "type": "input_audio",
                "input_audio": {"data": "AAAA", "format": "wav"},
            },
        ]
        chat = ciel_runtime.apply_provider_adapter_request_policy(
            "meta",
            pcfg,
            {
                "model": "muse-spark-1.3-contributor",
                "messages": [{"role": "user", "content": chat_content}],
            },
        )
        self.assertEqual(chat_content, chat["messages"][0]["content"])

    def test_chat_protocol_projects_named_function_choice_to_documented_auto_mode(self):
        body = {
            "model": "muse-spark-1.3-contributor",
            "messages": [{"role": "user", "content": "look it up"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "lookup_issue",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            "tool_choice": {
                "type": "function",
                "function": {"name": "lookup_issue"},
            },
        }

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "meta", self.meta_cfg(), body, "openai_chat"
        )

        self.assertEqual("auto", normalized["tool_choice"])
        self.assertEqual(body["tools"], normalized["tools"])

    def test_claude_environment_projects_documented_muse_features(self):
        pcfg = self.meta_cfg(api_key="meta-test-key")
        cfg = {
            "current_provider": "meta",
            "providers": {"meta": pcfg},
        }

        env = ciel_runtime.env_vars(cfg)

        self.assertEqual(ciel_runtime.ROUTER_BASE, env["ANTHROPIC_BASE_URL"])
        self.assertEqual("meta-test-key", env["ANTHROPIC_AUTH_TOKEN"])
        self.assertIn("muse-spark-1.3", env["ANTHROPIC_MODEL"])
        self.assertIn("[1m]", env["ANTHROPIC_MODEL"])
        self.assertEqual(env["ANTHROPIC_MODEL"], env["CLAUDE_CODE_SUBAGENT_MODEL"])
        self.assertEqual("900000", env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"])
        self.assertEqual("high", env["CLAUDE_CODE_EFFORT_LEVEL"])
        self.assertEqual("true", env["ENABLE_TOOL_SEARCH"])

    def test_meta_is_exposed_by_credential_and_provider_option_clis(self):
        self.assertIn("meta", ciel_runtime.credential_cli_controller().policy.required_providers)
        self.assertIn("meta", ciel_runtime.PROVIDER_OPTION_PROVIDERS)


class _FilesResponse(io.BytesIO):
    status = 200
    headers = {"content-type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class MetaFilesProxyTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.responses = []

        def open_request(request, **_kwargs):
            self.requests.append(request)
            if request.data is not None:
                self.uploaded = request.data.read(3) + request.data.read(1024)
            response = _FilesResponse(b'{"id":"file_test"}')
            self.responses.append(response)
            return response

        self.proxy = ProviderFilesProxy(
            ProviderFilesProxyPorts(
                current_provider=lambda _cfg: ("meta", {"current_model": "muse-spark-1.3"}),
                bridge_enabled=lambda _cfg: False,
                bridge_is_request=lambda _handler, _cfg: False,
                bridge_resolve=lambda *_args: None,
                upstream_base=lambda _provider, _cfg: "https://api.meta.ai/v1",
                join_url=ciel_runtime.join_url,
                headers=lambda _provider, _cfg, inbound, _protocol: {
                    "authorization": "Bearer configured",
                    "content-type": inbound.get("content-type", "application/json"),
                },
                urlopen=open_request,
                timeout_seconds=lambda _cfg: 30,
                copy_response_headers=lambda _handler, _headers: None,
                write_json=lambda handler, payload, status=200: (
                    setattr(handler, "json_response", (status, payload))
                ),
            )
        )

    @staticmethod
    def handler(body=b"abcdef", *, path="/v1/files", content_type="multipart/form-data; boundary=x"):
        return SimpleNamespace(
            path=path,
            headers={"content-type": content_type},
            rfile=io.BytesIO(body),
            wfile=io.BytesIO(),
            send_response=mock.Mock(),
            send_header=mock.Mock(),
            end_headers=mock.Mock(),
            close_connection=False,
        )

    def test_files_paths_are_remote_bridge_authenticated_routes(self):
        for path in (
            "/v1/files",
            "/v1/files/file_123",
            "/v1/files/file_123/content?download=1",
        ):
            with self.subTest(path=path):
                self.assertTrue(is_provider_files_path(path))
                self.assertTrue(remote_bridge_path_allowed(path))

    def test_upload_streams_exact_declared_length_and_preserves_multipart_type(self):
        handler = self.handler()

        self.assertTrue(
            self.proxy.post(
                handler,
                "/v1/files",
                6,
                handler.headers["content-type"],
                {},
            )
        )

        request = self.requests[0]
        self.assertEqual("POST", request.method)
        self.assertEqual("https://api.meta.ai/v1/files", request.full_url)
        self.assertEqual("6", request.headers["Content-length"])
        self.assertEqual(b"abcdef", self.uploaded)
        self.assertTrue(handler.close_connection)
        handler.send_response.assert_called_once_with(200)

    def test_upload_rejects_non_multipart_and_over_one_gib_without_reading(self):
        wrong_type = self.handler(content_type="application/json")
        self.assertTrue(
            self.proxy.post(wrong_type, "/v1/files", 6, "application/json", {})
        )
        self.assertEqual(415, wrong_type.json_response[0])

        too_large = self.handler()
        self.assertTrue(
            self.proxy.post(
                too_large,
                "/v1/files",
                META_FILE_UPLOAD_WIRE_MAX_BYTES + 1,
                too_large.headers["content-type"],
                {},
            )
        )
        self.assertEqual(413, too_large.json_response[0])
        self.assertGreater(META_FILE_UPLOAD_WIRE_MAX_BYTES, META_FILE_UPLOAD_MAX_BYTES)
        self.assertEqual([], self.requests)

    def test_get_and_delete_forward_file_resource_paths(self):
        get_handler = self.handler(path="/v1/files?purpose=user_data")
        delete_handler = self.handler(path="/v1/files/file_123")

        self.assertTrue(self.proxy.get(get_handler, "/v1/files", {}))
        self.assertTrue(self.proxy.delete(delete_handler, "/v1/files/file_123", {}))

        self.assertEqual(
            ["https://api.meta.ai/v1/files?purpose=user_data", "https://api.meta.ai/v1/files/file_123"],
            [request.full_url for request in self.requests],
        )
        self.assertEqual(["GET", "DELETE"], [request.method for request in self.requests])


class ProviderResponsesPassthroughTests(unittest.TestCase):
    @staticmethod
    def _passthrough_for_response(response, *, logs=None, urlopen=None):
        return ProviderResponsesPassthrough(
            ProviderResponsesPassthroughPorts(
                project_channel_context=lambda body: (body, {"delivery": True}),
                begin_channel_delivery=mock.Mock(),
                normalize_model=lambda _provider, _config, _model: "qwen3.8-max",
                normalize_request=lambda _provider, _config, body: body,
                upstream_base=lambda _provider, _config: "https://example.test/v1",
                join_url=ciel_runtime.join_url,
                headers=lambda _provider, _config, _inbound: {},
                urlopen=urlopen or (lambda *_args, **_kwargs: response),
                timeout_seconds=lambda _config: 30.0,
                copy_response_headers=lambda _handler, _headers: None,
                log=lambda level, message: (logs if logs is not None else []).append(
                    (level, message)
                ),
            )
        )

    @staticmethod
    def _passthrough_handler():
        return SimpleNamespace(
            headers={},
            wfile=io.BytesIO(),
            send_response=mock.Mock(),
            send_header=mock.Mock(),
            end_headers=mock.Mock(),
        )

    def test_passthrough_keeps_typed_sse_bytes_and_uses_provider_endpoint(self):
        payload = (
            b"event: response.reasoning_text.delta\n"
            b'data: {\"type\":\"response.reasoning_text.delta\"}\n\n'
            b"event: response.completed\n"
            b'data: {\"type\":\"response.completed\",\"response\":{\"usage\":{\"input_tokens\":1000,\"output_tokens\":25,\"input_tokens_details\":{\"cached_tokens\":800}}}}\n\n'
        )
        captured = {}
        record_usage = mock.Mock()

        class Response:
            status = 200
            headers = {"content-type": "text/event-stream"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                if captured.get("read"):
                    return b""
                captured["read"] = True
                return payload

        def urlopen(request, **kwargs):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data)
            captured["headers"] = dict(request.header_items())
            captured["kwargs"] = kwargs
            return Response()

        handler = SimpleNamespace(
            headers={},
            wfile=io.BytesIO(),
            send_response=mock.Mock(),
            send_header=mock.Mock(),
            end_headers=mock.Mock(),
        )
        typed_input = [
            {
                "type": "compaction",
                "encrypted_content": "native-checkpoint-ciphertext",
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "continue"}],
            },
        ]
        source_body = {
            "model": "alias",
            "input": typed_input,
            "stream": True,
            "context_management": [
                {"type": "compaction", "compact_threshold": 900_000}
            ],
            "prompt_cache_key": "codex-native-window",
        }
        service = ProviderResponsesPassthrough(
            ProviderResponsesPassthroughPorts(
                project_channel_context=lambda body: (body, {"delivery": True}),
                begin_channel_delivery=mock.Mock(),
                normalize_model=lambda _provider, _config, _model: "muse-spark-1.1",
                normalize_request=lambda _provider, _config, body: {
                    **body,
                    "include": ["reasoning.encrypted_content"],
                },
                upstream_base=lambda _provider, _config: "https://api.meta.ai/v1",
                join_url=ciel_runtime.join_url,
                headers=lambda _provider, _config, _inbound: {
                    "authorization": "Bearer test"
                },
                urlopen=urlopen,
                timeout_seconds=lambda _config: 30.0,
                copy_response_headers=lambda _handler, _headers: None,
                record_usage=record_usage,
            )
        )

        delivery = service.forward(
            handler,
            "meta",
            {},
            source_body,
        )

        self.assertEqual("https://api.meta.ai/v1/responses", captured["url"])
        self.assertEqual("muse-spark-1.1", captured["body"]["model"])
        self.assertEqual(typed_input, captured["body"]["input"])
        self.assertEqual(
            source_body["context_management"], captured["body"]["context_management"]
        )
        self.assertEqual(
            source_body["prompt_cache_key"], captured["body"]["prompt_cache_key"]
        )
        self.assertEqual(
            ["reasoning.encrypted_content"], captured["body"]["include"]
        )
        self.assertEqual(payload, handler.wfile.getvalue())
        self.assertEqual({"delivery": True}, delivery)
        record_usage.assert_called_once()
        usage_args = record_usage.call_args.args
        self.assertEqual(("meta", "muse-spark-1.1"), usage_args[:2])
        self.assertEqual(1000, usage_args[2]["input_tokens"])
        self.assertEqual(25, usage_args[2]["output_tokens"])
        self.assertEqual(800, usage_args[2]["cache_read_tokens"])
        self.assertEqual(200, usage_args[2]["uncached_input_tokens"])
        self.assertEqual(80.0, usage_args[2]["cache_hit_percent"])
        self.assertEqual(16, len(usage_args[2]["prompt_cache_key_fingerprint"]))

    def test_meta_custom_function_envelope_is_restored_in_stream(self):
        function_item = {
            "id": "fc_patch",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_patch",
            "name": "apply_patch",
            "arguments": '{"input":"*** Begin Patch\\n*** End Patch"}',
        }
        exec_item = {
            "id": "fc_exec",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_exec",
            "name": "exec_command",
            "arguments": '{"cmd":"Get-Content probe.txt","yield_time_ms":10000.0}',
        }
        events = [
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {**function_item, "status": "in_progress", "arguments": ""},
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_patch",
                "output_index": 0,
                "delta": '{"input":"*** Begin',
            },
            {
                "type": "response.function_call_arguments.done",
                "item_id": "fc_patch",
                "output_index": 0,
                "arguments": function_item["arguments"],
            },
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": function_item,
            },
            {
                "type": "response.output_item.added",
                "output_index": 1,
                "item": {**exec_item, "status": "in_progress", "arguments": ""},
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_exec",
                "output_index": 1,
                "delta": exec_item["arguments"],
            },
            {
                "type": "response.function_call_arguments.done",
                "item_id": "fc_exec",
                "output_index": 1,
                "arguments": exec_item["arguments"],
            },
            {
                "type": "response.output_item.done",
                "output_index": 1,
                "item": exec_item,
            },
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_patch",
                    "status": "completed",
                    "output": [function_item, exec_item],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
        ]
        payload = b"".join(
            (
                f"event: {event['type']}\n"
                f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
            ).encode()
            for event in events
        )
        captured = {}

        class Response:
            status = 200
            headers = {
                "content-type": "text/event-stream",
                "content-length": str(len(payload)),
            }

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                if captured.get("read"):
                    return b""
                captured["read"] = True
                return payload

        copied_headers = {}
        service = ProviderResponsesPassthrough(
            ProviderResponsesPassthroughPorts(
                project_channel_context=lambda body: (body, {}),
                begin_channel_delivery=mock.Mock(),
                normalize_model=lambda _provider, _config, model: model,
                normalize_request=lambda _provider, config, body: (
                    ciel_runtime.apply_provider_adapter_request_policy(
                        "meta", config, body, "openai_responses"
                    )
                ),
                upstream_base=lambda _provider, _config: "https://api.meta.ai/v1",
                join_url=ciel_runtime.join_url,
                headers=lambda _provider, _config, _inbound: {},
                urlopen=lambda *_args, **_kwargs: Response(),
                timeout_seconds=lambda _config: 30.0,
                copy_response_headers=lambda _handler, headers: copied_headers.update(headers),
            )
        )
        handler = self._passthrough_handler()
        config = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["meta"])
        source = {
            "model": "muse-spark-1.3-contributor",
            "stream": True,
            "input": "Apply the patch",
            "tools": [
                {
                    "type": "custom",
                    "name": "apply_patch",
                    "description": "Apply raw patch",
                    "format": {
                        "type": "grammar",
                        "syntax": "lark",
                        "definition": "start: /.+/",
                    },
                },
                {
                    "type": "function",
                    "name": "exec_command",
                    "description": "Run a command",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "cmd": {"type": "string"},
                            "yield_time_ms": {"type": "number"},
                        },
                        "required": ["cmd"],
                    },
                },
            ],
        }

        service.forward(handler, "meta", config, source)

        output = handler.wfile.getvalue().decode()
        self.assertIn("response.custom_tool_call_input.delta", output)
        self.assertIn("response.custom_tool_call_input.done", output)
        self.assertNotIn("10000.0", output)
        decoded = [
            json.loads(line[6:])
            for line in output.splitlines()
            if line.startswith("data: ")
        ]
        added = next(
            item
            for item in decoded
            if item["type"] == "response.output_item.added"
        )
        completed = next(
            item for item in decoded if item["type"] == "response.completed"
        )
        self.assertEqual("custom_tool_call", added["item"]["type"])
        self.assertEqual(
            "*** Begin Patch\n*** End Patch",
            completed["response"]["output"][0]["input"],
        )
        self.assertEqual(
            10000,
            json.loads(completed["response"]["output"][1]["arguments"])[
                "yield_time_ms"
            ],
        )
        self.assertNotIn("content-length", copied_headers)

    def test_provider_wire_limit_compacts_before_upstream_request(self):
        captured = {}
        compact_calls = []

        class Response:
            status = 200
            headers = {"content-type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return b""

        def compact(body, budget, **kwargs):
            compact_calls.append((budget, kwargs))
            return {**body, "input": body["input"][-1:]}

        def urlopen(request, **_kwargs):
            captured["data"] = request.data
            captured["body"] = json.loads(request.data)
            return Response()

        service = ProviderResponsesPassthrough(
            ProviderResponsesPassthroughPorts(
                project_channel_context=lambda body: (body, {}),
                begin_channel_delivery=mock.Mock(),
                normalize_model=lambda _provider, _config, _model: "qwen3.8-max",
                normalize_request=lambda _provider, _config, body: body,
                upstream_base=lambda _provider, _config: "https://example.test/v1",
                join_url=ciel_runtime.join_url,
                headers=lambda _provider, _config, _inbound: {},
                urlopen=urlopen,
                timeout_seconds=lambda _config: 30.0,
                copy_response_headers=lambda _handler, _headers: None,
                request_max_bytes=lambda _provider, _config: 1200,
                estimate_tokens=lambda body: max(1, len(json.dumps(body)) // 4),
                compact_responses=compact,
            )
        )
        source = {
            "model": "alias",
            "stream": False,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "x" * 900}],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "continue"}],
                },
            ],
        }

        service.forward(
            self._passthrough_handler(),
            "alitoken",
            {"responses_cache_checkpoint_items": 24},
            source,
        )

        self.assertTrue(compact_calls)
        self.assertEqual(
            24, compact_calls[0][1]["stable_prefix_checkpoint_items"]
        )
        self.assertEqual(source["input"][-1:], captured["body"]["input"])
        self.assertLessEqual(len(captured["data"]), 1080)
        self.assertNotIn(b'": "', captured["data"])

    def test_stateless_alibaba_request_defers_session_cache_header(self):
        captured = {}

        class Response:
            status = 200
            headers = {"content-type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return b""

        def urlopen(request, **_kwargs):
            captured["headers"] = {
                name.casefold(): value for name, value in request.header_items()
            }
            return Response()

        service = ProviderResponsesPassthrough(
            ProviderResponsesPassthroughPorts(
                project_channel_context=lambda body: (body, {}),
                begin_channel_delivery=mock.Mock(),
                normalize_model=lambda _provider, _config, model: model,
                normalize_request=lambda _provider, _config, body: body,
                upstream_base=lambda _provider, _config: "https://example.test/v1",
                join_url=ciel_runtime.join_url,
                headers=lambda *_args: {
                    "x-dashscope-session-cache": "enable",
                    "authorization": "Bearer test",
                },
                urlopen=urlopen,
                timeout_seconds=lambda _config: 30.0,
                copy_response_headers=lambda _handler, _headers: None,
            )
        )

        service.forward(
            self._passthrough_handler(),
            "alitoken",
            {"responses_session_cache_requires_previous_response_id": True},
            {"model": "qwen3.8-max", "input": [], "stream": False},
        )

        self.assertNotIn("x-dashscope-session-cache", captured["headers"])
        self.assertIn("authorization", captured["headers"])

    def test_linked_alibaba_request_keeps_session_cache_header(self):
        captured = {}

        class Response:
            status = 200
            headers = {"content-type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return b""

        def urlopen(request, **_kwargs):
            captured["headers"] = {
                name.casefold(): value for name, value in request.header_items()
            }
            return Response()

        service = ProviderResponsesPassthrough(
            ProviderResponsesPassthroughPorts(
                project_channel_context=lambda body: (body, {}),
                begin_channel_delivery=mock.Mock(),
                normalize_model=lambda _provider, _config, model: model,
                normalize_request=lambda _provider, _config, body: body,
                upstream_base=lambda _provider, _config: "https://example.test/v1",
                join_url=ciel_runtime.join_url,
                headers=lambda *_args: {
                    "x-dashscope-session-cache": "enable"
                },
                urlopen=urlopen,
                timeout_seconds=lambda _config: 30.0,
                copy_response_headers=lambda _handler, _headers: None,
            )
        )

        service.forward(
            self._passthrough_handler(),
            "alitoken",
            {"responses_session_cache_requires_previous_response_id": True},
            {
                "model": "qwen3.8-max",
                "previous_response_id": "resp_123",
                "input": [],
                "stream": False,
            },
        )

        self.assertEqual(
            "enable", captured["headers"]["x-dashscope-session-cache"]
        )

    def test_unshrinkable_provider_body_fails_before_upstream(self):
        urlopen = mock.Mock()
        service = ProviderResponsesPassthrough(
            ProviderResponsesPassthroughPorts(
                project_channel_context=lambda body: (body, {}),
                begin_channel_delivery=mock.Mock(),
                normalize_model=lambda _provider, _config, _model: "qwen3.8-max",
                normalize_request=lambda _provider, _config, body: body,
                upstream_base=lambda _provider, _config: "https://example.test/v1",
                join_url=ciel_runtime.join_url,
                headers=lambda _provider, _config, _inbound: {},
                urlopen=urlopen,
                timeout_seconds=lambda _config: 30.0,
                copy_response_headers=lambda _handler, _headers: None,
                request_max_bytes=lambda _provider, _config: 512,
                estimate_tokens=lambda _body: 1000,
                compact_responses=lambda body, _budget, **_kwargs: body,
            )
        )

        with self.assertRaises(urllib.error.HTTPError) as caught:
            service.forward(
                self._passthrough_handler(),
                "alitoken",
                {},
                {"model": "alias", "stream": False, "input": ["x" * 1000]},
            )

        self.assertEqual(413, caught.exception.code)
        self.assertIn(b"bounded context compaction", caught.exception.read())
        urlopen.assert_not_called()

    def test_real_responses_compactor_preserves_native_fields_and_latest_tail(self):
        captured = {}

        class Response:
            status = 200
            headers = {"content-type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return b""

        def urlopen(request, **_kwargs):
            captured["data"] = request.data
            captured["body"] = json.loads(request.data)
            return Response()

        service = ProviderResponsesPassthrough(
            ProviderResponsesPassthroughPorts(
                project_channel_context=lambda body: (body, {}),
                begin_channel_delivery=mock.Mock(),
                normalize_model=lambda _provider, _config, _model: "qwen3.8-max",
                normalize_request=lambda _provider, _config, body: body,
                upstream_base=lambda _provider, _config: "https://example.test/v1",
                join_url=ciel_runtime.join_url,
                headers=lambda _provider, _config, _inbound: {},
                urlopen=urlopen,
                timeout_seconds=lambda _config: 30.0,
                copy_response_headers=lambda _handler, _headers: None,
                request_max_bytes=lambda _provider, _config: 120_000,
                estimate_tokens=ciel_runtime.estimate_tokens,
                compact_responses=ciel_runtime.compact_responses_with_remote_instruction,
            )
        )
        source = {
            "model": "alias",
            "stream": False,
            "prompt_cache_key": "native-cache-key",
            "context_management": [
                {"type": "compaction", "compact_threshold": 900_000}
            ],
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"history-{index:03d} " + ("x" * 3000),
                        }
                    ],
                }
                for index in range(70)
            ],
        }

        with mock.patch.object(
            ciel_runtime, "_latest_remote_instruction", return_value=""
        ):
            service.forward(
                self._passthrough_handler(), "alitoken", {}, source
            )

        projected = captured["body"]
        self.assertLessEqual(len(captured["data"]), 108_000)
        self.assertLess(len(projected["input"]), len(source["input"]))
        self.assertEqual(source["input"][-1], projected["input"][-1])
        self.assertIn("deterministic chunk summaries", json.dumps(projected["input"]))
        self.assertEqual("native-cache-key", projected["prompt_cache_key"])
        self.assertEqual(source["context_management"], projected["context_management"])

    def test_incomplete_read_partial_that_completes_terminal_event_is_preserved(self):
        payload = (
            b"event: response.completed\n"
            b'data: {"type":"response.completed","response":{"id":"resp_done"}}\n\n'
        )

        class Response:
            status = 200
            headers = {"content-type": "text/event-stream"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                raise IncompleteRead(payload)

        handler = self._passthrough_handler()
        logs = []
        delivery = self._passthrough_for_response(Response(), logs=logs).forward(
            handler,
            "alitoken",
            {},
            {"model": "alias", "input": [], "stream": True},
        )

        self.assertEqual(payload, handler.wfile.getvalue())
        self.assertEqual({"delivery": True}, delivery)
        self.assertTrue(any("length_mismatch_after_terminal" in item[1] for item in logs))

    def test_incomplete_native_stream_raises_typed_error_after_forwarding_partial(self):
        prefix = b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_cut"}}\n\n'
        broken = b'event: response.completed\ndata: {"type":"response.completed","response":{'

        class Response:
            status = 200
            headers = {"content-type": "text/event-stream"}

            def __init__(self):
                self.calls = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                self.calls += 1
                if self.calls == 1:
                    return prefix
                raise IncompleteRead(broken)

        handler = self._passthrough_handler()
        with self.assertRaises(UpstreamStreamReadError) as caught:
            self._passthrough_for_response(Response()).forward(
                handler,
                "alitoken",
                {},
                {"model": "alias", "input": [], "stream": True},
            )

        error = caught.exception
        self.assertTrue(error.downstream_started)
        self.assertEqual("resp_cut", error.response_id)
        self.assertEqual(len(prefix) + len(broken), int(str(error).split("after ")[1].split(" bytes")[0]))
        self.assertEqual(prefix + broken, handler.wfile.getvalue())

    def test_clean_eof_without_terminal_event_is_reported_as_truncated(self):
        payload = b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_eof"}}\n\n'

        class Response:
            status = 200
            headers = {"content-type": "text/event-stream"}

            def __init__(self):
                self.sent = False

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                if self.sent:
                    return b""
                self.sent = True
                return payload

        handler = self._passthrough_handler()
        with self.assertRaises(UpstreamStreamReadError) as caught:
            self._passthrough_for_response(Response()).forward(
                handler,
                "alitoken",
                {},
                {"model": "alias", "input": [], "stream": True},
            )

        self.assertTrue(caught.exception.downstream_started)
        self.assertEqual("resp_eof", caught.exception.response_id)
        self.assertIn("without a terminal event", str(caught.exception))

    def test_alitoken_buffers_and_retries_one_truncated_native_stream(self):
        prefix = b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_cut"}}\n\n'
        complete = (
            b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_ok"}}\n\n'
            b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_ok"}}\n\n'
        )

        class Response:
            status = 200
            headers = {"content-type": "text/event-stream"}

            def __init__(self, payload):
                self.payload = payload
                self.sent = False

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                if self.sent:
                    return b""
                self.sent = True
                return self.payload

        responses = [Response(prefix), Response(complete)]
        logs = []
        handler = self._passthrough_handler()
        urlopen = mock.Mock(side_effect=responses)
        service = self._passthrough_for_response(None, logs=logs, urlopen=urlopen)

        service.forward(
            handler,
            "alitoken",
            {"responses_stream_truncation_retries": 1},
            {"model": "alias", "input": [], "stream": True},
        )

        self.assertEqual(complete, handler.wfile.getvalue())
        self.assertEqual(2, urlopen.call_count)
        self.assertTrue(any("provider_responses_stream_retry" in item[1] for item in logs))

    def test_alitoken_truncation_retry_is_bounded_and_commits_no_partial_bytes(self):
        payload = b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_cut"}}\n\n'

        class Response:
            status = 200
            headers = {"content-type": "text/event-stream"}

            def __init__(self):
                self.sent = False

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                if self.sent:
                    return b""
                self.sent = True
                return payload

        handler = self._passthrough_handler()
        urlopen = mock.Mock(side_effect=[Response(), Response()])
        service = self._passthrough_for_response(None, urlopen=urlopen)

        with self.assertRaises(UpstreamStreamReadError) as caught:
            service.forward(
                handler,
                "alitoken",
                {"responses_stream_truncation_retries": 1, "gateway_retries": 10},
                {"model": "alias", "input": [], "stream": True},
            )

        self.assertEqual(2, caught.exception.attempts)
        self.assertFalse(caught.exception.downstream_started)
        self.assertEqual(b"", handler.wfile.getvalue())
        self.assertEqual(2, urlopen.call_count)

    def test_router_selects_native_provider_responses_before_conversion_route(self):
        event_bus = SimpleNamespace(publish=mock.Mock())
        forward = mock.Mock(return_value={"delivery": True})
        collect = mock.Mock()
        commit = mock.Mock()
        services = openai_responses_router.OpenAIResponsesServices(
            core=openai_responses_router.OpenAIResponsesCore(
                event_bus=event_bus,
                request_id=lambda: "request-id",
                input_as_list=lambda value: list(value),
                is_client_disconnect=lambda _exc: False,
                log=mock.Mock(),
            ),
            conversion=openai_responses_router.OpenAIResponsesConversion(
                to_anthropic=lambda _body, _alias: {"messages": []},
                current_alias=lambda _cfg: "alias",
                update_tool_schema=mock.Mock(),
                normalize_thinking=mock.Mock(),
                filter_blocked_tools=mock.Mock(),
                normalize_tool_choice=mock.Mock(),
                write_context_usage=mock.Mock(),
                strip_advisor_tools=mock.Mock(),
                inject_channel_context=mock.Mock(),
                inject_tool_result_context=mock.Mock(),
            ),
            routing=openai_responses_router.OpenAIResponsesRouting(
                maybe_import_session=lambda *_args, **_kwargs: False,
                codex_routed_enabled=lambda _provider, _config: False,
                forward_codex=mock.Mock(),
                select_protocol=lambda *_args: "openai_responses",
                forward_provider_responses=forward,
                dump_request=mock.Mock(),
                normalize_provider_wire=mock.Mock(),
                collect_message=collect,
                apply_codex_compat_instructions=lambda _cfg, _provider, _pcfg, body: body,
                recover_preamble_only_turn=lambda _h, _p, _c, _b, message: message,
            ),
            delivery=openai_responses_router.OpenAIResponsesDelivery(
                begin=mock.Mock(),
                mark_success=mock.Mock(),
                mark_failed=mock.Mock(),
                commit=commit,
            ),
            output=openai_responses_router.OpenAIResponsesOutput(
                write_response=mock.Mock(),
                write_error=mock.Mock(),
                upstream_error_message=mock.Mock(),
                codex_auth_error_message=mock.Mock(),
                event_preview=mock.Mock(),
            ),
        )
        handler = SimpleNamespace(path="/v1/responses")
        body = {"model": "muse-spark-1.1", "input": [], "stream": True}

        openai_responses_router.handle_openai_responses_request(
            handler,
            {},
            "meta",
            {},
            body,
            services,
        )

        forward.assert_called_once_with(handler, "meta", {}, body)
        collect.assert_not_called()
        commit.assert_called_once_with({"delivery": True}, handler)

    def test_router_preserves_upstream_413_as_request_too_large(self):
        error_body = b'{"error":{"type":"request_too_large","message":"provider limit"}}'
        upstream_error = urllib.error.HTTPError(
            "https://api.meta.ai/v1/responses",
            413,
            "Payload Too Large",
            {},
            io.BytesIO(error_body),
        )
        write_error = mock.Mock()
        services = openai_responses_router.OpenAIResponsesServices(
            core=openai_responses_router.OpenAIResponsesCore(
                event_bus=SimpleNamespace(publish=mock.Mock()),
                request_id=lambda: "request-id",
                input_as_list=lambda value: list(value),
                is_client_disconnect=lambda _exc: False,
                log=mock.Mock(),
            ),
            conversion=mock.Mock(),
            routing=openai_responses_router.OpenAIResponsesRouting(
                maybe_import_session=lambda *_args, **_kwargs: False,
                codex_routed_enabled=lambda *_args: False,
                forward_codex=mock.Mock(),
                select_protocol=lambda *_args: "openai_responses",
                forward_provider_responses=mock.Mock(side_effect=upstream_error),
                dump_request=mock.Mock(),
                normalize_provider_wire=mock.Mock(),
                collect_message=mock.Mock(),
                apply_codex_compat_instructions=lambda _cfg, _provider, _pcfg, body: body,
                recover_preamble_only_turn=mock.Mock(),
            ),
            delivery=openai_responses_router.OpenAIResponsesDelivery(
                begin=mock.Mock(),
                mark_success=mock.Mock(),
                mark_failed=mock.Mock(),
                commit=mock.Mock(),
            ),
            output=openai_responses_router.OpenAIResponsesOutput(
                write_response=mock.Mock(),
                write_error=write_error,
                upstream_error_message=lambda _error, _raw: "request_too_large: provider limit",
                codex_auth_error_message=mock.Mock(),
                event_preview=mock.Mock(),
            ),
        )
        handler = SimpleNamespace(path="/v1/responses")

        openai_responses_router.handle_openai_responses_request(
            handler,
            {},
            "meta",
            {},
            {"model": "muse-spark-1.1", "input": [], "stream": False},
            services,
        )

        write_error.assert_called_once_with(
            handler,
            "request_too_large: provider limit",
            stream=False,
            status=413,
            error_type="request_too_large",
        )

    def test_router_preserves_upstream_401_as_authentication_error(self):
        error_body = (
            b'{"error":{"type":"invalid_authentication_error",'
            b'"message":"The API Key appears to be invalid or may have expired"}}'
        )
        upstream_error = urllib.error.HTTPError(
            "https://api.kimi.com/coding/v1/chat/completions",
            401,
            "Unauthorized",
            {},
            io.BytesIO(error_body),
        )
        write_error = mock.Mock()
        services = openai_responses_router.OpenAIResponsesServices(
            core=openai_responses_router.OpenAIResponsesCore(
                event_bus=SimpleNamespace(publish=mock.Mock()),
                request_id=lambda: "request-id",
                input_as_list=lambda value: list(value),
                is_client_disconnect=lambda _exc: False,
                log=mock.Mock(),
            ),
            conversion=mock.Mock(),
            routing=openai_responses_router.OpenAIResponsesRouting(
                maybe_import_session=lambda *_args, **_kwargs: False,
                codex_routed_enabled=lambda *_args: False,
                forward_codex=mock.Mock(),
                select_protocol=lambda *_args: "openai_responses",
                forward_provider_responses=mock.Mock(side_effect=upstream_error),
                dump_request=mock.Mock(),
                normalize_provider_wire=mock.Mock(),
                collect_message=mock.Mock(),
                apply_codex_compat_instructions=lambda _cfg, _provider, _pcfg, body: body,
                recover_preamble_only_turn=mock.Mock(),
            ),
            delivery=openai_responses_router.OpenAIResponsesDelivery(
                begin=mock.Mock(),
                mark_success=mock.Mock(),
                mark_failed=mock.Mock(),
                commit=mock.Mock(),
            ),
            output=openai_responses_router.OpenAIResponsesOutput(
                write_response=mock.Mock(),
                write_error=write_error,
                upstream_error_message=lambda _error, _raw: (
                    "invalid_authentication_error: The API Key appears to be "
                    "invalid or may have expired"
                ),
                codex_auth_error_message=lambda message: message,
                event_preview=mock.Mock(),
            ),
        )
        handler = SimpleNamespace(path="/v1/responses")

        openai_responses_router.handle_openai_responses_request(
            handler,
            {},
            "kimi",
            {},
            {"model": "k3", "input": [], "stream": False},
            services,
        )

        write_error.assert_called_once_with(
            handler,
            "invalid_authentication_error: The API Key appears to be invalid or may have expired",
            stream=False,
            status=401,
            error_type="authentication_error",
        )

    def test_router_returns_local_compaction_as_success_without_upstream_retry(self):
        anthropic_body = {"model": "k3", "messages": []}
        summary = "[ciel-runtime local context checkpoint]\nsummary"
        write_response = mock.Mock()
        collect = mock.Mock()
        mark_success = mock.Mock()
        commit = mock.Mock()
        services = openai_responses_router.OpenAIResponsesServices(
            core=openai_responses_router.OpenAIResponsesCore(
                event_bus=SimpleNamespace(publish=mock.Mock()),
                request_id=lambda: "request-id",
                input_as_list=lambda value: list(value),
                is_client_disconnect=lambda _exc: False,
                log=mock.Mock(),
            ),
            conversion=openai_responses_router.OpenAIResponsesConversion(
                to_anthropic=lambda _body, _alias: anthropic_body,
                current_alias=lambda _cfg: "alias",
                update_tool_schema=mock.Mock(),
                normalize_thinking=lambda _provider, _pcfg, body: body,
                filter_blocked_tools=lambda _provider, _pcfg, body: body,
                normalize_tool_choice=lambda _provider, _pcfg, body: body,
                write_context_usage=mock.Mock(),
                strip_advisor_tools=lambda _provider, body: body,
                inject_channel_context=lambda body: body,
                inject_tool_result_context=lambda body: body,
            ),
            routing=openai_responses_router.OpenAIResponsesRouting(
                maybe_import_session=lambda *_args, **_kwargs: False,
                codex_routed_enabled=lambda *_args: False,
                forward_codex=mock.Mock(),
                select_protocol=lambda *_args: "openai_chat",
                forward_provider_responses=mock.Mock(),
                dump_request=mock.Mock(),
                normalize_provider_wire=mock.Mock(
                    side_effect=AutomaticContextCompactionCompleted(summary)
                ),
                collect_message=collect,
                apply_codex_compat_instructions=lambda _cfg, _provider, _pcfg, body: body,
                recover_preamble_only_turn=mock.Mock(),
            ),
            delivery=openai_responses_router.OpenAIResponsesDelivery(
                begin=mock.Mock(),
                mark_success=mark_success,
                mark_failed=mock.Mock(),
                commit=commit,
            ),
            output=openai_responses_router.OpenAIResponsesOutput(
                write_response=write_response,
                write_error=mock.Mock(),
                upstream_error_message=mock.Mock(),
                codex_auth_error_message=mock.Mock(),
                event_preview=lambda _body, _cfg: {},
            ),
        )
        handler = SimpleNamespace(path="/v1/responses")
        source = {"model": "k3", "input": [], "stream": True}

        openai_responses_router.handle_openai_responses_request(
            handler, {}, "kimi", {}, source, services
        )

        collect.assert_not_called()
        write_response.assert_called_once()
        message = write_response.call_args.args[1]
        self.assertEqual(summary, message["content"][0]["text"])
        self.assertEqual("end_turn", message["stop_reason"])
        self.assertEqual(
            {"source_body": source, "stream": True}, write_response.call_args.kwargs
        )
        mark_success.assert_called_once_with(handler, "responses_local_compaction")
        commit.assert_called_once_with(anthropic_body, handler)

    def test_router_catches_local_compaction_from_collection_boundary(self):
        anthropic_body = {"model": "k3", "messages": []}
        summary = "[ciel-runtime local context checkpoint]\nsummary"
        write_response = mock.Mock()
        write_error = mock.Mock()
        mark_success = mock.Mock()
        mark_failed = mock.Mock()
        commit = mock.Mock()
        services = openai_responses_router.OpenAIResponsesServices(
            core=openai_responses_router.OpenAIResponsesCore(
                event_bus=SimpleNamespace(publish=mock.Mock()),
                request_id=lambda: "request-id",
                input_as_list=lambda value: list(value),
                is_client_disconnect=lambda _exc: False,
                log=mock.Mock(),
            ),
            conversion=openai_responses_router.OpenAIResponsesConversion(
                to_anthropic=lambda _body, _alias: anthropic_body,
                current_alias=lambda _cfg: "alias",
                update_tool_schema=mock.Mock(),
                normalize_thinking=lambda _provider, _pcfg, body: body,
                filter_blocked_tools=lambda _provider, _pcfg, body: body,
                normalize_tool_choice=lambda _provider, _pcfg, body: body,
                write_context_usage=mock.Mock(),
                strip_advisor_tools=lambda _provider, body: body,
                inject_channel_context=lambda body: body,
                inject_tool_result_context=lambda body: body,
            ),
            routing=openai_responses_router.OpenAIResponsesRouting(
                maybe_import_session=lambda *_args, **_kwargs: False,
                codex_routed_enabled=lambda *_args: False,
                forward_codex=mock.Mock(),
                select_protocol=lambda *_args: "openai_chat",
                forward_provider_responses=mock.Mock(),
                dump_request=mock.Mock(),
                normalize_provider_wire=lambda _provider, _pcfg, body: body,
                collect_message=mock.Mock(
                    side_effect=AutomaticContextCompactionCompleted(summary)
                ),
                apply_codex_compat_instructions=lambda _cfg, _provider, _pcfg, body: body,
                recover_preamble_only_turn=mock.Mock(),
            ),
            delivery=openai_responses_router.OpenAIResponsesDelivery(
                begin=mock.Mock(),
                mark_success=mark_success,
                mark_failed=mark_failed,
                commit=commit,
            ),
            output=openai_responses_router.OpenAIResponsesOutput(
                write_response=write_response,
                write_error=write_error,
                upstream_error_message=mock.Mock(),
                codex_auth_error_message=mock.Mock(),
                event_preview=lambda _body, _cfg: {},
            ),
        )
        handler = SimpleNamespace(path="/v1/responses")
        source = {"model": "k3", "input": [], "stream": True}

        openai_responses_router.handle_openai_responses_request(
            handler, {}, "kimi", {}, source, services
        )

        write_response.assert_called_once()
        self.assertEqual(summary, write_response.call_args.args[1]["content"][0]["text"])
        write_error.assert_not_called()
        mark_failed.assert_not_called()
        mark_success.assert_called_once_with(handler, "responses_local_compaction")
        commit.assert_called_once_with(anthropic_body, handler)


if __name__ == "__main__":
    unittest.main()
