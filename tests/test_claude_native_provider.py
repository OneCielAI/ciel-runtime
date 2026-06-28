"""Tests for the Claude Native provider mode contract:
- PROVIDER_LABELS exposes the "Claude Native" label.
- PROVIDER_ALIASES accepts claude-native / native / claude-code synonyms.
- env_vars() for native mode injects only the minimal marker (and
  ANTHROPIC_API_KEY when stored), so all Anthropic backend, model, advisor,
  output-token, auto-compact and similar settings revert to Claude Code's
  own defaults.
- stop_router_with_guarantee() polls router_up() and raises if the router
  can't be brought down.
- cleanup_managed_services_for_provider() always kills the router for native,
  even when the cleanup.managed_services_on_launch config gate is off.
"""
import tempfile
import io
import os
import urllib.error
import unittest
from pathlib import Path
from unittest import mock

import ciel_runtime


class ProviderLabelTests(unittest.TestCase):
    def test_label_is_claude_native(self):
        self.assertEqual("Claude Native", ciel_runtime.PROVIDER_LABELS["anthropic"])

    def test_aliases_route_to_anthropic(self):
        for alias in ("anthropic", "claude", "claude-native", "native", "claude-code"):
            self.assertEqual("anthropic", ciel_runtime.PROVIDER_ALIASES[alias])

    def test_provider_menu_exposes_native_and_routed_anthropic_choices(self):
        cfg = {
            "current_provider": "anthropic",
            "providers": {
                "anthropic": {
                    "base_url": "https://api.anthropic.com",
                    "api_key": "",
                    "route_through_router": False,
                },
            },
        }

        rows, values = ciel_runtime.provider_panel_rows(cfg)

        self.assertIn(ciel_runtime.ANTHROPIC_NATIVE_PROVIDER_CHOICE, values)
        self.assertIn(ciel_runtime.ANTHROPIC_ROUTED_PROVIDER_CHOICE, values)
        self.assertTrue(any("Claude Native" in row and row.startswith("*") for row in rows))
        self.assertTrue(any("Anthropic routed" in row and "Claude Code auth" in row for row in rows))

    def test_provider_menu_marks_routed_anthropic_choice(self):
        cfg = {
            "current_provider": "anthropic",
            "providers": {
                "anthropic": {
                    "base_url": "https://api.anthropic.com",
                    "api_key": "sk-ant-real",
                    "route_through_router": True,
                },
            },
        }

        rows, _ = ciel_runtime.provider_panel_rows(cfg)

        self.assertTrue(any("Anthropic routed" in row and row.startswith("*") for row in rows))
        self.assertTrue(any("Claude Native" in row and row.startswith(" ") for row in rows))
        self.assertEqual(
            ciel_runtime.ANTHROPIC_ROUTED_PROVIDER_CHOICE,
            ciel_runtime.current_provider_panel_choice("anthropic", cfg["providers"]["anthropic"]),
        )

    def test_main_menu_provider_label_reflects_anthropic_route_mode(self):
        cfg = {"language": "en"}
        native = {"route_through_router": False, "advisor_model": "", "current_model": "claude-opus-4-7"}
        routed = {"route_through_router": True, "advisor_model": "", "current_model": "claude-opus-4-7"}

        self.assertIn("Provider  [Claude Native]", ciel_runtime.main_menu_rows(cfg, "anthropic", native, "en")[1])
        self.assertIn("Provider  [Anthropic routed]", ciel_runtime.main_menu_rows(cfg, "anthropic", routed, "en")[1])

    def test_compat_prompt_is_not_added_to_anthropic_modes(self):
        cfg = {"claude_code": {"compat_prompt_for_non_anthropic": True}}

        self.assertFalse(ciel_runtime.should_append_compat_prompt("anthropic", {"route_through_router": False}, cfg))
        self.assertFalse(ciel_runtime.should_append_compat_prompt("anthropic", {"route_through_router": True}, cfg))
        self.assertTrue(ciel_runtime.should_append_compat_prompt("vllm", {}, cfg))
        self.assertIn("ciel-runtime router", ciel_runtime.ROUTED_COMPAT_PROMPT)
        self.assertNotIn("non-Anthropic model provider", ciel_runtime.ROUTED_COMPAT_PROMPT)

    def test_routed_anthropic_resolves_tool_model_aliases(self):
        pcfg = {"current_model": "claude-opus-4-8"}
        body = {
            "model": "ciel-runtime-anthropic-claude-opus-4-8",
            "tools": [
                {"name": "Bash", "description": "run", "input_schema": {"type": "object"}},
                {"type": "advisor_20260301", "name": "advisor", "model": "ciel-runtime-anthropic-claude-opus-4-8"},
            ],
        }

        out = ciel_runtime.resolve_tool_model_references("anthropic", pcfg, body)

        self.assertEqual("claude-opus-4-8", out["tools"][1]["model"])
        self.assertIs(body["tools"][0], out["tools"][0])
        self.assertEqual("ciel-runtime-anthropic-claude-opus-4-8", body["tools"][1]["model"])

    def test_provider_choice_toggles_anthropic_routing(self):
        cfg = {
            "current_provider": "anthropic",
            "providers": {
                "anthropic": {
                    "base_url": "https://api.anthropic.com",
                    "api_key": "",
                    "route_through_router": False,
                },
            },
        }
        saved: dict[str, object] = {}

        def fake_save_config(next_cfg):
            saved.clear()
            saved.update(next_cfg)

        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "save_config", side_effect=fake_save_config),
            mock.patch.object(ciel_runtime, "clear_model_cache"),
        ):
            lines = ciel_runtime.set_provider_choice_config(ciel_runtime.ANTHROPIC_ROUTED_PROVIDER_CHOICE)

        self.assertEqual("anthropic", saved["current_provider"])
        self.assertTrue(saved["providers"]["anthropic"]["route_through_router"])
        self.assertTrue(any("Claude Code OAuth/API auth headers" in line for line in lines))

    def test_plain_anthropic_provider_selection_resets_to_native(self):
        cfg = {
            "current_provider": "opencode",
            "providers": {
                "anthropic": {
                    "base_url": "https://api.anthropic.com",
                    "api_key": "",
                    "route_through_router": True,
                },
                "opencode": {},
            },
        }
        saved: dict[str, object] = {}

        def fake_save_config(next_cfg):
            saved.clear()
            saved.update(next_cfg)

        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "save_config", side_effect=fake_save_config),
            mock.patch.object(ciel_runtime, "clear_model_cache"),
        ):
            lines = ciel_runtime.set_provider_config("anthropic")

        self.assertEqual("anthropic", saved["current_provider"])
        self.assertFalse(saved["providers"]["anthropic"]["route_through_router"])
        self.assertIn("mode: anthropic-native", lines)


