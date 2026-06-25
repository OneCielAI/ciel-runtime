import copy
import unittest
from unittest import mock

import ciel_runtime


class OpenRouterProviderTests(unittest.TestCase):
    def setUp(self):
        with ciel_runtime._API_KEY_ROTATION_LOCK:
            ciel_runtime._API_KEY_ROTATION_CURSOR.clear()

    def openrouter_pcfg(self, **overrides):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["openrouter"])
        pcfg.update(overrides)
        return pcfg

    def openrouter_cfg(self, **overrides):
        return {
            "current_provider": "openrouter",
            "providers": {"openrouter": self.openrouter_pcfg(**overrides)},
        }

    def test_provider_alias_and_label(self):
        self.assertEqual("openrouter", ciel_runtime.normalize_provider("or"))
        self.assertEqual("openrouter", ciel_runtime.normalize_provider("open-router"))
        self.assertEqual("OpenRouter", ciel_runtime.PROVIDER_LABELS["openrouter"])

    def test_default_model_is_nemotron_free(self):
        pcfg = ciel_runtime.DEFAULT_CONFIG["providers"]["openrouter"]
        self.assertEqual("nvidia/nemotron-3-ultra-550b-a55b:free", pcfg["current_model"])
        self.assertFalse(pcfg["native_compat"])
        self.assertEqual("https://openrouter.ai/api/v1", pcfg["base_url"])

    def test_routes_via_openai_compatible_path(self):
        pcfg = self.openrouter_pcfg()
        self.assertTrue(ciel_runtime.provider_openai_router_enabled("openrouter", pcfg))

    def test_base_url_joins_correctly(self):
        base = ciel_runtime.default_base_url("openrouter")
        self.assertEqual(
            "https://openrouter.ai/api/v1/chat/completions",
            ciel_runtime.join_url(base, "/v1/chat/completions"),
        )
        self.assertEqual(
            "https://openrouter.ai/api/v1/models",
            ciel_runtime.join_url(base, "/v1/models"),
        )

    def test_provider_headers_emit_bearer(self):
        headers = ciel_runtime.provider_headers("openrouter", self.openrouter_pcfg(api_key="sk-or-test"))
        self.assertEqual("Bearer sk-or-test", headers["Authorization"])
        self.assertEqual("sk-or-test", headers["x-api-key"])
        self.assertNotIn("authorization", headers)

    def test_multi_key_round_robin(self):
        pcfg = self.openrouter_pcfg(api_key="", api_keys=["sk-or-one", "sk-or-two"])
        first = ciel_runtime.provider_headers("openrouter", pcfg)
        second = ciel_runtime.provider_headers("openrouter", pcfg)
        third = ciel_runtime.provider_headers("openrouter", pcfg)
        self.assertEqual("Bearer sk-or-one", first["Authorization"])
        self.assertEqual("Bearer sk-or-two", second["Authorization"])
        self.assertEqual("Bearer sk-or-one", third["Authorization"])

    def test_select_provider_api_key_rotates(self):
        pcfg = self.openrouter_pcfg(api_key="", api_keys=["sk-or-one", "sk-or-two"])
        self.assertEqual("sk-or-one", ciel_runtime.select_provider_api_key("openrouter", pcfg))
        self.assertEqual("sk-or-two", ciel_runtime.select_provider_api_key("openrouter", pcfg))
        self.assertEqual("sk-or-one", ciel_runtime.select_provider_api_key("openrouter", pcfg))

    def test_provider_headers_require_openrouter_api_key(self):
        with self.assertRaisesRegex(RuntimeError, "OpenRouter requires"):
            ciel_runtime.provider_headers("openrouter", self.openrouter_pcfg(api_key="", api_keys=[]))

    def test_provider_selectable_in_menu(self):
        rows, values = ciel_runtime.provider_panel_rows(self.openrouter_cfg())
        self.assertIn("openrouter", values)
        self.assertTrue(any("OpenRouter" in row for row in rows))

    def test_provider_options_status_full_parity(self):
        status = ciel_runtime.provider_options_status("openrouter", self.openrouter_pcfg())
        self.assertIn("context_window=262144", status)
        self.assertIn("max_output_tokens=8192", status)
        self.assertIn("temperature=", status)

    def test_provider_options_command_accepts_openrouter(self):
        self.assertIn("openrouter", ciel_runtime.PROVIDER_OPTION_PROVIDERS)
        self.assertIn("openrouter", ciel_runtime.PROVIDER_SAMPLING_OPTION_PROVIDERS)

    def test_env_vars_route_through_router(self):
        env = ciel_runtime.env_vars(self.openrouter_cfg(api_key="sk-or-test"))
        self.assertEqual("openrouter", env["CIEL_RUNTIME_PROVIDER"])
        self.assertEqual(ciel_runtime.ROUTER_BASE, env["ANTHROPIC_BASE_URL"])
        self.assertEqual("sk-or-test", env["ANTHROPIC_AUTH_TOKEN"])
        self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_launch_requires_api_key(self):
        errors = ciel_runtime.launch_readiness_errors(self.openrouter_cfg(api_key=""))
        self.assertTrue(any("OpenRouter requires" in err for err in errors))

    def test_router_dispatches_to_openai_compatible_forwarder(self):
        cfg = self.openrouter_cfg(api_key="sk-or-test")
        cfg["router_debug_message_preview_chars"] = 0
        handler = object.__new__(ciel_runtime.RouterHandler)
        handler.path = "/v1/messages"
        handler.headers = {"content-length": "2"}
        handler.rfile = mock.Mock()
        handler.rfile.read.return_value = b"{}"
        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "reject_external_router_request", return_value=False),
            mock.patch.object(ciel_runtime, "handle_llm_config_post", return_value=False),
            mock.patch.object(ciel_runtime, "handle_channel_mcp_post", return_value=False),
            mock.patch.object(ciel_runtime, "handle_chat_post", return_value=False),
            mock.patch.object(ciel_runtime, "handle_plan_post", return_value=False),
            mock.patch.object(ciel_runtime, "maybe_handle_plan_mode_tool_choice", return_value=False),
            mock.patch.object(ciel_runtime, "filter_blocked_tools", side_effect=lambda _p, _c, b: b),
            mock.patch.object(ciel_runtime, "write_context_usage"),
            mock.patch.object(ciel_runtime, "maybe_handle_router_debug_request", return_value=False),
            mock.patch.object(ciel_runtime, "maybe_handle_advisor_request", return_value=False),
            mock.patch.object(ciel_runtime, "body_with_pending_channel_messages", side_effect=lambda b: b),
            mock.patch.object(ciel_runtime, "dump_request_for_trace"),
            mock.patch.object(ciel_runtime, "forward_openai_compatible_chat") as forward,
        ):
            handler.do_POST()
        forward.assert_called_once()
        self.assertEqual("openrouter", forward.call_args.args[1])

    def test_openrouter_stream_request_sends_authorization_header(self):
        pcfg = self.openrouter_pcfg(api_key="", api_keys=["sk-or-one", "sk-or-two"])
        captured = {}

        class FakeResponse:
            headers = {}

            def read(self):
                return b""

        def fake_urlopen(req, timeout):
            captured["timeout"] = timeout
            captured["headers"] = dict(req.header_items())
            return FakeResponse()

        body = {"model": "test", "messages": [{"role": "user", "content": "hello"}], "stream": True}
        with (
            mock.patch.object(ciel_runtime.urllib.request, "urlopen", side_effect=fake_urlopen),
            mock.patch.object(ciel_runtime, "set_upstream_stream_read_timeout"),
        ):
            ciel_runtime.open_openai_stream_with_rate_retry(
                "https://openrouter.ai/api/v1/chat/completions",
                body,
                ciel_runtime.provider_headers("openrouter", pcfg),
                7.0,
                "openrouter",
                pcfg,
                "test",
            )

        self.assertEqual(7.0, captured["timeout"])
        self.assertEqual("Bearer sk-or-one", captured["headers"].get("Authorization"))
        self.assertEqual("sk-or-one", captured["headers"].get("X-api-key"))


if __name__ == "__main__":
    unittest.main()
