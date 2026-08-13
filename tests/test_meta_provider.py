import copy
import io
import json
import unittest
import urllib.error
from types import SimpleNamespace
from unittest import mock

import ciel_runtime
from ciel_runtime_support import openai_responses_router
from ciel_runtime_support.provider_responses_passthrough import (
    ProviderResponsesPassthrough,
    ProviderResponsesPassthroughPorts,
)


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
        self.assertEqual("muse-spark-1.1", pcfg["current_model"])
        self.assertEqual(1_048_576, pcfg["context_window"])
        self.assertEqual(1_048_576, pcfg["max_model_len"])
        self.assertEqual(900_000, pcfg["auto_compact_window"])
        self.assertEqual("high", pcfg["effort_level"])
        self.assertTrue(pcfg["enable_tool_search"])
        self.assertNotIn("max_output_tokens", pcfg)

    def test_protocol_selection_preserves_each_native_wire_format(self):
        pcfg = self.meta_cfg()

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

    def test_claude_environment_projects_documented_muse_features(self):
        pcfg = self.meta_cfg(api_key="meta-test-key")
        cfg = {
            "current_provider": "meta",
            "providers": {"meta": pcfg},
        }

        env = ciel_runtime.env_vars(cfg)

        self.assertEqual(ciel_runtime.ROUTER_BASE, env["ANTHROPIC_BASE_URL"])
        self.assertEqual("meta-test-key", env["ANTHROPIC_AUTH_TOKEN"])
        self.assertIn("muse-spark-1.1", env["ANTHROPIC_MODEL"])
        self.assertIn("[1m]", env["ANTHROPIC_MODEL"])
        self.assertEqual(env["ANTHROPIC_MODEL"], env["CLAUDE_CODE_SUBAGENT_MODEL"])
        self.assertEqual("900000", env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"])
        self.assertEqual("high", env["CLAUDE_CODE_EFFORT_LEVEL"])
        self.assertEqual("true", env["ENABLE_TOOL_SEARCH"])


class ProviderResponsesPassthroughTests(unittest.TestCase):
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
            {"model": "alias", "input": [], "stream": True},
        )

        self.assertEqual("https://api.meta.ai/v1/responses", captured["url"])
        self.assertEqual("muse-spark-1.1", captured["body"]["model"])
        self.assertEqual(
            ["reasoning.encrypted_content"], captured["body"]["include"]
        )
        self.assertEqual(payload, handler.wfile.getvalue())
        self.assertEqual({"delivery": True}, delivery)
        record_usage.assert_called_once_with(
            "meta",
            "muse-spark-1.1",
            {
                "input_tokens": 1000,
                "output_tokens": 25,
                "cache_read_tokens": 800,
                "cache_creation_tokens": 0,
                "uncached_input_tokens": 200,
            },
        )

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


if __name__ == "__main__":
    unittest.main()
