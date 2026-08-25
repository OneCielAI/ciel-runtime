import copy
import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock
import urllib.request

import ciel_runtime


class ZaiProviderTests(unittest.TestCase):
    def zai_cfg(self, **overrides):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["zai"])
        pcfg.update(overrides)
        return {
            "current_provider": "zai",
            "providers": {
                "zai": pcfg,
            },
        }

    def test_provider_is_registered(self):
        self.assertEqual("zai", ciel_runtime.PROVIDER_ALIASES["z.ai"])
        self.assertEqual("zai", ciel_runtime.PROVIDER_ALIASES["zhipu"])
        self.assertEqual("zai", ciel_runtime.PROVIDER_ALIASES["glm"])
        self.assertEqual("Z.AI GLM", ciel_runtime.PROVIDER_LABELS["zai"])
        self.assertEqual(ciel_runtime.ZAI_ANTHROPIC_BASE_URL, ciel_runtime.default_base_url("zai"))

    def test_plan_profiles_use_their_verified_protocol_endpoints(self):
        self.assertEqual(
            "https://api.z.ai/api/paas/v4",
            ciel_runtime.default_base_url("zai-api"),
        )
        self.assertEqual(
            "https://api.z.ai/api/coding/paas/v4",
            ciel_runtime.default_base_url("zai-coding-plan"),
        )
        self.assertEqual(
            "https://zcode.z.ai/api/v1/zcode-plan",
            ciel_runtime.default_base_url("zai-start-plan"),
        )
        coding = ciel_runtime.DEFAULT_CONFIG["providers"]["zai-coding-plan"]
        start = ciel_runtime.DEFAULT_CONFIG["providers"]["zai-start-plan"]
        start_adapter = ciel_runtime.configured_provider_adapter(
            "zai-start-plan", start
        )
        self.assertEqual(
            "anthropic_messages",
            start_adapter.capabilities(
                ciel_runtime.provider_contract_config("zai-start-plan", start)
            ).upstream_protocol,
        )
        self.assertEqual(
            "https://api.z.ai/api/anthropic",
            ciel_runtime.native_anthropic_base_url("zai-coding-plan", coding),
        )
        self.assertEqual(
            "https://zcode.z.ai/api/v1/zcode-plan/anthropic",
            ciel_runtime.native_anthropic_base_url("zai-start-plan", start),
        )
        self.assertEqual(
            "https://zcode.z.ai/api/v1/zcode-plan/chat/completions",
            ciel_runtime.provider_endpoint("zai-start-plan", start, "openai_chat"),
        )
        self.assertEqual(
            "https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages",
            ciel_runtime.provider_endpoint(
                "zai-start-plan", start, "anthropic_messages"
            ),
        )
        self.assertEqual(
            "anthropic_messages",
            ciel_runtime.select_provider_protocol(
                "zai-start-plan", start, "openai_responses", "glm-5.3"
            ),
        )
        self.assertEqual(
            "anthropic_messages",
            ciel_runtime.select_provider_protocol(
                "zai-start-plan", start, "anthropic_messages", "glm-5.3"
            ),
        )
        self.assertEqual(
            "upstream=https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages",
            ciel_runtime.provider_upstream_summary_for_launch(
                "zai-start-plan", start
            ),
        )

    def test_start_plan_keeps_codex_client_protocol_separate_from_upstream(self):
        start = copy.deepcopy(
            ciel_runtime.DEFAULT_CONFIG["providers"]["zai-start-plan"]
        )
        cfg = {
            "current_provider": "zai-start-plan",
            "providers": {"zai-start-plan": start},
        }

        self.assertIn(
            "zai-start-plan", ciel_runtime.CODEX_OPENAI_COMPATIBLE_ROUTER_PROVIDERS
        )
        with (
            mock.patch.object(ciel_runtime, "save_config") as save,
            mock.patch.object(ciel_runtime, "clear_model_cache") as clear_cache,
        ):
            lines = ciel_runtime.apply_launch_endpoint_policy(cfg, "codex")

        self.assertFalse(start["native_compat"])
        self.assertFalse(
            ciel_runtime.provider_native_compat_enabled("zai-start-plan", start)
        )
        self.assertTrue(any("OpenAI Responses client protocol" in line for line in lines))
        self.assertEqual(
            "anthropic_messages",
            ciel_runtime.select_provider_protocol(
                "zai-start-plan", start, "openai_responses", "glm-5.3"
            ),
        )
        self.assertEqual(
            "https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages",
            ciel_runtime.provider_endpoint(
                "zai-start-plan", start, "anthropic_messages"
            ),
        )
        save.assert_called_once_with(cfg)
        clear_cache.assert_called_once()

        with (
            mock.patch.object(ciel_runtime, "save_config") as save,
            mock.patch.object(ciel_runtime, "clear_model_cache") as clear_cache,
        ):
            lines = ciel_runtime.apply_launch_endpoint_policy(cfg, "claude")

        self.assertTrue(start["native_compat"])
        self.assertTrue(
            ciel_runtime.provider_native_compat_enabled("zai-start-plan", start)
        )
        self.assertTrue(any("Anthropic Messages" in line for line in lines))
        save.assert_called_once_with(cfg)
        clear_cache.assert_called_once()

    def test_start_plan_headers_use_its_oauth_jwt_and_zcode_identity(self):
        pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"]["zai-start-plan"])
        pcfg["api_key"] = "oauth-jwt"

        headers = ciel_runtime.provider_headers("zai-start-plan", pcfg)

        self.assertEqual("Bearer oauth-jwt", headers["authorization"])
        self.assertEqual("oauth-jwt", headers["x-api-key"])
        self.assertEqual(
            "ZCode/0.16.3 ai-sdk/provider-utils/4.0.27 runtime/node.js/22",
            headers["User-Agent"],
        )
        self.assertEqual("*/*", headers["Accept"])
        self.assertEqual("*", headers["Accept-Language"])
        self.assertEqual("cors", headers["Sec-Fetch-Mode"])
        self.assertEqual("https://zcode.z.ai", headers["HTTP-Referer"])
        self.assertEqual("production", headers["X-Release-Channel"])
        self.assertTrue(headers["X-Client-Language"])
        self.assertTrue(headers["X-Client-Timezone"])
        self.assertTrue(headers["X-Platform"])
        self.assertTrue(headers["X-Os-Category"])
        self.assertTrue(headers["X-Os-Version"])
        blocker = ciel_runtime.configured_provider_adapter(
            "zai-start-plan", pcfg
        ).launch_api_key_error(
            ciel_runtime.provider_contract_config("zai-start-plan", pcfg)
        )
        self.assertIsNone(blocker)

    def test_start_plan_allows_explicit_official_user_agent_override(self):
        pcfg = copy.deepcopy(
            ciel_runtime.DEFAULT_CONFIG["providers"]["zai-start-plan"]
        )
        pcfg["zcode_user_agent"] = "ZCode/future official-transport/1"

        headers = ciel_runtime.provider_headers("zai-start-plan", pcfg)

        self.assertEqual(
            "ZCode/future official-transport/1", headers["User-Agent"]
        )

    def test_start_plan_anthropic_wire_matches_official_zcode_options(self):
        pcfg = copy.deepcopy(
            ciel_runtime.DEFAULT_CONFIG["providers"]["zai-start-plan"]
        )
        body = {
            "model": "glm-5.3",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 131072,
            "reasoning_effort": "xhigh",
            "thinking": {"type": "enabled"},
        }

        normalized = ciel_runtime.normalize_anthropic_model_request_options(
            "zai-start-plan", pcfg, body, "glm-5.3"
        )

        self.assertNotIn("reasoning_effort", normalized)
        self.assertEqual({"effort": "max"}, normalized["output_config"])
        self.assertEqual(
            {"type": "enabled", "budget_tokens": 32000},
            normalized["thinking"],
        )
        self.assertEqual(128000, normalized["max_tokens"])

    def test_start_plan_max_effort_adds_official_thinking_when_absent(self):
        pcfg = copy.deepcopy(
            ciel_runtime.DEFAULT_CONFIG["providers"]["zai-start-plan"]
        )
        body = {
            "model": "glm-5.3",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 131072,
        }

        normalized = ciel_runtime.normalize_anthropic_model_request_options(
            "zai-start-plan", pcfg, body, "glm-5.3"
        )

        self.assertEqual({"effort": "max"}, normalized["output_config"])
        self.assertEqual(
            {"type": "enabled", "budget_tokens": 32000},
            normalized["thinking"],
        )

    def test_zcode_wire_version_migration_updates_only_the_old_default(self):
        cfg = {
            "providers": {
                "zai": {"zcode_app_version": "3.8.1"},
                "zai-api": {"zcode_app_version": "custom-version"},
                "zai-coding-plan": {"zcode_app_version": "3.8.1"},
                "zai-start-plan": {"zcode_app_version": "3.8.1"},
            },
            "migrations": {},
        }

        ciel_runtime.apply_config_migrations(cfg)

        self.assertEqual("0.16.3", cfg["providers"]["zai"]["zcode_app_version"])
        self.assertEqual(
            "custom-version", cfg["providers"]["zai-api"]["zcode_app_version"]
        )
        self.assertEqual(
            "0.16.3", cfg["providers"]["zai-coding-plan"]["zcode_app_version"]
        )
        self.assertEqual(
            "0.16.3", cfg["providers"]["zai-start-plan"]["zcode_app_version"]
        )
        self.assertTrue(cfg["migrations"]["zcode_wire_version_0163_20260824"])

    def test_codex_translation_adds_anthropic_version_for_start_plan(self):
        pcfg = copy.deepcopy(
            ciel_runtime.DEFAULT_CONFIG["providers"]["zai-start-plan"]
        )
        pcfg["api_key"] = "oauth-jwt"

        headers = ciel_runtime.provider_headers(
            "zai-start-plan",
            pcfg,
            {
                "content-type": "application/json",
                "accept": "text/event-stream",
                "user-agent": "codex-cli/0.149.1",
            },
            "anthropic_messages",
        )

        self.assertEqual("2023-06-01", headers["anthropic-version"])

    def test_start_plan_wire_request_keeps_zcode_user_agent_and_bearer_token(self):
        received = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                received.update(dict(self.headers.items()))
                length = int(self.headers.get("Content-Length") or "0")
                self.rfile.read(length)
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        try:
            pcfg = copy.deepcopy(
                ciel_runtime.DEFAULT_CONFIG["providers"]["zai-start-plan"]
            )
            pcfg["api_key"] = "oauth-jwt"
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/messages",
                data=b"{}",
                headers=ciel_runtime.provider_headers("zai-start-plan", pcfg),
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=2.0) as response:
                self.assertEqual(200, response.status)
        finally:
            thread.join(timeout=2.0)
            server.server_close()

        self.assertEqual(
            "ZCode/0.16.3 ai-sdk/provider-utils/4.0.27 runtime/node.js/22",
            received["User-Agent"],
        )
        self.assertEqual("*/*", received["Accept"])
        self.assertEqual("*", received["Accept-Language"])
        self.assertEqual("cors", received["Sec-Fetch-Mode"])
        self.assertEqual("Bearer oauth-jwt", received["Authorization"])
        self.assertEqual("oauth-jwt", received["X-Api-Key"])

    def test_all_zai_profiles_use_the_configured_zcode_user_agent(self):
        for provider in ("zai", "zai-api", "zai-coding-plan", "zai-start-plan"):
            with self.subTest(provider=provider):
                pcfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"][provider])
                pcfg.update({"api_key": "credential", "zcode_app_version": "3.8.1"})

                headers = ciel_runtime.provider_headers(provider, pcfg)

                expected = (
                    "ZCode/3.8.1 ai-sdk/provider-utils/4.0.27 runtime/node.js/22"
                    if provider == "zai-start-plan"
                    else "ZCode/3.8.1"
                )
                self.assertEqual(expected, headers["User-Agent"])

    def test_start_plan_remote_captcha_options_are_ctl_mutable(self):
        pcfg = copy.deepcopy(
            ciel_runtime.DEFAULT_CONFIG["providers"]["zai-start-plan"]
        )
        adapter = ciel_runtime.configured_provider_adapter("zai-start-plan", pcfg)
        policy = adapter.configuration_policy(
            ciel_runtime.provider_contract_config("zai-start-plan", pcfg)
        )

        self.assertEqual(
            "zai_captcha_bind_host", policy.text_option_aliases["captcha_bind_host"]
        )
        self.assertEqual(
            "zai_captcha_port", policy.text_option_aliases["captcha_port"]
        )
        self.assertEqual(
            "zai_captcha_public_base_url",
            policy.text_option_aliases["captcha_public_base_url"],
        )

    def test_default_config_matches_zai_claude_code_docs(self):
        pcfg = ciel_runtime.DEFAULT_CONFIG["providers"]["zai"]
        self.assertEqual("https://api.z.ai/api/anthropic", pcfg["base_url"])
        self.assertEqual("glm-5.3[1m]", pcfg["current_model"])
        self.assertEqual("glm-5.3[1m]", pcfg["opus_model"])
        self.assertEqual("glm-5.3[1m]", pcfg["sonnet_model"])
        self.assertEqual("glm-4.7", pcfg["haiku_model"])
        self.assertEqual(1000000, pcfg["context_window"])
        self.assertEqual(1000000, pcfg["auto_compact_window"])
        self.assertEqual(131072, pcfg["max_output_tokens"])
        self.assertEqual(131072, pcfg["context_reserve_tokens"])
        self.assertEqual(3000000, pcfg["request_timeout_ms"])
        self.assertTrue(pcfg["native_compat"])
        self.assertTrue(pcfg["preserve_anthropic_thinking"])
        self.assertIn("thinking", pcfg["claude_code_supported_capabilities"])

    def test_model_suffix_is_preserved_for_zai_one_million_context(self):
        self.assertEqual("glm-5.2[1m]", ciel_runtime.normalize_model_id("zai", "glm-5.2[1m]"))
        pcfg = self.zai_cfg(current_model="glm-5.2[1m]")["providers"]["zai"]

        self.assertEqual(1000000, ciel_runtime.provider_model_context_capacity("zai", pcfg))

    def test_glm53_profile_and_request_options_follow_official_contract(self):
        pcfg = self.zai_cfg(current_model="glm-5.3[1m]")["providers"]["zai"]
        adapter = ciel_runtime.configured_provider_adapter("zai", pcfg)
        profile, notice = adapter.model_configuration_profile(
            ciel_runtime.provider_contract_config("zai", pcfg)
        )
        self.assertEqual(1_000_000, profile["context_window"])
        self.assertEqual(131_072, profile["max_output_tokens"])
        self.assertIn("1M context", notice)

        normalized = ciel_runtime.apply_provider_adapter_request_policy(
            "zai",
            pcfg,
            {"model": "glm-5.3", "thinking": {"type": "disabled"}, "reasoning_effort": "medium", "temperature": 0.2},
        )
        self.assertEqual({"type": "enabled"}, normalized["thinking"])
        self.assertEqual("high", normalized["reasoning_effort"])
        self.assertEqual(1.0, normalized["temperature"])

    def test_glm51_and_glm52_profiles_follow_official_context_and_output_limits(self):
        cases = {
            "glm-5.1": 200_000,
            "glm-5.2": 1_000_000,
        }
        for model, context_window in cases.items():
            with self.subTest(model=model):
                pcfg = self.zai_cfg(current_model=model)["providers"]["zai"]
                adapter = ciel_runtime.configured_provider_adapter("zai", pcfg)
                profile, notice = adapter.model_configuration_profile(
                    ciel_runtime.provider_contract_config("zai", pcfg)
                )
                self.assertEqual(context_window, profile["context_window"])
                self.assertEqual(131_072, profile["max_output_tokens"])
                self.assertNotIn("effort_level", profile)
                self.assertIn("128K maximum output", notice)

    def test_zai_turbo_suffix_does_not_claim_one_million_context(self):
        pcfg = self.zai_cfg(
            current_model="glm-5-turbo[1m]",
            context_window=524288,
            auto_compact_window=1000000,
            max_output_tokens=32768,
            context_reserve_tokens=16384,
        )["providers"]["zai"]

        self.assertEqual(200000, ciel_runtime.provider_model_context_capacity("zai", pcfg))
        self.assertEqual(200000, ciel_runtime.context_limit_for_status("zai", pcfg))
        self.assertEqual(200000, ciel_runtime.claude_code_auto_compact_window("zai", pcfg))
        self.assertEqual("long-context", ciel_runtime.model_option_family("zai", pcfg))
        self.assertEqual("long-context-128k", ciel_runtime.recommended_preset_id("zai", pcfg))

        messages = ciel_runtime.cap_context_settings_to_model_capacity("zai", pcfg)
        messages.extend(ciel_runtime.cap_output_settings_to_context_ratio("zai", pcfg))

        self.assertEqual(200000, pcfg["context_window"])
        self.assertEqual(6144, pcfg["max_output_tokens"])
        self.assertTrue(any("Context window capped" in line for line in messages))

    def test_provider_headers_include_zai_api_key(self):
        pcfg = self.zai_cfg(api_key="sk-zai-test")["providers"]["zai"]

        headers = ciel_runtime.provider_headers("zai", pcfg)

        self.assertEqual("Bearer sk-zai-test", headers["authorization"])
        self.assertEqual("sk-zai-test", headers["x-api-key"])
        self.assertEqual("2023-06-01", headers["anthropic-version"])
        self.assertEqual("claude-cli", headers["user-agent"])

    def test_model_list_fetches_zai_models_and_keeps_documented_fallbacks(self):
        pcfg = self.zai_cfg(api_key="sk-zai-test", custom_models=[])["providers"]["zai"]
        response = {
            "data": [
                {
                    "id": "glm-5.2[1m]",
                    "context_length": 1000000,
                }
            ]
        }

        with (
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=None),
            mock.patch.object(ciel_runtime, "http_json", return_value=response) as http_json,
            mock.patch.object(ciel_runtime, "write_model_list_cache") as write_cache,
        ):
            models = ciel_runtime.upstream_model_ids("zai", pcfg)

        self.assertIn("glm-5.2[1m]", models)
        self.assertIn("glm-4.7", models)
        self.assertTrue(http_json.call_args.args[0].endswith("/anthropic/v1/models"))
        write_cache.assert_called_once()
        metadata = write_cache.call_args.args[3]
        self.assertEqual(1000000, metadata["model_info"]["glm-5.2[1m]"]["max_model_len"])

    def test_model_list_falls_back_to_documented_zai_models_without_network(self):
        pcfg = self.zai_cfg(api_key="", custom_models=[])["providers"]["zai"]

        with (
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=None),
            mock.patch.object(ciel_runtime, "http_json", side_effect=RuntimeError("network down")),
            mock.patch.object(ciel_runtime, "write_model_list_cache") as write_cache,
        ):
            models = ciel_runtime.upstream_model_ids("zai", pcfg)

        self.assertIn("glm-5.2[1m]", models)
        self.assertIn("glm-5-turbo", models)
        self.assertIn("glm-4.7-flash", models)
        self.assertIn("glm-4.7-flashx", models)
        self.assertIn("glm-4.6", models)
        self.assertIn("glm-4.5-flash", models)
        self.assertIn("glm-4-32b-0414-128k", models)
        write_cache.assert_called_once()

    def test_zai_documented_model_context_hints_cover_current_text_models(self):
        cases = {
            "glm-5.3": 1000000,
            "glm-5.3[1m]": 1000000,
            "glm-5.2": 1000000,
            "glm-5.2[1m]": 1000000,
            "glm-5.1": 200000,
            "glm-5": 200000,
            "glm-5-turbo": 200000,
            "glm-4.7": 200000,
            "glm-4.7-flashx": 200000,
            "glm-4.7-flash": 200000,
            "glm-4.6": 200000,
            "glm-4.5": 128000,
            "glm-4.5-x": 128000,
            "glm-4.5-airx": 128000,
            "glm-4.5-air": 128000,
            "glm-4.5-flash": 128000,
            "glm-4-32b-0414-128k": 128000,
        }
        for model, expected in cases.items():
            with self.subTest(model=model):
                self.assertEqual(expected, ciel_runtime.model_context_hint_from_model_id(model))

    def test_env_vars_route_zai_through_ciel_runtime_router_with_glm_defaults(self):
        cfg = self.zai_cfg(api_key="sk-zai-test")

        env = ciel_runtime.env_vars(cfg)

        self.assertEqual("zai", env["CIEL_RUNTIME_PROVIDER"])
        self.assertEqual(ciel_runtime.ROUTER_BASE, env["ANTHROPIC_BASE_URL"])
        self.assertEqual("sk-zai-test", env["ANTHROPIC_AUTH_TOKEN"])
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual("1000000", env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"])
        self.assertEqual("131072", env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"])
        self.assertEqual("ciel-runtime-zai-glm-4.7", env["ANTHROPIC_DEFAULT_HAIKU_MODEL"])
        self.assertEqual("ciel-runtime-zai-glm-5.3-1m[1m]", env["ANTHROPIC_DEFAULT_OPUS_MODEL"])
        self.assertEqual("ciel-runtime-zai-glm-5.3-1m[1m]", env["ANTHROPIC_DEFAULT_SONNET_MODEL"])
        self.assertIn("thinking", env["ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES"])

    def test_zai_glm52_without_suffix_still_exposes_one_million_marker_to_claude_code(self):
        cfg = self.zai_cfg(api_key="sk-zai-test", current_model="glm-5.2", opus_model="glm-5.2", sonnet_model="glm-5.2")

        env = ciel_runtime.env_vars(cfg)

        self.assertEqual("ciel-runtime-zai-glm-5.2[1m]", env["ANTHROPIC_MODEL"])
        self.assertEqual("ciel-runtime-zai-glm-5.2[1m]", env["ANTHROPIC_DEFAULT_OPUS_MODEL"])
        self.assertEqual("ciel-runtime-zai-glm-5.2[1m]", env["ANTHROPIC_DEFAULT_SONNET_MODEL"])

    def test_zai_turbo_context_suffix_does_not_expose_one_million_marker_to_claude_code(self):
        cfg = self.zai_cfg(
            api_key="sk-zai-test",
            current_model="glm-5-turbo[1m]",
            opus_model="glm-5-turbo[1m]",
            sonnet_model="glm-5-turbo[1m]",
        )

        env = ciel_runtime.env_vars(cfg)

        self.assertNotIn("[1m]", env["ANTHROPIC_MODEL"])
        self.assertNotIn("[1m]", env["ANTHROPIC_DEFAULT_OPUS_MODEL"])
        self.assertNotIn("[1m]", env["ANTHROPIC_DEFAULT_SONNET_MODEL"])
        self.assertEqual("200000", env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"])

    def test_zai_set_model_aligns_claude_code_default_families_with_selected_model(self):
        cfg = self.zai_cfg(api_key="sk-zai-test")

        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "save_config"),
            mock.patch.object(ciel_runtime, "clear_model_cache"),
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=["glm-4.7-flash"]),
            mock.patch.object(ciel_runtime, "read_model_info_cache", return_value={}),
        ):
            messages = ciel_runtime.set_model_config("glm-4.7-flash")

        pcfg = cfg["providers"]["zai"]
        self.assertEqual("glm-4.7-flash", pcfg["current_model"])
        self.assertEqual("glm-4.7-flash", pcfg["haiku_model"])
        self.assertEqual("glm-4.7-flash", pcfg["opus_model"])
        self.assertEqual("glm-4.7-flash", pcfg["sonnet_model"])
        self.assertTrue(any("Model for zai set to glm-4.7-flash" in message for message in messages))

        env = ciel_runtime.env_vars(cfg)
        self.assertEqual("ciel-runtime-zai-glm-4.7-flash", env["ANTHROPIC_MODEL"])
        self.assertEqual("ciel-runtime-zai-glm-4.7-flash", env["ANTHROPIC_DEFAULT_HAIKU_MODEL"])
        self.assertEqual("ciel-runtime-zai-glm-4.7-flash", env["ANTHROPIC_DEFAULT_OPUS_MODEL"])
        self.assertEqual("ciel-runtime-zai-glm-4.7-flash", env["ANTHROPIC_DEFAULT_SONNET_MODEL"])

    def test_resolve_requested_model_strips_zai_context_suffix_for_api(self):
        cfg = self.zai_cfg(api_key="sk-zai-test")
        pcfg = cfg["providers"]["zai"]

        self.assertEqual("glm-5.3[1m]", ciel_runtime.current_upstream_model_id("zai", pcfg))
        self.assertEqual(
            "glm-5.2",
            ciel_runtime.resolve_requested_model("zai", pcfg, "ciel-runtime-zai-glm-5.2-1m[1m]"),
        )
        self.assertEqual("glm-5.2", ciel_runtime.resolve_requested_model("zai", pcfg, "glm-5.2[1m]"))
        self.assertEqual("glm-5-turbo", ciel_runtime.resolve_requested_model("zai", pcfg, "glm-5-turbo[1m]"))

    def test_compatibility_test_uses_zai_api_model_without_context_suffix(self):
        cfg = self.zai_cfg(api_key="sk-zai-test")
        response = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "glm-5.2",
            "content": [{"type": "text", "text": "OK"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        args = type("Args", (), {"mode": "quick", "timeout": 10})()

        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "save_config"),
            mock.patch.object(ciel_runtime, "endpoint_route_exists", return_value=None),
            mock.patch.object(ciel_runtime, "post_json", return_value=response) as post_json,
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                ciel_runtime._cmd_test(args)

        request_body = post_json.call_args.args[1]
        self.assertEqual("glm-5.3", request_body["model"])
        self.assertIn("Model: glm-5.3[1m]", stdout.getvalue())
        self.assertIn("API model: glm-5.3", stdout.getvalue())

    def test_zai_glm47_flash_compatibility_fails_fast_before_tool_timeout(self):
        cfg = self.zai_cfg(api_key="sk-zai-test", current_model="glm-4.7-flash")
        response = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "glm-4.7-flash",
            "content": [{"type": "text", "text": "OK"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 17, "output_tokens": 2},
        }
        args = type("Args", (), {"mode": "full", "timeout": 120})()

        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "save_config"),
            mock.patch.object(ciel_runtime, "endpoint_route_exists", return_value=None),
            mock.patch.object(ciel_runtime, "run_compatibility_api_key_probes", return_value=[]),
            mock.patch.object(ciel_runtime, "post_json", return_value=response) as post_json,
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout), self.assertRaises(SystemExit) as raised:
                ciel_runtime._cmd_test(args)

        self.assertEqual(1, raised.exception.code)
        self.assertEqual(1, post_json.call_count)
        output = stdout.getvalue()
        self.assertIn("Text response: OK", output)
        self.assertIn("Compatibility: FAIL", output)
        self.assertIn("GLM-4.7-Flash", output)
        self.assertIn("direct Anthropic tool-use probes time out", output)

    def test_zai_glm47_flash_is_the_only_known_tool_use_blocker(self):
        self.assertIn(
            "tool-use probes time out",
            ciel_runtime.known_compatibility_tool_use_blocker("zai", "glm-4.7-flash"),
        )
        self.assertEqual("", ciel_runtime.known_compatibility_tool_use_blocker("zai", "glm-4.5-flash"))
        self.assertEqual("", ciel_runtime.known_compatibility_tool_use_blocker("zai", "glm-4.7"))

    def test_zai_managed_mcp_config_contains_official_servers(self):
        pcfg = self.zai_cfg(api_key="sk-zai-test")["providers"]["zai"]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "zai-mcp.json"
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "ZAI_MCP_CONFIG", path),
                mock.patch.object(ciel_runtime, "find_executable", side_effect=lambda name: f"/bin/{name}"),
            ):
                written = ciel_runtime.write_zai_mcp_config("zai", pcfg)

            self.assertEqual(path, written)
            data = json.loads(path.read_text(encoding="utf-8"))
            servers = data["mcpServers"]
            self.assertEqual("/bin/npx", servers["zai-mcp-server"]["command"])
            self.assertEqual(["-y", "@z_ai/mcp-server@latest"], servers["zai-mcp-server"]["args"])
            self.assertEqual("sk-zai-test", servers["zai-mcp-server"]["env"]["Z_AI_API_KEY"])
            self.assertEqual("ZAI", servers["zai-mcp-server"]["env"]["Z_AI_MODE"])
            self.assertEqual("https://api.z.ai/api/mcp/web_search_prime/mcp", servers["web-search-prime"]["url"])
            self.assertEqual("https://api.z.ai/api/mcp/web_reader/mcp", servers["web-reader"]["url"])
            self.assertEqual("https://api.z.ai/api/mcp/zread/mcp", servers["zread"]["url"])
            self.assertEqual("Bearer sk-zai-test", servers["web-search-prime"]["headers"]["Authorization"])

    def test_zai_managed_mcp_config_is_removed_for_other_providers(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "zai-mcp.json"
            path.write_text("{}", encoding="utf-8")
            with mock.patch.object(ciel_runtime, "ZAI_MCP_CONFIG", path):
                ciel_runtime.reset_zai_mcp_config_if_inactive("deepseek")
            self.assertFalse(path.exists())

    def test_zai_uses_managed_mcp_instead_of_generic_web_search_by_default(self):
        cfg = self.zai_cfg(api_key="sk-zai-test")
        self.assertFalse(ciel_runtime.should_attach_web_search("zai", cfg, None))
        self.assertTrue(ciel_runtime.should_attach_web_search("zai", cfg, True))

    def test_launch_requires_zai_api_key(self):
        with mock.patch.object(ciel_runtime, "base_url_status_line", return_value="Base URL: Z.AI configured"):
            errors = ciel_runtime.launch_readiness_errors(self.zai_cfg(api_key=""))
        self.assertTrue(any("Z.AI GLM requires" in err for err in errors))
        self.assertTrue(ciel_runtime.launch_blockers_require_api_key(errors))


if __name__ == "__main__":
    unittest.main()
