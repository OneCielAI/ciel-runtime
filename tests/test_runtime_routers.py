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


if __name__ == "__main__":
    unittest.main()