class NativeEnvContractTests(unittest.TestCase):
    def _cfg(self, **provider_overrides):
        base = {
            "current_provider": "anthropic",
            "providers": {
                "anthropic": {
                    "api_key": "",
                    "advisor_model": "deepseek-v4-pro",
                    "current_model": "claude-sonnet-4-7",
                    "max_output_tokens": 8192,
                    **provider_overrides,
                },
            },
        }
        return base

    def test_native_env_omits_ciel_runtime_specific_overrides(self):
        # With advisor/model/output-tokens all set in the saved provider
        # config, the native-mode env MUST NOT propagate any of them — the
        # user explicitly chose Claude Native to revert to Anthropic defaults.
        env = ciel_runtime.env_vars(self._cfg())
        self.assertEqual("anthropic", env.get("CIEL_RUNTIME_PROVIDER"))
        for forbidden in (
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_MODEL",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTS",
            "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTS",
            "ANTHROPIC_DEFAULT_SONNET_MODEL",
            "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTS",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_SUBAGENT_MODEL",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
            "CLAUDE_CODE_DISABLE_TERMINAL_TITLE",
            "CLAUDE_CODE_ATTRIBUTION_HEADER",
            "CIEL_RUNTIME_ADVISOR_MODEL",
            "CIEL_RUNTIME_MODEL_ALIAS",
        ):
            self.assertNotIn(forbidden, env, msg=f"native env must not set {forbidden}")

    def test_native_env_includes_api_key_only_when_stored(self):
        # No stored key → ANTHROPIC_API_KEY absent (Claude Code's OAuth wins).
        env = ciel_runtime.env_vars(self._cfg(api_key=""))
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        # Stored key → ANTHROPIC_API_KEY exposed.
        env_with_key = ciel_runtime.env_vars(self._cfg(api_key="sk-ant-real"))
        self.assertEqual("sk-ant-real", env_with_key.get("ANTHROPIC_API_KEY"))

    def test_routed_anthropic_env_uses_ciel_runtime_router(self):
        env = ciel_runtime.env_vars(self._cfg(api_key="sk-ant-real", route_through_router=True))

        self.assertEqual("anthropic", env.get("CIEL_RUNTIME_PROVIDER"))
        self.assertEqual(ciel_runtime.ROUTER_BASE, env.get("ANTHROPIC_BASE_URL"))
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertEqual("ciel-runtime-anthropic-claude-sonnet-4-7", env.get("ANTHROPIC_MODEL"))
        self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_routed_anthropic_reports_router_mode(self):
        pcfg = self._cfg(route_through_router=True)["providers"]["anthropic"]

        self.assertEqual("anthropic-routed", ciel_runtime.provider_mode_label("anthropic", pcfg))
        self.assertFalse(ciel_runtime.direct_native_anthropic_enabled("anthropic", pcfg))

    def test_server_side_web_tools_are_only_disallowed_outside_claude_modes(self):
        native_pcfg = self._cfg()["providers"]["anthropic"]
        routed_pcfg = self._cfg(route_through_router=True)["providers"]["anthropic"]
        self.assertFalse(
            ciel_runtime.should_disallow_claude_server_side_web_tools("anthropic", native_pcfg, True)
        )
        self.assertFalse(
            ciel_runtime.should_disallow_claude_server_side_web_tools("anthropic", routed_pcfg, False)
        )
        self.assertTrue(
            ciel_runtime.should_disallow_claude_server_side_web_tools(
                "ollama", {"route_through_router": False}, False
            )
        )

    def test_routed_anthropic_without_api_key_can_launch_for_oauth_header_pass_through(self):
        with mock.patch.object(ciel_runtime, "base_url_status_line", return_value="Base URL: model list reachable"):
            errors = ciel_runtime.launch_readiness_errors(self._cfg(route_through_router=True, api_key=""))

        self.assertEqual([], errors)

    def test_routed_anthropic_provider_headers_use_inbound_oauth_when_no_api_key(self):
        headers = ciel_runtime.provider_headers(
            "anthropic",
            {"api_key": ""},
            {"authorization": "Bearer oauth-token", "anthropic-beta": "tools-2026"},
        )

        self.assertEqual("Bearer oauth-token", headers["authorization"])
        self.assertEqual("tools-2026", headers["anthropic-beta"])
        self.assertNotIn("x-api-key", headers)

    def test_routed_anthropic_provider_headers_prefer_configured_api_key(self):
        headers = ciel_runtime.provider_headers(
            "anthropic",
            {"api_key": "sk-ant-real"},
            {"authorization": "Bearer oauth-token"},
        )

        self.assertEqual("sk-ant-real", headers["x-api-key"])
        self.assertNotIn("authorization", headers)

    def test_routed_anthropic_advisor_request_uses_messages_api(self):
        pcfg = {
            "base_url": "https://api.anthropic.com",
            "api_key": "",
            "advisor_model": "claude-opus-4-8",
            "route_through_router": True,
            "max_output_tokens": 4096,
        }
        body = {
            "system": "You are in Claude Code.",
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "CIEL_RUNTIME_ADVISOR_CALL\nFocus: plan"}]},
                {"role": "system", "content": [{"type": "text", "text": "Runtime state from Claude Code."}]},
                {"role": "assistant", "content": [{"type": "text", "text": "I will inspect files."}]},
            ],
            "tools": [{"name": "Bash"}],
            "tool_choice": {"type": "auto"},
        }

        self.assertTrue(ciel_runtime.advisor_provider_supported("anthropic"))
        self.assertEqual("https://api.anthropic.com/v1/messages", ciel_runtime.advisor_endpoint("anthropic", pcfg))
        req = ciel_runtime.advisor_request("anthropic", "claude-opus-4-8", body, pcfg)

        self.assertEqual("claude-opus-4-8", req["model"])
        self.assertEqual(False, req["stream"])
        self.assertNotIn("tools", req)
        self.assertNotIn("tool_choice", req)
        self.assertEqual(["user", "assistant", "user"], [message["role"] for message in req["messages"]])
        self.assertIn("Advisor focus", ciel_runtime.anthropic_content_to_text(req["messages"][-1]["content"]))
        # The session's own system text must stay first and verbatim — Anthropic
        # rejects OAuth requests whose first system block is not the Claude Code
        # identity (429 rate_limit_error).
        self.assertEqual("You are in Claude Code.", req["system"][0]["text"])
        self.assertEqual(ciel_runtime.ADVISOR_REVIEW_PROMPT, req["system"][1]["text"])
        self.assertIn("Runtime state from Claude Code.", ciel_runtime.anthropic_content_to_text(req["system"]))

    def test_routed_anthropic_advisor_call_forwards_oauth_headers(self):
        pcfg = {
            "base_url": "https://api.anthropic.com",
            "api_key": "",
            "advisor_model": "claude-opus-4-8",
            "route_through_router": True,
            "max_output_tokens": 4096,
        }
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "CIEL_RUNTIME_ADVISOR_CALL"}]}],
        }

        with mock.patch.object(
            ciel_runtime,
            "post_json_with_rate_retry",
            return_value={"content": [{"type": "text", "text": "advisor ok"}]},
        ) as post:
            text = ciel_runtime.call_advisor_text(
                "anthropic",
                pcfg,
                body,
                inbound_headers={"authorization": "Bearer oauth-token", "anthropic-beta": "tools-2026"},
            )

        self.assertEqual("advisor ok", text)
        args = post.call_args.args
        self.assertEqual("https://api.anthropic.com/v1/messages", args[0])
        self.assertEqual("Bearer oauth-token", args[2]["authorization"])
        self.assertEqual("tools-2026", args[2]["anthropic-beta"])

    def test_interactive_advisor_call_can_skip_rate_limit_wait_and_retry(self):
        pcfg = {
            "base_url": "https://api.anthropic.com",
            "api_key": "",
            "advisor_model": "claude-opus-4-8",
            "route_through_router": True,
            "max_output_tokens": 4096,
        }
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "CIEL_RUNTIME_ADVISOR_CALL"}]}],
        }

        with (
            mock.patch.object(ciel_runtime, "apply_router_rate_limit") as apply_rate_limit,
            mock.patch.object(ciel_runtime, "post_json_with_rate_retry", side_effect=RuntimeError("rate limited")) as post,
        ):
            with self.assertRaises(RuntimeError):
                ciel_runtime.call_advisor_text(
                    "anthropic",
                    pcfg,
                    body,
                    inbound_headers={"authorization": "Bearer oauth-token"},
                    allow_rate_limit_wait=False,
                    retry_rate_limits=False,
                    raise_errors=True,
                )

        apply_rate_limit.assert_not_called()
        self.assertFalse(post.call_args.kwargs["retry_rate_limits"])

    def test_post_json_can_fail_fast_on_429_without_retry_sleep(self):
        error = urllib.error.HTTPError(
            "https://api.anthropic.com/v1/messages",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(b'{"error":{"message":"rate limit"}}'),
        )

        with (
            mock.patch("urllib.request.urlopen", side_effect=error) as urlopen,
            mock.patch.object(ciel_runtime, "learn_router_rate_limit_headers"),
            mock.patch.object(ciel_runtime, "write_router_activity"),
            mock.patch.object(ciel_runtime, "router_log"),
            mock.patch("time.sleep") as sleep,
        ):
            with self.assertRaises(RuntimeError):
                ciel_runtime.post_json_with_rate_retry(
                    "https://api.anthropic.com/v1/messages",
                    {"model": "claude-opus-4-8", "messages": []},
                    {},
                    30.0,
                    "anthropic",
                    {"gateway_retries": 2},
                    "claude-opus-4-8",
                    retry_rate_limits=False,
                )

        self.assertEqual(1, urlopen.call_count)
        sleep.assert_not_called()

    def test_direct_native_anthropic_does_not_require_api_key_or_base_url(self):
        errors = ciel_runtime.launch_readiness_errors(self._cfg(base_url="", api_key="", route_through_router=False))

        self.assertEqual([], errors)


