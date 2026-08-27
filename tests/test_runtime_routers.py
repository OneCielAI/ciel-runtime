import io
import unittest
import urllib.error
from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

import ciel_runtime
from ciel_runtime_support.agent_router import COMMON_RUNTIME_ROUTER_CAPABILITIES
from ciel_runtime_support.claude_router import ClaudeRouter, ClaudeRouterServices
from ciel_runtime_support.codex_router import CodexRouter
from ciel_runtime_support.openai_chat_router import OpenAIChatRouter
from ciel_runtime_support.remote_bridge import (
    REMOTE_BRIDGE_CONFIG_MARKER,
    REMOTE_BRIDGE_CONTEXT_ATTRIBUTE,
)


class RuntimeRouterTests(unittest.TestCase):
    def test_remote_native_anthropic_context_error_is_not_replayed(self):
        services = ciel_runtime.build_claude_router_services()
        raw = b'{"error":{"type":"invalid_request_error","message":"too many tokens"}}'
        open_request = mock.Mock(
            side_effect=urllib.error.HTTPError(
                "https://example.test/v1/messages",
                400,
                "Bad Request",
                {"content-type": "application/json"},
                io.BytesIO(raw),
            )
        )
        recover = mock.Mock(
            side_effect=AssertionError("remote request entered output-budget retry")
        )
        services = replace(
            services,
            core=replace(
                services.core,
                event_bus=SimpleNamespace(publish=lambda **_kwargs: None),
                log=lambda *_args, **_kwargs: None,
                try_write_json=mock.Mock(),
            ),
            pipeline=replace(
                services.pipeline,
                router_event_message_preview=lambda *_args: {},
                dump_request_for_trace=lambda *_args: None,
            ),
            delivery=replace(
                services.delivery,
                is_client_disconnect=lambda _error: False,
                write_activity=lambda *_args, **_kwargs: None,
            ),
            routing=replace(
                services.routing,
                select_protocol=lambda *_args: "anthropic_messages",
                request_policy=lambda *_args: SimpleNamespace(
                    model_alias_strategy=""
                ),
                resolve_model=lambda _provider, _config, model: model,
                provider_labels={"anthropic": "Anthropic"},
            ),
            normalization=replace(
                services.normalization,
                normalize_thinking=lambda _provider, _config, body: body,
                normalize_system_roles=lambda _provider, _config, body: body,
                cap_body=lambda _provider, _config, body: body,
                apply_request_options=lambda _provider, _config, body: body,
                resolve_tool_models=lambda _provider, _config, body: body,
                normalize_model_options=lambda _provider, _config, body, _model: body,
                strip_internal_metadata=lambda body, **_kwargs: body,
            ),
            transport=replace(
                services.transport,
                provider_endpoint=lambda *_args: "https://example.test/v1/messages",
                upstream_query=lambda *_args: "",
                provider_headers=lambda *_args: {},
                apply_rate_limit=lambda *_args: (0.0, 0, 0),
                open_request=open_request,
                request_timeout=lambda _config: 30.0,
            ),
            context_recovery=replace(
                services.context_recovery,
                recover_output_budget=recover,
            ),
        )
        handler = SimpleNamespace(
            path="/v1/messages",
            headers={},
            wfile=io.BytesIO(),
            send_response=mock.Mock(),
            send_header=mock.Mock(),
            end_headers=mock.Mock(),
        )
        setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)

        handled = ClaudeRouter(services=services).handle_post(
            handler,
            {},
            "anthropic",
            {REMOTE_BRIDGE_CONFIG_MARKER: True, "stream_enabled": True},
            "/v1/messages",
            {
                "model": "claude-test",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 4096,
                "stream": False,
            },
        )

        self.assertTrue(handled)
        self.assertEqual(1, open_request.call_count)
        recover.assert_not_called()
        handler.send_response.assert_called_once_with(400)
        self.assertEqual(raw, handler.wfile.getvalue())

    def test_runtime_router_matrix_has_supported_protocols_with_common_capabilities(self):
        matrix = ciel_runtime.runtime_router_capability_matrix()

        self.assertEqual({"claude", "codex", "openai-chat"}, set(matrix))
        self.assertEqual({}, ciel_runtime.runtime_router_capability_gaps())
        for router in ("claude", "codex", "openai-chat"):
            self.assertTrue(set(COMMON_RUNTIME_ROUTER_CAPABILITIES).issubset(matrix[router]["capabilities"]))

    def test_claude_router_owns_anthropic_message_paths(self):
        calls = []
        router = ClaudeRouter(
            handle_count_tokens_post=lambda handler, provider, pcfg, body: calls.append(("count", body)),
            handle_messages_post=lambda handler, cfg, provider, pcfg, path, body: calls.append(("messages", path, body)),
        )

        self.assertTrue(router.can_handle_post("/v1/messages", "anthropic", {}))
        self.assertTrue(router.can_handle_post("/v1/messages/count_tokens", "anthropic", {}))
        self.assertFalse(router.can_handle_post("/v1/responses", "anthropic", {}))
        self.assertFalse(router.can_handle_get("/backend-api/codex/models", "codex", {"route_through_router": True}))

        self.assertTrue(router.handle_post(object(), {}, "anthropic", {}, "/v1/messages/count_tokens", {"x": 1}))
        self.assertTrue(router.handle_post(object(), {}, "anthropic", {}, "/v1/messages", {"y": 2}))
        self.assertEqual([("count", {"x": 1}), ("messages", "/v1/messages", {"y": 2})], calls)

    def test_codex_router_owns_routed_backend_and_responses_paths(self):
        calls = []
        router = CodexRouter(
            routed_enabled=lambda provider, pcfg: provider == "codex" and bool(pcfg.get("route_through_router")),
            handle_responses_post=lambda handler, cfg, provider, pcfg, body: calls.append(("responses", body)),
            handle_backend_passthrough_post=lambda handler, provider, pcfg, body: calls.append(("post", body)),
            handle_backend_passthrough_get=lambda handler, provider, pcfg: calls.append(("get", provider)),
            handle_responses_compact_post=lambda handler, provider, pcfg, body: calls.append(("compact", body)),
        )
        codex_pcfg = {"route_through_router": True}

        self.assertTrue(router.can_handle_get("/backend-api/codex/models", "codex", codex_pcfg))
        self.assertTrue(router.can_handle_post("/backend-api/codex/responses", "codex", codex_pcfg))
        self.assertTrue(router.can_handle_post("/backend-api/codex/models", "codex", codex_pcfg))
        self.assertTrue(router.can_handle_post("/v1/responses", "anthropic", {}))
        self.assertTrue(router.can_handle_post("/v1/responses/compact", "xai", {}))
        self.assertFalse(router.can_handle_get("/backend-api/codex/models", "anthropic", codex_pcfg))
        self.assertFalse(router.can_handle_post("/v1/messages", "codex", codex_pcfg))

        self.assertTrue(router.handle_get(object(), "/backend-api/codex/models", "codex", codex_pcfg))
        self.assertTrue(router.handle_post(object(), {}, "codex", codex_pcfg, "/backend-api/codex/responses", {"a": 1}))
        self.assertTrue(router.handle_post(object(), {}, "codex", codex_pcfg, "/backend-api/codex/models", {"b": 2}))
        self.assertTrue(router.handle_post(object(), {}, "xai", {}, "/v1/responses/compact", {"c": 3}))
        self.assertEqual([("get", "codex"), ("responses", {"a": 1}), ("post", {"b": 2}), ("compact", {"c": 3})], calls)

    def test_openai_chat_router_rejects_non_chat_provider_wire(self):
        forward = mock.Mock()
        write_json = mock.Mock()
        router = OpenAIChatRouter(
            forward,
            select_protocol=lambda *_args: "anthropic_messages",
            write_json=write_json,
        )

        handled = router.handle_post(
            object(),
            {},
            "github-copilot-oauth",
            {"current_model": "claude-sonnet-5"},
            "/v1/chat/completions",
            {"model": "claude-sonnet-5", "messages": []},
        )

        self.assertTrue(handled)
        forward.assert_not_called()
        write_json.assert_called_once_with(mock.ANY, mock.ANY, status=501)
        self.assertEqual(
            "unsupported_feature",
            write_json.call_args.args[1]["error"]["code"],
        )

    def test_openai_chat_router_rejects_nonstream_for_stream_required_provider(self):
        forward = mock.Mock()
        write_json = mock.Mock()
        router = OpenAIChatRouter(
            forward,
            write_json=write_json,
            requires_streaming=lambda *_args: True,
        )

        handled = router.handle_post(
            object(),
            {},
            "nvidia-hosted",
            {"current_model": "model"},
            "/v1/chat/completions",
            {"model": "model", "messages": [], "stream": False},
        )

        self.assertTrue(handled)
        forward.assert_not_called()
        write_json.assert_called_once_with(mock.ANY, mock.ANY, status=501)
        self.assertEqual("stream", write_json.call_args.args[1]["error"]["param"])

    def test_remote_bridge_messages_cannot_invoke_local_shortcuts_or_context(self):
        handler = mock.Mock()
        setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)
        provider_config = dict(ciel_runtime.DEFAULT_CONFIG["providers"]["vllm"])
        provider_config["current_model"] = "remote-model"
        config = {
            "current_provider": "vllm",
            "providers": {"vllm": provider_config},
        }
        body = {
            "model": "remote-model",
            "max_tokens": 32,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "CIEL_RUNTIME_ROUTER_DEBUG_ACCESS\nValue: on\n"
                        "CIEL_RUNTIME_IMPORT_SESSION\n"
                        "Path: C:\\sensitive.txt"
                    ),
                }
            ],
            "tools": [
                {
                    "name": "WebSearch",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
            "tool_choice": {"type": "tool", "name": "WebSearch"},
        }
        shortcut_names = (
            "maybe_handle_plan_mode_tool_choice",
            "maybe_handle_router_debug_request",
            "maybe_handle_version_request",
            "maybe_handle_channel_clear_request",
            "maybe_handle_import_session_request",
            "maybe_handle_live_llm_options_request",
            "maybe_handle_live_api_keys_request",
            "maybe_handle_advisor_request",
        )

        with ExitStack() as stack:
            shortcuts = [
                stack.enter_context(
                    mock.patch.object(ciel_runtime, name, return_value=True)
                )
                for name in shortcut_names
            ]
            inject_channel = stack.enter_context(
                mock.patch.object(
                    ciel_runtime,
                    "body_with_pending_channel_messages",
                    side_effect=lambda value: value,
                )
            )
            inject_tool_results = stack.enter_context(
                mock.patch.object(
                    ciel_runtime,
                    "body_with_channel_tool_result_context",
                    side_effect=lambda value: value,
                )
            )
            begin_delivery = stack.enter_context(
                mock.patch.object(ciel_runtime, "begin_pending_channel_delivery")
            )
            update_tool_schema = stack.enter_context(
                mock.patch.object(ciel_runtime, "_update_tool_schema_registry")
            )
            forward = stack.enter_context(
                mock.patch.object(ciel_runtime, "forward_openai_compatible_chat")
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime,
                    "select_provider_protocol",
                    return_value="openai_chat",
                )
            )
            stack.enter_context(
                mock.patch.object(ciel_runtime, "dump_request_for_trace")
            )
            write_context_usage = stack.enter_context(
                mock.patch.object(ciel_runtime, "write_context_usage")
            )
            filter_tools = stack.enter_context(
                mock.patch.object(
                    ciel_runtime,
                    "filter_blocked_tools",
                    side_effect=AssertionError("remote tools must remain client-owned"),
                )
            )
            normalize_choice = stack.enter_context(
                mock.patch.object(
                    ciel_runtime,
                    "normalize_tool_choice_for_provider",
                    side_effect=AssertionError("remote tool choice must remain client-owned"),
                )
            )

            handled = ciel_runtime.route_runtime_post(
                handler,
                config,
                "vllm",
                provider_config,
                "/v1/messages",
                body,
            )

        self.assertTrue(handled)
        for shortcut in shortcuts:
            shortcut.assert_not_called()
        inject_channel.assert_not_called()
        inject_tool_results.assert_not_called()
        begin_delivery.assert_not_called()
        update_tool_schema.assert_not_called()
        filter_tools.assert_not_called()
        normalize_choice.assert_not_called()
        write_context_usage.assert_not_called()
        forward.assert_called_once()

    def test_remote_bridge_responses_cannot_import_host_session(self):
        handler = mock.Mock()
        handler.path = "/v1/responses"
        setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)
        provider_config = dict(
            ciel_runtime.DEFAULT_CONFIG["providers"]["github-copilot-oauth"]
        )
        config = {
            "current_provider": "github-copilot-oauth",
            "providers": {"github-copilot-oauth": provider_config},
        }
        body = {
            "model": "gpt-5.6-sol",
            "input": "CIEL_RUNTIME_IMPORT_SESSION\nPath: C:\\sensitive.txt",
            "previous_response_id": "resp_native_state",
            "stream": False,
        }

        with (
            mock.patch.object(
                ciel_runtime,
                "maybe_handle_import_session_request",
                return_value=True,
            ) as import_session,
            mock.patch.object(
                ciel_runtime,
                "body_with_codex_compat_instructions",
                side_effect=lambda *_args: body,
            ) as compat_instructions,
            mock.patch.object(
                ciel_runtime,
                "_update_tool_schema_registry",
            ) as update_tool_schema,
            mock.patch.object(
                ciel_runtime,
                "select_provider_protocol",
                return_value="openai_responses",
            ),
            mock.patch.object(
                ciel_runtime,
                "forward_provider_responses",
                return_value={},
            ) as forward,
            mock.patch.object(
                ciel_runtime,
                "openai_responses_to_anthropic_messages",
                side_effect=AssertionError(
                    "native Responses route must not run Anthropic projection"
                ),
            ) as to_anthropic,
        ):
            handled = ciel_runtime.route_runtime_post(
                handler,
                config,
                "github-copilot-oauth",
                provider_config,
                "/v1/responses",
                body,
            )

        self.assertTrue(handled)
        import_session.assert_not_called()
        compat_instructions.assert_not_called()
        update_tool_schema.assert_not_called()
        to_anthropic.assert_not_called()
        forward.assert_called_once()
        self.assertEqual("resp_native_state", forward.call_args.args[3]["previous_response_id"])

    def test_remote_bridge_responses_preserve_client_tools_and_skip_host_usage(self):
        handler = mock.Mock()
        handler.path = "/v1/responses"
        setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)
        provider_config = dict(ciel_runtime.DEFAULT_CONFIG["providers"]["vllm"])
        provider_config["current_model"] = "remote-model"
        config = {
            "current_provider": "vllm",
            "providers": {"vllm": provider_config},
        }
        body = {
            "model": "remote-model",
            "input": "search",
            "stream": False,
            "tools": [
                {
                    "type": "function",
                    "name": "WebSearch",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
            "tool_choice": {"type": "function", "name": "WebSearch"},
        }

        with (
            mock.patch.object(
                ciel_runtime,
                "select_provider_protocol",
                return_value="anthropic_messages",
            ),
            mock.patch.object(
                ciel_runtime,
                "normalize_thinking_for_non_anthropic_provider",
                side_effect=lambda _provider, _config, value: value,
            ),
            mock.patch.object(
                ciel_runtime,
                "filter_blocked_tools",
                side_effect=AssertionError("remote tools must remain client-owned"),
            ) as filter_tools,
            mock.patch.object(
                ciel_runtime,
                "normalize_tool_choice_for_provider",
                side_effect=AssertionError("remote choice must remain client-owned"),
            ) as normalize_choice,
            mock.patch.object(
                ciel_runtime,
                "write_context_usage",
                side_effect=AssertionError("remote request must not persist usage"),
            ) as write_context_usage,
            mock.patch.object(
                ciel_runtime,
                "strip_autonomous_advisor_server_tools",
                side_effect=AssertionError("remote tools must not be stripped"),
            ) as strip_advisor,
            mock.patch.object(
                ciel_runtime,
                "normalize_request_for_provider_wire",
                side_effect=AssertionError("remote request must not use local wire repair"),
            ) as normalize_wire,
            mock.patch.object(
                ciel_runtime,
                "collect_provider_message_for_responses",
                return_value={
                    "model": "remote-model",
                    "content": [{"type": "text", "text": "done"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                },
            ),
            mock.patch.object(
                ciel_runtime, "write_openai_responses_response"
            ) as write_response,
            mock.patch.object(ciel_runtime, "dump_request_for_trace"),
        ):
            handled = ciel_runtime.route_runtime_post(
                handler,
                config,
                "vllm",
                provider_config,
                "/v1/responses",
                body,
            )

        self.assertTrue(handled)
        filter_tools.assert_not_called()
        normalize_choice.assert_not_called()
        write_context_usage.assert_not_called()
        strip_advisor.assert_not_called()
        normalize_wire.assert_not_called()
        forwarded = write_response.call_args.kwargs["source_body"]
        self.assertEqual("WebSearch", forwarded["tools"][0]["name"])

    def test_remote_copilot_responses_model_is_adapted_to_chat_wire(self):
        handler = mock.Mock()
        setattr(handler, REMOTE_BRIDGE_CONTEXT_ATTRIBUTE, True)
        provider_config = dict(
            ciel_runtime.DEFAULT_CONFIG["providers"]["github-copilot-oauth"]
        )
        provider_config["current_model"] = "gpt-5.6-luna"
        config = {
            "current_provider": "github-copilot-oauth",
            "providers": {"github-copilot-oauth": provider_config},
        }

        with (
            mock.patch.object(ciel_runtime, "forward_provider_chat") as forward,
            mock.patch.object(
                ciel_runtime, "forward_openai_chat_compatible"
            ) as forward_compatible,
            mock.patch.object(ciel_runtime, "try_write_json") as write_json,
        ):
            handled = ciel_runtime.route_runtime_post(
                handler,
                config,
                "github-copilot-oauth",
                provider_config,
                "/v1/chat/completions",
                {
                    "model": "gpt-5.6-luna",
                    "messages": [],
                    "stream": False,
                },
            )

        self.assertTrue(handled)
        forward.assert_not_called()
        write_json.assert_not_called()
        forward_compatible.assert_called_once_with(
            handler,
            "github-copilot-oauth",
            provider_config,
            {
                "model": "gpt-5.6-luna",
                "messages": [],
                "stream": False,
            },
            "openai_responses",
        )

    def test_runtime_post_delegation_returns_false_for_unowned_path(self):
        self.assertFalse(ciel_runtime.route_runtime_post(object(), {}, "anthropic", {}, "/not-found", {}))

    def test_claude_router_uses_typed_service_groups(self):
        services = ciel_runtime.build_claude_router_services()

        self.assertIsInstance(services, ClaudeRouterServices)
        self.assertIs(services.core.event_bus, ciel_runtime.EVENT_BUS)
        self.assertIs(services.routing.forward_ollama, ciel_runtime.forward_ollama_api_chat)
        self.assertIs(services.transport.open_request, ciel_runtime.open_provider_request_with_key_retry)

    def test_runtime_post_delegation_uses_claude_router_for_count_tokens(self):
        with (
            mock.patch.object(ciel_runtime, "estimate_tokens", return_value=42),
            mock.patch.object(ciel_runtime, "write_context_usage") as write_context_usage,
            mock.patch.object(ciel_runtime, "write_json") as write_json,
        ):
            handled = ciel_runtime.route_runtime_post(
                object(),
                {},
                "anthropic",
                {},
                "/v1/messages/count_tokens",
                {"messages": []},
            )

        self.assertTrue(handled)
        write_context_usage.assert_called_once()
        write_json.assert_called_once_with(mock.ANY, {"input_tokens": 42})

    def test_router_post_uncaught_exception_returns_api_error(self):
        cfg = {"current_provider": "anthropic", "providers": {"anthropic": {}}}
        handler = object.__new__(ciel_runtime.RouterHandler)
        handler.path = "/v1/responses"
        handler.headers = {"content-length": "17"}
        handler.rfile = mock.Mock()
        handler.rfile.read.return_value = b'{"stream": false}'

        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "reject_external_router_request", return_value=False),
            mock.patch.object(ciel_runtime, "handle_llm_config_post", return_value=False),
            mock.patch.object(ciel_runtime, "handle_channel_mcp_post", return_value=False),
            mock.patch.object(ciel_runtime, "handle_chat_post", return_value=False),
            mock.patch.object(ciel_runtime, "handle_plan_post", return_value=False),
            mock.patch.object(ciel_runtime, "route_runtime_post", side_effect=RuntimeError("boom")),
            mock.patch.object(ciel_runtime, "write_openai_responses_error") as write_error,
            mock.patch.object(ciel_runtime, "router_log") as log,
        ):
            handler.do_POST()

        write_error.assert_called_once()
        self.assertIn("Ciel Runtime router error: RuntimeError: boom", write_error.call_args.args[1])
        self.assertFalse(write_error.call_args.kwargs["stream"])
        self.assertEqual(500, write_error.call_args.kwargs["status"])
        self.assertTrue(any("router_post_uncaught" in call.args[1] for call in log.call_args_list))

    def test_router_post_never_writes_a_second_status_after_stream_started(self):
        cfg = {"current_provider": "anthropic", "providers": {"anthropic": {}}}
        handler = object.__new__(ciel_runtime.RouterHandler)
        handler.path = "/v1/responses"
        handler.headers = {"content-length": "16"}
        handler.rfile = mock.Mock()
        handler.rfile.read.return_value = b'{"stream": true}'
        handler.close_connection = False

        def fail_after_start(*_args, **_kwargs):
            handler._ciel_runtime_response_status = 200
            raise RuntimeError("upstream stream read failed")

        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(
                ciel_runtime,
                "reject_external_router_request",
                return_value=False,
            ),
            mock.patch.object(ciel_runtime, "handle_llm_config_post", return_value=False),
            mock.patch.object(ciel_runtime, "handle_channel_mcp_post", return_value=False),
            mock.patch.object(ciel_runtime, "handle_chat_post", return_value=False),
            mock.patch.object(ciel_runtime, "handle_plan_post", return_value=False),
            mock.patch.object(
                ciel_runtime,
                "route_runtime_post",
                side_effect=fail_after_start,
            ),
            mock.patch.object(ciel_runtime, "write_openai_responses_error") as write_error,
            mock.patch.object(ciel_runtime, "try_write_json") as write_json,
            mock.patch.object(ciel_runtime, "router_log") as log,
        ):
            handler.do_POST()

        write_error.assert_not_called()
        write_json.assert_not_called()
        self.assertTrue(handler.close_connection)
        self.assertTrue(
            any(
                "router_post_uncaught_after_response_started" in call.args[1]
                for call in log.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()
