import unittest
from unittest import mock

import ciel_runtime
from ciel_runtime_support.agent_router import COMMON_RUNTIME_ROUTER_CAPABILITIES
from ciel_runtime_support.claude_router import ClaudeRouter, missing_claude_runtime_dependencies
from ciel_runtime_support.codex_router import CodexRouter


class RuntimeRouterTests(unittest.TestCase):
    def test_runtime_router_matrix_has_claude_and_codex_with_common_capabilities(self):
        matrix = ciel_runtime.runtime_router_capability_matrix()

        self.assertEqual({"claude", "codex"}, set(matrix))
        self.assertEqual({}, ciel_runtime.runtime_router_capability_gaps())
        for router in ("claude", "codex"):
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
        )
        codex_pcfg = {"route_through_router": True}

        self.assertTrue(router.can_handle_get("/backend-api/codex/models", "codex", codex_pcfg))
        self.assertTrue(router.can_handle_post("/backend-api/codex/responses", "codex", codex_pcfg))
        self.assertTrue(router.can_handle_post("/backend-api/codex/models", "codex", codex_pcfg))
        self.assertTrue(router.can_handle_post("/v1/responses", "anthropic", {}))
        self.assertFalse(router.can_handle_get("/backend-api/codex/models", "anthropic", codex_pcfg))
        self.assertFalse(router.can_handle_post("/v1/messages", "codex", codex_pcfg))

        self.assertTrue(router.handle_get(object(), "/backend-api/codex/models", "codex", codex_pcfg))
        self.assertTrue(router.handle_post(object(), {}, "codex", codex_pcfg, "/backend-api/codex/responses", {"a": 1}))
        self.assertTrue(router.handle_post(object(), {}, "codex", codex_pcfg, "/backend-api/codex/models", {"b": 2}))
        self.assertEqual([("get", "codex"), ("responses", {"a": 1}), ("post", {"b": 2})], calls)

    def test_runtime_post_delegation_returns_false_for_unowned_path(self):
        self.assertFalse(ciel_runtime.route_runtime_post(object(), {}, "anthropic", {}, "/not-found", {}))

    def test_claude_router_runtime_dependencies_are_present_in_main_module(self):
        self.assertEqual([], missing_claude_runtime_dependencies(vars(ciel_runtime)))

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
            mock.patch.object(ciel_runtime, "handle_codex_mcp_split_proxy_request", return_value=False),
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


if __name__ == "__main__":
    unittest.main()