class NativeSlashCommandContractTests(unittest.TestCase):
    def test_native_mode_removes_ciel_runtime_slash_commands(self):
        with tempfile.TemporaryDirectory() as td:
            commands_dir = Path(td) / "commands"
            commands_dir.mkdir()
            advisor = commands_dir / "advisor.md"
            router_debug = commands_dir / "router-debug.md"
            ciel_version = commands_dir / "ciel-version.md"
            channel_clear = commands_dir / "channel-clear.md"
            api_key = commands_dir / "api-key.md"
            advisor.write_text(ciel_runtime.ADVISOR_SLASH_COMMAND, encoding="utf-8")
            router_debug.write_text(ciel_runtime.ROUTER_DEBUG_SLASH_COMMAND, encoding="utf-8")
            ciel_version.write_text(ciel_runtime.VERSION_SLASH_COMMAND, encoding="utf-8")
            channel_clear.write_text(ciel_runtime.CHANNEL_CLEAR_SLASH_COMMAND, encoding="utf-8")
            api_key.write_text(ciel_runtime.API_KEYS_SLASH_COMMAND, encoding="utf-8")

            with mock.patch.object(ciel_runtime, "CLAUDE_COMMANDS_DIR", commands_dir):
                ciel_runtime.disable_ciel_runtime_slash_commands_for_native()

            self.assertFalse(advisor.exists())
            self.assertFalse(router_debug.exists())
            self.assertFalse(ciel_version.exists())
            self.assertFalse(channel_clear.exists())
            self.assertFalse(api_key.exists())

    def test_non_native_install_restores_router_backed_slash_commands(self):
        with tempfile.TemporaryDirectory() as td:
            commands_dir = Path(td) / "commands"
            commands_dir.mkdir()
            advisor = commands_dir / "advisor.md"
            advisor.write_text(ciel_runtime.ADVISOR_SLASH_COMMAND, encoding="utf-8")

            with mock.patch.object(ciel_runtime, "CLAUDE_COMMANDS_DIR", commands_dir):
                ciel_runtime.disable_ciel_runtime_slash_commands_for_native()
                self.assertFalse(advisor.exists())
                ciel_runtime.install_ciel_runtime_slash_commands()

            self.assertIn("CIEL_RUNTIME_ADVISOR_CALL", advisor.read_text(encoding="utf-8"))

    def test_native_mode_preserves_user_custom_advisor_command(self):
        custom = "---\ndescription: My advisor\n---\n\nCustom user command\n"
        with tempfile.TemporaryDirectory() as td:
            commands_dir = Path(td) / "commands"
            commands_dir.mkdir()
            advisor = commands_dir / "advisor.md"
            advisor.write_text(custom, encoding="utf-8")

            with mock.patch.object(ciel_runtime, "CLAUDE_COMMANDS_DIR", commands_dir):
                ciel_runtime.disable_ciel_runtime_slash_commands_for_native()

            self.assertEqual(custom, advisor.read_text(encoding="utf-8"))

    def test_non_native_install_preserves_user_custom_advisor_command(self):
        custom = "---\ndescription: My advisor\n---\n\nCustom user command\n"
        with tempfile.TemporaryDirectory() as td:
            commands_dir = Path(td) / "commands"
            commands_dir.mkdir()
            advisor = commands_dir / "advisor.md"
            advisor.write_text(custom, encoding="utf-8")

            with mock.patch.object(ciel_runtime, "CLAUDE_COMMANDS_DIR", commands_dir):
                ciel_runtime.install_ciel_runtime_slash_commands()

            self.assertEqual(custom, advisor.read_text(encoding="utf-8"))


class NativeSessionBoundaryTests(unittest.TestCase):
    def test_native_launch_forks_after_previous_router_mode_in_same_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td)
            state_path = config_dir / "launch-state.json"
            cwd = str(config_dir / "project")
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", config_dir),
                mock.patch.object(ciel_runtime, "LAUNCH_STATE_PATH", state_path),
            ):
                ciel_runtime.record_launch_state_for_cwd(cwd, "opencode-go", "router:opencode-go", "deepseek-v4-flash")
                should_fork, previous_mode = ciel_runtime.should_fork_native_session_after_mode_switch(
                    "anthropic",
                    {"route_through_router": False},
                    True,
                    [],
                    cwd,
                )

        self.assertTrue(should_fork)
        self.assertEqual("router:opencode-go", previous_mode)

    def test_native_launch_respects_explicit_resume_options(self):
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td)
            state_path = config_dir / "launch-state.json"
            cwd = str(config_dir / "project")
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", config_dir),
                mock.patch.object(ciel_runtime, "LAUNCH_STATE_PATH", state_path),
            ):
                ciel_runtime.record_launch_state_for_cwd(cwd, "ollama-cloud", "router:ollama-cloud", "deepseek-v4-flash")
                should_fork, reason = ciel_runtime.should_fork_native_session_after_mode_switch(
                    "anthropic",
                    {"route_through_router": False},
                    True,
                    ["--continue"],
                    cwd,
                )

        self.assertFalse(should_fork)
        self.assertEqual("explicit_session_control", reason)

    def test_native_launch_does_not_fork_after_previous_native_mode(self):
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td)
            state_path = config_dir / "launch-state.json"
            cwd = str(config_dir / "project")
            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", config_dir),
                mock.patch.object(ciel_runtime, "LAUNCH_STATE_PATH", state_path),
            ):
                ciel_runtime.record_launch_state_for_cwd(cwd, "anthropic", "anthropic-native", "claude-opus-4-8")
                should_fork, previous_mode = ciel_runtime.should_fork_native_session_after_mode_switch(
                    "anthropic",
                    {"route_through_router": False},
                    True,
                    [],
                    cwd,
                )

        self.assertFalse(should_fork)
        self.assertEqual("anthropic-native", previous_mode)


class NativeModelListTests(unittest.TestCase):
    def test_public_docs_parser_extracts_current_claude_models_without_footnotes(self):
        html = """
        Claude Fable 5 (`claude-fable-5`) is Anthropic's most capable widely released model.
        Claude Mythos 5 (`claude-mythos-5`) joins the invitation-only Claude Mythos Preview (`claude-mythos-preview`).
        Claude API ID
        <span>claude-opus-4-8</span><span>claude-sonnet-4-6</span>
        <span>claude-haiku-4-5-20251001</span>
        Claude API alias
        <span>claude-haiku-4-5</span>
        AWS Bedrock ID <span>anthropic.claude-opus-4-8</span>
        Vertex AI ID <span>claude-haiku-4-5@20251001</span>
        footnote artifact <span>claude-opus-4-1-2</span>
        """

        ids = ciel_runtime.anthropic_model_ids_from_docs_text(html)

        self.assertEqual(
            [
                "claude-fable-5",
                "claude-mythos-5",
                "claude-mythos-preview",
                "claude-opus-4-8",
                "claude-sonnet-4-6",
                "claude-haiku-4-5-20251001",
                "claude-haiku-4-5",
            ],
            ids,
        )

    def test_public_docs_parser_rejects_embedded_current_model_words(self):
        html = """
        Not a model ID: claude-fable-5-and-claude-mythos-5
        Not a model ID: anthropic.claude-fable-5
        Real model ID: `claude-fable-5`
        """

        ids = ciel_runtime.anthropic_model_ids_from_docs_text(html)

        self.assertEqual(["claude-fable-5"], ids)

    def test_public_docs_fetch_filters_limited_and_legacy_models_from_default_picker(self):
        ids = ciel_runtime.filter_anthropic_default_model_ids(
            [
                "claude-fable-5",
                "claude-mythos-5",
                "claude-mythos-preview",
                "claude-opus-4-8",
                "claude-sonnet-4-6",
                "claude-haiku-4-5-20251001",
                "claude-haiku-4-5",
                "claude-opus-4-5-20251101",
                "claude-sonnet-4-20250514",
            ]
        )

        self.assertEqual(
            [
                "claude-fable-5",
                "claude-opus-4-8",
                "claude-sonnet-4-6",
                "claude-haiku-4-5-20251001",
                "claude-haiku-4-5",
            ],
            ids,
        )

    def test_public_docs_parser_includes_latest_aliases(self):
        html = """
        <code>claude-3-7-sonnet-latest</code>
        <code>claude-3-5-haiku-latest</code>
        <code>claude-sonnet-4-6-latest</code>
        """

        ids = ciel_runtime.anthropic_model_ids_from_docs_text(html)

        self.assertEqual(
            ["claude-3-7-sonnet-latest", "claude-3-5-haiku-latest", "claude-sonnet-4-6-latest"],
            ids,
        )

    def test_native_refresh_bypasses_model_cache_and_uses_public_docs_without_api_key(self):
        pcfg = {"base_url": "https://api.anthropic.com", "api_key": "", "current_model": "claude-sonnet-4-6"}

        with (
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=["claude-old-cache"]),
            mock.patch.object(ciel_runtime, "http_json", side_effect=RuntimeError("missing api key")),
            mock.patch.object(ciel_runtime, "fetch_anthropic_public_model_ids", return_value=["claude-opus-4-8", "claude-sonnet-4-6"]) as docs,
            mock.patch.object(ciel_runtime, "write_model_list_cache") as write,
        ):
            self.assertEqual(["claude-old-cache"], ciel_runtime.upstream_model_ids("anthropic", pcfg))
            refreshed = ciel_runtime.upstream_model_ids("anthropic", pcfg, force_refresh=True)

        self.assertEqual(["claude-opus-4-8", "claude-sonnet-4-6"], refreshed)
        docs.assert_called_once()
        write.assert_called_once_with("anthropic", pcfg, ["claude-opus-4-8", "claude-sonnet-4-6"])

    def test_anthropic_refresh_prefers_api_key_model_list(self):
        pcfg = {"base_url": "https://api.anthropic.com", "api_key": "sk-ant-real", "current_model": "claude-sonnet-4-6"}

        with (
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=None),
            mock.patch.object(ciel_runtime, "fetch_anthropic_public_model_ids", return_value=["claude-opus-4-8", "claude-sonnet-4-6"]) as docs,
            mock.patch.object(ciel_runtime, "http_json", return_value={"data": [{"id": "claude-account-only"}]}) as http_json,
            mock.patch.object(ciel_runtime, "write_model_list_cache"),
        ):
            refreshed = ciel_runtime.upstream_model_ids("anthropic", pcfg, force_refresh=True)

        self.assertEqual(["claude-sonnet-4-6", "claude-account-only"], refreshed)
        http_json.assert_called_once()
        docs.assert_not_called()

    def test_anthropic_routed_model_discovery_uses_inbound_oauth_headers(self):
        pcfg = {
            "base_url": "https://api.anthropic.com",
            "api_key": "",
            "current_model": "claude-sonnet-4-6",
            "route_through_router": True,
        }

        with (
            mock.patch.object(ciel_runtime, "http_json", return_value={"data": [{"id": "claude-oauth-only"}]}) as http_json,
            mock.patch.object(ciel_runtime, "fetch_anthropic_public_model_ids", return_value=["claude-opus-4-8"]) as docs,
        ):
            models = ciel_runtime.list_model_objects_for_request(
                "anthropic",
                pcfg,
                {"authorization": "Bearer oauth-token", "anthropic-beta": "tools-2026"},
            )

        ids = [item["id"] for item in models]
        self.assertIn("ciel-runtime-anthropic-claude-oauth-only", ids)
        self.assertIn("ciel-runtime-anthropic-claude-sonnet-4-6", ids)
        http_json.assert_called_once()
        _, kwargs = http_json.call_args
        self.assertEqual("Bearer oauth-token", kwargs["headers"]["authorization"])
        self.assertEqual("tools-2026", kwargs["headers"]["anthropic-beta"])
        docs.assert_not_called()

    def test_native_model_registry_persists_provider_model_list(self):
        pcfg = {"base_url": "https://api.anthropic.com", "api_key": "", "current_model": "claude-sonnet-4-6"}

        with tempfile.TemporaryDirectory() as td:
            cache_path = Path(td) / "model-list-cache.json"
            registry_path = Path(td) / "model-registry.json"
            with (
                mock.patch.object(ciel_runtime, "MODEL_LIST_CACHE_PATH", cache_path),
                mock.patch.object(ciel_runtime, "MODEL_REGISTRY_PATH", registry_path),
            ):
                ciel_runtime.write_model_registry(
                    "anthropic",
                    pcfg,
                    ["claude-fable-5", "claude-mythos-preview", "claude-opus-4-8", "claude-haiku-4-5"],
                    "anthropic-docs",
                )
                cached = ciel_runtime.read_model_list_cache("anthropic", pcfg)
                registry = ciel_runtime.read_model_registry("anthropic", pcfg)

        self.assertEqual(["claude-fable-5", "claude-mythos-preview", "claude-opus-4-8", "claude-haiku-4-5"], cached)
        self.assertIsNotNone(registry)
        assert registry is not None
        self.assertEqual("anthropic-docs", registry["source"])
        recommendations = registry["recommendations"]
        self.assertEqual("balanced", recommendations["claude-fable-5"]["recommended_preset"])
        self.assertEqual(1048576, recommendations["claude-fable-5"]["limits"]["context_window"])
        self.assertEqual(128000, recommendations["claude-fable-5"]["limits"]["max_output_tokens"])
        self.assertEqual("high", recommendations["claude-fable-5"]["runtime"]["claude_code_default_effort"])
        self.assertEqual("xhigh", recommendations["claude-fable-5"]["runtime"]["claude_code_max_effort"])
        self.assertEqual("adaptive", recommendations["claude-fable-5"]["runtime"]["thinking_mode"])
        self.assertTrue(recommendations["claude-fable-5"]["runtime"]["adaptive_thinking_always_on"])
        self.assertIn("temperature", recommendations["claude-fable-5"]["runtime"]["unsupported_sampling_parameters"])
        self.assertEqual("mythos", recommendations["claude-mythos-preview"]["model_family"])
        self.assertEqual(1048576, recommendations["claude-mythos-preview"]["limits"]["context_window"])
        self.assertEqual(128000, recommendations["claude-mythos-preview"]["limits"]["max_output_tokens"])
        self.assertEqual("balanced", recommendations["claude-opus-4-8"]["recommended_preset"])
        self.assertEqual(4096, recommendations["claude-opus-4-8"]["parameters"]["max_output_tokens"])
        self.assertEqual(1048576, recommendations["claude-opus-4-8"]["limits"]["context_window"])
        self.assertEqual(128000, recommendations["claude-opus-4-8"]["limits"]["max_output_tokens"])
        self.assertEqual("high", recommendations["claude-opus-4-8"]["runtime"]["claude_code_default_effort"])
        self.assertEqual("xhigh", recommendations["claude-opus-4-8"]["runtime"]["claude_code_max_effort"])
        self.assertEqual("adaptive", recommendations["claude-opus-4-8"]["runtime"]["thinking_mode"])
        self.assertTrue(recommendations["claude-opus-4-8"]["runtime"]["fast_mode"]["available"])
        self.assertIn("temperature", recommendations["claude-opus-4-8"]["runtime"]["unsupported_sampling_parameters"])
        self.assertEqual("fast", recommendations["claude-haiku-4-5"]["recommended_preset"])
        self.assertEqual(2048, recommendations["claude-haiku-4-5"]["parameters"]["max_output_tokens"])
        self.assertEqual(200000, recommendations["claude-haiku-4-5"]["limits"]["context_window"])

    def test_anthropic_latest_models_strip_unsupported_sampling_request_options(self):
        body = {
            "model": "claude-fable-5",
            "max_tokens": 4096,
            "temperature": 0.2,
            "top_p": 0.9,
            "top_k": 40,
            "messages": [],
        }

        out = ciel_runtime.normalize_anthropic_model_request_options("anthropic", {}, body, "claude-fable-5")

        self.assertNotIn("temperature", out)
        self.assertNotIn("top_p", out)
        self.assertNotIn("top_k", out)
        self.assertEqual(4096, out["max_tokens"])
        self.assertIn("temperature", body)

    def test_non_anthropic_request_options_are_not_stripped_by_anthropic_model_hints(self):
        body = {
            "model": "claude-fable-5",
            "temperature": 0.2,
            "top_p": 0.9,
            "top_k": 40,
            "messages": [],
        }

        out = ciel_runtime.normalize_anthropic_model_request_options("vllm", {}, body, "claude-fable-5")

        self.assertEqual(body, out)

    def test_anthropic_docs_registry_survives_api_key_state_changes(self):
        pcfg_with_key = {"base_url": "https://api.anthropic.com", "api_key": "sk-ant-real", "current_model": "claude-sonnet-4-6"}
        pcfg_without_key = {"base_url": "https://api.anthropic.com", "api_key": "", "current_model": "claude-sonnet-4-6"}

        with tempfile.TemporaryDirectory() as td:
            cache_path = Path(td) / "model-list-cache.json"
            registry_path = Path(td) / "model-registry.json"
            with (
                mock.patch.object(ciel_runtime, "MODEL_LIST_CACHE_PATH", cache_path),
                mock.patch.object(ciel_runtime, "MODEL_REGISTRY_PATH", registry_path),
            ):
                ciel_runtime.write_model_registry(
                    "anthropic",
                    pcfg_with_key,
                    ["claude-opus-4-8", "claude-sonnet-4-6"],
                    "anthropic-docs",
                )
                cached = ciel_runtime.read_model_list_cache("anthropic", pcfg_without_key)

        self.assertEqual(["claude-opus-4-8", "claude-sonnet-4-6"], cached)

    def test_native_model_panel_force_refresh_shows_latest_public_models(self):
        pcfg = {"base_url": "https://api.anthropic.com", "api_key": "", "current_model": "claude-sonnet-4-6"}

        with (
            mock.patch.object(ciel_runtime, "read_model_list_cache", return_value=None),
            mock.patch.object(ciel_runtime, "http_json", side_effect=RuntimeError("missing api key")),
            mock.patch.object(ciel_runtime, "fetch_anthropic_public_model_ids", return_value=["claude-opus-4-8", "claude-sonnet-4-6"]),
            mock.patch.object(ciel_runtime, "write_model_list_cache"),
        ):
            rows, values = ciel_runtime.model_panel_rows("anthropic", pcfg, fetch=True, force_refresh=True)

        self.assertIn("claude-opus-4-8", values)
        self.assertTrue(any("claude-opus-4-8" in row for row in rows))


class StopRouterGuaranteeTests(unittest.TestCase):
    def test_returns_false_when_router_already_down(self):
        with (
            mock.patch.object(ciel_runtime, "router_up", return_value=False),
            mock.patch.object(ciel_runtime, "stop_router_processes") as stop,
            mock.patch.object(ciel_runtime, "router_log"),
        ):
            result = ciel_runtime.stop_router_with_guarantee("test", max_wait_seconds=0.5)
        self.assertFalse(result)
        stop.assert_not_called()

    def test_returns_true_when_kill_brings_router_down(self):
        health = {"config_dir": str(ciel_runtime.CONFIG_DIR), "pid": 2468}
        states = iter([health, None])  # alive at first, dead after stop
        def fake_health():
            try:
                return next(states)
            except StopIteration:
                return None

        with (
            mock.patch.object(ciel_runtime, "router_health", side_effect=fake_health),
            mock.patch.object(ciel_runtime, "stop_router_processes") as stop,
            mock.patch.object(ciel_runtime, "router_log"),
        ):
            result = ciel_runtime.stop_router_with_guarantee("test", max_wait_seconds=1.0)
        self.assertTrue(result)
        stop.assert_called_once()

    def test_raises_when_router_stays_up_past_deadline(self):
        # router_health always returns this config's router -> guarantee should give up and raise.
        health = {"config_dir": str(ciel_runtime.CONFIG_DIR), "pid": 2468}
        with (
            mock.patch.object(ciel_runtime, "router_health", return_value=health),
            mock.patch.object(ciel_runtime, "stop_router_processes"),
            mock.patch.object(ciel_runtime, "router_log"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                ciel_runtime.stop_router_with_guarantee("native_anthropic_launch", max_wait_seconds=0.3)
        self.assertIn("native_anthropic_launch", str(ctx.exception))
        self.assertIn("router", str(ctx.exception).lower())

    def test_stop_router_processes_skips_foreign_config_router(self):
        with (
            mock.patch.object(ciel_runtime, "terminate_pid_file", return_value=False) as pid_file,
            mock.patch.object(ciel_runtime, "router_health", return_value={"config_dir": "/other/config", "pid": 4321}),
            mock.patch.object(ciel_runtime, "terminate_router_health_pid") as health_pid,
            mock.patch.object(ciel_runtime, "router_log"),
        ):
            result = ciel_runtime.stop_router_processes(quiet=True)

        self.assertFalse(result)
        pid_file.assert_called_once()
        health_pid.assert_not_called()

    def test_posix_pids_on_port_parses_ss_listener_pid(self):
        class FakeProcess:
            stdout = 'LISTEN 0 4096 127.0.0.1:8799 0.0.0.0:* users:(("python",pid=4321,fd=4))'
            stderr = ""

        def fake_which(name):
            return f"/usr/bin/{name}" if name == "ss" else None

        with (
            mock.patch.object(ciel_runtime.os, "name", "posix"),
            mock.patch.object(ciel_runtime, "linux_procfs_pids_on_port", return_value=[]),
            mock.patch.object(ciel_runtime.shutil, "which", side_effect=fake_which),
            mock.patch.object(ciel_runtime.subprocess, "run", return_value=FakeProcess()),
            mock.patch.object(ciel_runtime.os, "getpid", return_value=100),
            mock.patch.object(ciel_runtime.os, "getppid", return_value=101),
        ):
            self.assertEqual([4321], ciel_runtime.posix_pids_on_port(8799))

    def test_posix_pids_on_port_uses_procfs_fallback_without_tools(self):
        with (
            mock.patch.object(ciel_runtime.os, "name", "posix"),
            mock.patch.object(ciel_runtime, "linux_procfs_pids_on_port", return_value=[5555]),
            mock.patch.object(ciel_runtime.shutil, "which", return_value=None),
            mock.patch.object(ciel_runtime.os, "getpid", return_value=100),
            mock.patch.object(ciel_runtime.os, "getppid", return_value=101),
        ):
            self.assertEqual([5555], ciel_runtime.posix_pids_on_port(8799))

    def test_terminate_router_health_pid_uses_health_pid(self):
        with mock.patch.object(ciel_runtime, "terminate_pid", return_value=True) as terminate:
            result = ciel_runtime.terminate_router_health_pid({"pid": 2468, "config_dir": str(ciel_runtime.CONFIG_DIR)}, quiet=True)

        self.assertTrue(result)
        terminate.assert_called_once_with(2468, "ciel-runtime router", quiet=True)

    def test_ensure_router_port_available_for_spawn_clears_empty_port(self):
        with (
            mock.patch.object(ciel_runtime, "terminate_router_health_pid", return_value=False),
            mock.patch.object(ciel_runtime, "stop_router_processes", return_value=False) as stop,
            mock.patch.object(ciel_runtime, "router_health", return_value=None),
            mock.patch.object(ciel_runtime, "router_port_listener_pids", return_value=[]),
            mock.patch.object(ciel_runtime, "router_log"),
        ):
            ciel_runtime.ensure_router_port_available_for_spawn("test", None, max_wait_seconds=0.2)

        stop.assert_called()

    def test_ensure_router_port_available_for_spawn_reports_remaining_pids(self):
        with (
            mock.patch.object(ciel_runtime, "terminate_router_health_pid", return_value=False),
            mock.patch.object(ciel_runtime, "stop_router_processes", return_value=False),
            mock.patch.object(ciel_runtime, "router_health", return_value={"version": "old", "source_fingerprint": "abc", "pid": 777}),
            mock.patch.object(ciel_runtime, "router_port_listener_pids", return_value=[777]),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                ciel_runtime.ensure_router_port_available_for_spawn("test", {"pid": 777}, max_wait_seconds=0.2)

        self.assertIn("listener_pids=[777]", str(ctx.exception))
        self.assertIn("version=old", str(ctx.exception))

    def test_ensure_router_port_available_refuses_foreign_config(self):
        health = {"version": "same", "source_fingerprint": "abc", "pid": 777, "config_dir": "/other/config"}
        with self.assertRaises(RuntimeError) as ctx:
            ciel_runtime.ensure_router_port_available_for_spawn("test", health, max_wait_seconds=0.2)

        self.assertIn("another ciel-runtime config", str(ctx.exception))
        self.assertIn("CIEL_RUNTIME_ROUTER_PORT", str(ctx.exception))

    def test_start_router_replaces_matching_router_by_default(self):
        health = {
            "version": ciel_runtime.VERSION,
            "source_fingerprint": ciel_runtime.SOURCE_FINGERPRINT,
            "pid": 2468,
            "user": ciel_runtime.getpass.getuser(),
            "config_dir": str(ciel_runtime.CONFIG_DIR),
        }
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "router.log"
            with (
                mock.patch.dict(os.environ, {"CIEL_RUNTIME_REUSE_ROUTER": "0"}, clear=False),
                mock.patch.object(ciel_runtime, "LOG_PATH", log_path),
                mock.patch.object(ciel_runtime, "router_health", return_value=health),
                mock.patch.object(ciel_runtime, "ensure_router_port_available_for_spawn") as ensure,
                mock.patch.object(ciel_runtime, "router_up", return_value=True),
                mock.patch.object(ciel_runtime.subprocess, "Popen") as popen,
                mock.patch.object(ciel_runtime, "router_log"),
            ):
                result = ciel_runtime.start_router_if_needed()

        self.assertTrue(result)
        ensure.assert_called_once_with("prelaunch_replace", health)
        popen.assert_called_once()
        popen_env = popen.call_args.kwargs["env"]
        self.assertEqual("1", popen_env["CIEL_RUNTIME_MANAGED_ROUTER"])
        self.assertEqual(str(os.getpid()), popen_env["CIEL_RUNTIME_ROUTER_OWNER_PID"])

    def test_start_router_reuses_matching_router_only_when_env_allows(self):
        health = {
            "version": ciel_runtime.VERSION,
            "source_fingerprint": ciel_runtime.SOURCE_FINGERPRINT,
            "pid": 2468,
            "user": ciel_runtime.getpass.getuser(),
            "config_dir": str(ciel_runtime.CONFIG_DIR),
        }
        with (
            mock.patch.dict(os.environ, {"CIEL_RUNTIME_REUSE_ROUTER": "1"}, clear=False),
            mock.patch.object(ciel_runtime, "router_health", return_value=health),
            mock.patch.object(ciel_runtime, "ensure_router_port_available_for_spawn") as ensure,
            mock.patch.object(ciel_runtime.subprocess, "Popen") as popen,
            mock.patch.object(ciel_runtime, "router_log"),
        ):
            result = ciel_runtime.start_router_if_needed()

        self.assertTrue(result)
        ensure.assert_not_called()
        popen.assert_not_called()

    def test_start_router_replaces_matching_router_with_active_clients_by_default(self):
        health = {
            "version": ciel_runtime.VERSION,
            "source_fingerprint": ciel_runtime.SOURCE_FINGERPRINT,
            "pid": 2468,
            "user": ciel_runtime.getpass.getuser(),
            "config_dir": str(ciel_runtime.CONFIG_DIR),
        }
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "router.log"
            with (
                mock.patch.dict(os.environ, {"CIEL_RUNTIME_REUSE_ROUTER": "0"}, clear=False),
                mock.patch.object(ciel_runtime, "LOG_PATH", log_path),
                mock.patch.object(ciel_runtime, "router_health", return_value=health),
                mock.patch.object(ciel_runtime, "active_router_client_pids", return_value=[999999]),
                mock.patch.object(ciel_runtime, "terminate_active_router_clients") as terminate_clients,
                mock.patch.object(ciel_runtime, "ensure_router_port_available_for_spawn") as ensure,
                mock.patch.object(ciel_runtime, "router_up", return_value=True),
                mock.patch.object(ciel_runtime.subprocess, "Popen") as popen,
                mock.patch.object(ciel_runtime, "router_log"),
            ):
                result = ciel_runtime.start_router_if_needed()

        self.assertTrue(result)
        terminate_clients.assert_called_once_with("prelaunch_active_clients", [999999], quiet=True)
        ensure.assert_called_once_with("prelaunch_active_clients", health)
        popen.assert_called_once()

    def test_start_router_keeps_matching_router_with_active_clients_when_replacement_disabled(self):
        health = {
            "version": ciel_runtime.VERSION,
            "source_fingerprint": ciel_runtime.SOURCE_FINGERPRINT,
            "pid": 2468,
            "user": ciel_runtime.getpass.getuser(),
            "config_dir": str(ciel_runtime.CONFIG_DIR),
        }
        with (
            mock.patch.dict(os.environ, {"CIEL_RUNTIME_REUSE_ROUTER": "0"}, clear=False),
            mock.patch.object(ciel_runtime, "router_health", return_value=health),
            mock.patch.object(ciel_runtime, "active_router_client_pids", return_value=[999999]),
            mock.patch.object(ciel_runtime, "ensure_router_port_available_for_spawn") as ensure,
            mock.patch.object(ciel_runtime.subprocess, "Popen") as popen,
            mock.patch.object(ciel_runtime, "router_log"),
        ):
            result = ciel_runtime.start_router_if_needed(replace_active_clients=False)

        self.assertTrue(result)
        ensure.assert_not_called()
        popen.assert_not_called()

    def test_start_router_replaces_version_mismatch_router_with_active_clients_by_default(self):
        health = {
            "version": "old",
            "source_fingerprint": "old-source",
            "pid": 2468,
            "user": ciel_runtime.getpass.getuser(),
            "config_dir": str(ciel_runtime.CONFIG_DIR),
        }
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "router.log"
            with (
                mock.patch.object(ciel_runtime, "LOG_PATH", log_path),
                mock.patch.object(ciel_runtime, "router_health", return_value=health),
                mock.patch.object(ciel_runtime, "active_router_client_pids", return_value=[999999]),
                mock.patch.object(ciel_runtime, "terminate_active_router_clients") as terminate_clients,
                mock.patch.object(ciel_runtime, "ensure_router_port_available_for_spawn") as ensure,
                mock.patch.object(ciel_runtime, "router_up", return_value=True),
                mock.patch.object(ciel_runtime.subprocess, "Popen") as popen,
                mock.patch.object(ciel_runtime, "router_log"),
            ):
                result = ciel_runtime.start_router_if_needed()

        self.assertTrue(result)
        terminate_clients.assert_called_once_with("version_mismatch_active_clients", [999999], quiet=True)
        ensure.assert_called_once_with("version_mismatch_active_clients", health)
        popen.assert_called_once()

    def test_terminate_active_router_clients_uses_ciel_runtime_wrapper_parent(self):
        with tempfile.TemporaryDirectory() as td:
            clients_dir = Path(td) / "router-clients"
            clients_dir.mkdir()
            (clients_dir / "222.json").write_text('{"pid": 222}', encoding="utf-8")
            with (
                mock.patch.object(ciel_runtime, "ROUTER_CLIENTS_DIR", clients_dir),
                mock.patch.object(ciel_runtime, "ciel_runtime_client_wrapper_parent_pids", return_value=[111]),
                mock.patch.object(ciel_runtime, "terminate_pid_tree", return_value=True) as terminate_tree,
                mock.patch.object(ciel_runtime, "router_log"),
            ):
                stopped = ciel_runtime.terminate_active_router_clients("test", [222], quiet=True)

        self.assertTrue(stopped)
        terminate_tree.assert_called_once_with(111, "previous ciel-runtime client", quiet=True)
        self.assertFalse((clients_dir / "222.json").exists())

    def test_start_router_refuses_foreign_config_router(self):
        health = {
            "version": ciel_runtime.VERSION,
            "source_fingerprint": ciel_runtime.SOURCE_FINGERPRINT,
            "pid": 2468,
            "user": ciel_runtime.getpass.getuser(),
            "config_dir": "/other/config",
        }
        with (
            mock.patch.object(ciel_runtime, "router_health", return_value=health),
            mock.patch.object(ciel_runtime, "active_router_client_pids", return_value=[]),
            mock.patch.object(ciel_runtime.subprocess, "Popen") as popen,
            mock.patch.object(ciel_runtime, "router_log"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                ciel_runtime.start_router_if_needed()

        self.assertIn("another ciel-runtime config", str(ctx.exception))
        popen.assert_not_called()


class RouterLifetimeTests(unittest.TestCase):
    def test_managed_router_watchdog_stops_when_owner_dead_and_no_clients(self):
        with (
            mock.patch.dict(os.environ, {"CIEL_RUNTIME_MANAGED_ROUTER": "1"}, clear=False),
            mock.patch.object(ciel_runtime, "active_router_client_pids", return_value=[]),
            mock.patch.object(ciel_runtime, "pid_is_running", return_value=False),
        ):
            reason = ciel_runtime.managed_router_stop_reason(started_at=100.0, owner_pid=2468, idle_seconds=90.0)

        self.assertEqual("owner_dead_no_clients", reason)

    def test_managed_router_watchdog_keeps_active_clients(self):
        with (
            mock.patch.dict(os.environ, {"CIEL_RUNTIME_MANAGED_ROUTER": "1"}, clear=False),
            mock.patch.object(ciel_runtime, "active_router_client_pids", return_value=[999999]),
            mock.patch.object(ciel_runtime, "pid_is_running", return_value=False),
        ):
            reason = ciel_runtime.managed_router_stop_reason(started_at=0.0, owner_pid=2468, idle_seconds=1.0)

        self.assertIsNone(reason)

    def test_managed_router_watchdog_stops_after_idle_grace(self):
        with (
            mock.patch.dict(os.environ, {"CIEL_RUNTIME_MANAGED_ROUTER": "1"}, clear=False),
            mock.patch.object(ciel_runtime, "active_router_client_pids", return_value=[]),
            mock.patch.object(ciel_runtime, "pid_is_running", return_value=True),
            mock.patch.object(ciel_runtime.time, "time", return_value=200.0),
        ):
            reason = ciel_runtime.managed_router_stop_reason(started_at=100.0, owner_pid=2468, idle_seconds=90.0)

        self.assertEqual("idle_no_clients", reason)

    def test_runner_exit_stops_router_when_no_other_clients_remain(self):
        with tempfile.TemporaryDirectory() as td:
            clients_dir = Path(td) / "router-clients"

            with (
                mock.patch.object(ciel_runtime, "ROUTER_CLIENTS_DIR", clients_dir),
                mock.patch.object(ciel_runtime, "pid_is_running", side_effect=lambda pid: pid == os.getpid()),
                mock.patch.object(ciel_runtime, "stop_router_with_guarantee", return_value=True) as stop,
                mock.patch.object(ciel_runtime, "router_log"),
            ):
                rc = ciel_runtime.run_with_router_lifetime(lambda: 7, manage_router=True)

            self.assertEqual(7, rc)
            stop.assert_called_once_with("claude_exit", quiet=True)
            self.assertEqual([], list(clients_dir.glob("*.json")))

    def test_runner_exit_keeps_router_when_another_client_is_alive(self):
        with tempfile.TemporaryDirectory() as td:
            clients_dir = Path(td) / "router-clients"
            clients_dir.mkdir()
            (clients_dir / "999999.json").write_text('{"pid": 999999}', encoding="utf-8")

            def fake_pid_is_running(pid):
                return pid in (os.getpid(), 999999)

            with (
                mock.patch.object(ciel_runtime, "ROUTER_CLIENTS_DIR", clients_dir),
                mock.patch.object(ciel_runtime, "pid_is_running", side_effect=fake_pid_is_running),
                mock.patch.object(ciel_runtime, "stop_router_with_guarantee") as stop,
                mock.patch.object(ciel_runtime, "router_log"),
            ):
                rc = ciel_runtime.run_with_router_lifetime(lambda: 0, manage_router=True)

            self.assertEqual(0, rc)
            stop.assert_not_called()
            self.assertTrue((clients_dir / "999999.json").exists())

    def test_runner_without_router_lifetime_does_not_touch_router(self):
        with (
            mock.patch.object(ciel_runtime, "register_router_client") as register,
            mock.patch.object(ciel_runtime, "stop_router_with_guarantee") as stop,
        ):
            rc = ciel_runtime.run_with_router_lifetime(lambda: 3, manage_router=False)

        self.assertEqual(3, rc)
        register.assert_not_called()
        stop.assert_not_called()

    def test_router_client_supervisor_ignores_transient_health_miss(self):
        with (
            mock.patch.object(ciel_runtime, "router_up", side_effect=[False, True]),
            mock.patch.object(ciel_runtime.time, "sleep"),
            mock.patch.object(ciel_runtime, "start_router_if_needed") as start,
            mock.patch.object(ciel_runtime, "router_log") as log,
        ):
            self.assertTrue(ciel_runtime.ensure_managed_router_running_for_client())

        start.assert_not_called()
        self.assertTrue(any("transient_health_miss" in call.args[1] for call in log.call_args_list))

    def test_router_client_supervisor_restarts_when_router_disappears(self):
        with (
            mock.patch.object(ciel_runtime, "router_up", return_value=False),
            mock.patch.object(ciel_runtime.time, "sleep"),
            mock.patch.object(ciel_runtime, "start_router_if_needed", return_value=True) as start,
            mock.patch.object(ciel_runtime, "router_log") as log,
        ):
            self.assertTrue(ciel_runtime.ensure_managed_router_running_for_client())

        start.assert_called_once()
        self.assertTrue(any("router_down_active_client" in call.args[1] for call in log.call_args_list))

    def test_runner_starts_and_stops_router_supervisor(self):
        supervisor_events = []

        def fake_supervisor(stop_event):
            supervisor_events.append(stop_event)
            return mock.Mock()

        with (
            mock.patch.object(ciel_runtime, "register_router_client", return_value=Path("client.json")) as register,
            mock.patch.object(ciel_runtime, "release_router_client") as release,
            mock.patch.object(ciel_runtime, "stop_router_if_no_active_clients", return_value=True),
            mock.patch.object(ciel_runtime, "start_router_client_supervisor", side_effect=fake_supervisor) as supervisor,
        ):
            rc = ciel_runtime.run_with_router_lifetime(lambda: 11, manage_router=True)

        self.assertEqual(11, rc)
        register.assert_called_once()
        supervisor.assert_called_once()
        release.assert_called_once_with(Path("client.json"))
        self.assertEqual(1, len(supervisor_events))
        self.assertTrue(supervisor_events[0].is_set())


class CleanupNativeRouterTests(unittest.TestCase):
    def test_native_bypasses_managed_services_toggle_but_only_idle_router(self):
        # Even when the user turned off managed_services_on_launch, native
        # mode still cleans this config's idle router. Active sessions are
        # protected by stop_router_if_no_active_clients().
        cfg = {"cleanup": {"managed_services_on_launch": False}}
        with (
            mock.patch.object(ciel_runtime, "native_anthropic_enabled", return_value=True),
            mock.patch.object(ciel_runtime, "provider_native_compat_enabled", return_value=False),
            mock.patch.object(ciel_runtime, "stop_router_if_no_active_clients", return_value=True) as stop_idle,
            mock.patch.object(ciel_runtime, "stop_ncp_proxy"),
        ):
            ciel_runtime.cleanup_managed_services_for_provider("anthropic", {}, cfg, quiet=True)
        stop_idle.assert_called_once()
        called_reason = stop_idle.call_args.args[0] if stop_idle.call_args.args else stop_idle.call_args.kwargs.get("reason", "")
        self.assertIn("native", called_reason.lower())

    def test_non_native_respects_managed_services_toggle(self):
        # When the gate is off, non-native providers should NOT kill the
        # router (preserves the existing opt-out behavior).
        cfg = {"cleanup": {"managed_services_on_launch": False}}
        with (
            mock.patch.object(ciel_runtime, "native_anthropic_enabled", return_value=False),
            mock.patch.object(ciel_runtime, "ollama_native_compat_enabled", return_value=True),
            mock.patch.object(ciel_runtime, "provider_native_compat_enabled", return_value=False),
            mock.patch.object(ciel_runtime, "stop_router_processes") as stop,
            mock.patch.object(ciel_runtime, "stop_router_with_guarantee") as guarantee,
            mock.patch.object(ciel_runtime, "stop_ncp_proxy"),
        ):
            ciel_runtime.cleanup_managed_services_for_provider("ollama", {}, cfg, quiet=True)
        stop.assert_not_called()
        guarantee.assert_not_called()

    def test_routed_anthropic_respects_managed_services_toggle(self):
        cfg = {"cleanup": {"managed_services_on_launch": False}}
        pcfg = {"route_through_router": True}
        with (
            mock.patch.object(ciel_runtime, "stop_router_processes") as stop,
            mock.patch.object(ciel_runtime, "stop_router_with_guarantee") as guarantee,
            mock.patch.object(ciel_runtime, "stop_ncp_proxy"),
        ):
            ciel_runtime.cleanup_managed_services_for_provider("anthropic", pcfg, cfg, quiet=True)

        stop.assert_not_called()
        guarantee.assert_not_called()


if __name__ == "__main__":
    unittest.main()
