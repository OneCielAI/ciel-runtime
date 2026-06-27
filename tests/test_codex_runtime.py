import copy
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ciel_runtime


class FakeSSEHandler:
    def __init__(self):
        self.wfile = io.BytesIO()
        self.status = None
        self.headers = []

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.headers.append((key.lower(), value))

    def end_headers(self):
        return None


class CodexRuntimeTests(unittest.TestCase):
    def test_provider_menu_exposes_native_and_routed_codex_choices(self):
        cfg = {
            "current_provider": "codex",
            "providers": {
                "codex": {
                    "base_url": "https://api.openai.com",
                    "api_key": "",
                    "route_through_router": False,
                },
            },
        }

        rows, values = ciel_runtime.provider_panel_rows(cfg)

        self.assertIn(ciel_runtime.CODEX_NATIVE_PROVIDER_CHOICE, values)
        self.assertIn(ciel_runtime.CODEX_ROUTED_PROVIDER_CHOICE, values)
        self.assertTrue(any("Codex Native" in row and row.startswith("*") for row in rows))
        self.assertTrue(any("Codex routed" in row and "native Codex auth" in row for row in rows))
        labels = [row[2:18].strip() for row in rows]
        self.assertEqual(sorted(labels, key=str.casefold), labels)

    def test_provider_command_lists_codex_choice_labels(self):
        cfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        cfg["current_provider"] = "anthropic"

        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            ciel_runtime.cmd_provider(type("Args", (), {"name": ""})())

        output = stdout.getvalue()
        self.assertIn("Codex Native", output)
        self.assertIn("codex-native", output)
        self.assertIn("Codex routed", output)
        self.assertIn("codex-routed", output)

    def test_main_menu_disables_opposite_runtime_for_codex_provider(self):
        cfg = {"language": "en"}
        codex = {"route_through_router": True, "base_url": "https://api.openai.com", "current_model": ""}
        anthropic = {"route_through_router": True, "base_url": "https://api.anthropic.com", "current_model": "claude"}

        codex_rows = ciel_runtime.main_menu_rows(cfg, "codex", codex, "en")
        self.assertIn("9. Launch Claude Code [disabled: Codex provider selected]", codex_rows)
        self.assertIn("10. Launch Codex", codex_rows)
        self.assertNotIn("10. Launch Codex [disabled", codex_rows)

        claude_rows = ciel_runtime.main_menu_rows(cfg, "anthropic", anthropic, "en")
        self.assertIn("9. Launch Claude Code", claude_rows)
        self.assertIn("10. Launch Codex [disabled: select Codex provider]", claude_rows)

    def test_provider_choice_toggles_codex_routing(self):
        cfg = {
            "current_provider": "anthropic",
            "providers": {
                "anthropic": {"route_through_router": False},
                "codex": {"base_url": "https://api.openai.com", "route_through_router": False},
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
            lines = ciel_runtime.set_provider_choice_config(ciel_runtime.CODEX_ROUTED_PROVIDER_CHOICE)

        self.assertEqual("codex", saved["current_provider"])
        self.assertTrue(saved["providers"]["codex"]["route_through_router"])
        self.assertIn("mode: codex-routed", lines)

    def test_openai_responses_input_converts_to_anthropic_messages(self):
        body = {
            "model": "ciel-runtime-ollama-qwen3",
            "instructions": "Be concise.",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "run pwd"}],
                },
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "shell_command",
                    "arguments": "{\"command\":\"pwd\"}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "ok",
                },
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "shell_command",
                    "description": "Run a command",
                    "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
                }
            ],
            "tool_choice": "required",
            "max_output_tokens": 123,
            "stream": True,
        }

        out = ciel_runtime.openai_responses_to_anthropic_messages(body, "fallback-model")

        self.assertEqual("ciel-runtime-ollama-qwen3", out["model"])
        self.assertEqual([{"type": "text", "text": "Be concise."}], out["system"])
        self.assertEqual("user", out["messages"][0]["role"])
        self.assertEqual("run pwd", out["messages"][0]["content"][0]["text"])
        tool_use = out["messages"][1]["content"][0]
        self.assertEqual("tool_use", tool_use["type"])
        self.assertEqual("call_1", tool_use["id"])
        self.assertEqual({"command": "pwd"}, tool_use["input"])
        tool_result = out["messages"][2]["content"][0]
        self.assertEqual("tool_result", tool_result["type"])
        self.assertEqual("call_1", tool_result["tool_use_id"])
        self.assertEqual("shell_command", out["tools"][0]["name"])
        self.assertEqual({"type": "any"}, out["tool_choice"])
        self.assertEqual(123, out["max_tokens"])

    def test_anthropic_message_converts_to_openai_response_items(self):
        message = {
            "id": "msg_1",
            "model": "model-a",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "tool_use", "id": "call_1", "name": "shell_command", "input": {"command": "pwd"}},
            ],
            "usage": {"input_tokens": 3, "output_tokens": 5},
        }

        response = ciel_runtime.anthropic_message_to_openai_response(message, {"tool_choice": "auto", "tools": []})

        self.assertEqual("response", response["object"])
        self.assertEqual("completed", response["status"])
        self.assertEqual("message", response["output"][0]["type"])
        self.assertEqual("output_text", response["output"][0]["content"][0]["type"])
        self.assertEqual("function_call", response["output"][1]["type"])
        self.assertEqual("call_1", response["output"][1]["call_id"])
        self.assertEqual("{\"command\": \"pwd\"}", response["output"][1]["arguments"])
        self.assertEqual({"input_tokens": 3, "output_tokens": 5, "total_tokens": 8}, response["usage"])

    def test_responses_sse_includes_codex_required_lifecycle_events(self):
        handler = FakeSSEHandler()
        message = {
            "model": "model-a",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "tool_use", "id": "call_1", "name": "shell_command", "input": {"command": "pwd"}},
            ],
            "usage": {"input_tokens": 1, "output_tokens": 2},
        }

        ciel_runtime.write_openai_responses_response(handler, message, {"tools": []}, stream=True)

        payload = handler.wfile.getvalue().decode("utf-8")
        self.assertEqual(200, handler.status)
        self.assertIn(("content-type", "text/event-stream"), handler.headers)
        self.assertIn("event: response.created", payload)
        self.assertIn("event: response.output_item.added", payload)
        self.assertIn("event: response.content_part.added", payload)
        self.assertIn("event: response.output_text.delta", payload)
        self.assertIn("event: response.content_part.done", payload)
        self.assertIn("\"type\": \"function_call\"", payload)
        self.assertIn("event: response.completed", payload)

    def test_codex_runtime_config_args_use_responses_provider_without_home_config(self):
        args = ciel_runtime.codex_runtime_config_args("http://127.0.0.1:9876")
        joined = "\n".join(args)

        self.assertIn("model_provider=\"ciel-runtime\"", joined)
        self.assertIn("model_providers.ciel-runtime.base_url=\"http://127.0.0.1:9876/v1\"", joined)
        self.assertIn("model_providers.ciel-runtime.wire_api=\"responses\"", joined)
        self.assertIn("model_providers.ciel-runtime.env_key=\"CIEL_RUNTIME_CODEX_API_KEY\"", joined)

    def test_codex_native_routed_config_args_use_chatgpt_codex_backend_provider(self):
        args = ciel_runtime.codex_native_routed_config_args("http://127.0.0.1:9876")
        joined = "\n".join(args)

        self.assertIn("model_provider=\"ciel-runtime-codex\"", joined)
        self.assertIn("model_providers.ciel-runtime-codex.name=\"Ciel Runtime Codex\"", joined)
        self.assertIn("model_providers.ciel-runtime-codex.base_url=\"http://127.0.0.1:9876/backend-api/codex\"", joined)
        self.assertIn("model_providers.ciel-runtime-codex.wire_api=\"responses\"", joined)
        self.assertIn("model_providers.ciel-runtime-codex.requires_openai_auth=true", joined)
        self.assertIn("model_providers.ciel-runtime-codex.supports_websockets=false", joined)
        self.assertNotIn("env_key", joined)

    def test_codex_alternate_screen_compat_converts_legacy_boolean_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.toml"
            config.write_text("[tui]\nalternate_screen = false\n", encoding="utf-8")
            with mock.patch.object(ciel_runtime, "router_log"), mock.patch("builtins.print"):
                args = ciel_runtime.codex_alternate_screen_compat_args([], env={"CODEX_HOME": tmp}, cwd=Path(tmp))

        self.assertEqual(["-c", "tui.alternate_screen=\"never\""], args)

    def test_codex_alternate_screen_compat_respects_explicit_passthrough(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.toml"
            config.write_text("[tui]\nalternate_screen = false\n", encoding="utf-8")
            env = {"CODEX_HOME": tmp}

            self.assertEqual([], ciel_runtime.codex_alternate_screen_compat_args(["--no-alt-screen"], env=env, cwd=Path(tmp)))
            self.assertEqual([], ciel_runtime.codex_alternate_screen_compat_args(["-c", "tui.alternate_screen=\"never\""], env=env, cwd=Path(tmp)))

    def test_codex_passthrough_maps_claude_session_flags(self):
        args, notes = ciel_runtime.codex_passthrough_args_for_launch(
            [
                "--continue",
                "--channels",
                "server:ai-net",
                "--permission-mode",
                "bypassPermissions",
                "--mcp-config",
                "claude-mcp.json",
                "-c",
                "model=\"gpt-5\"",
                "finish the task",
            ]
        )

        self.assertEqual(
            [
                "resume",
                "--last",
                "--dangerously-bypass-approvals-and-sandbox",
                "-c",
                "model=\"gpt-5\"",
                "finish the task",
            ],
            args,
        )
        self.assertIn("--continue -> resume --last", notes)
        self.assertIn("--channels ignored for Codex launch", notes)
        self.assertIn("--mcp-config ignored for Codex launch", notes)

    def test_codex_passthrough_maps_resume_and_print_forms(self):
        args, _ = ciel_runtime.codex_passthrough_args_for_launch(["--resume", "session-1", "continue work"])
        self.assertEqual(["resume", "session-1", "continue work"], args)

        args, _ = ciel_runtime.codex_passthrough_args_for_launch(["--print", "summarize"])
        self.assertEqual(["exec", "summarize"], args)

        args, _ = ciel_runtime.codex_passthrough_args_for_launch(["--fork-session", "--session-id", "session-2", "inspect"])
        self.assertEqual(["fork", "session-2", "inspect"], args)

        args, _ = ciel_runtime.codex_passthrough_args_for_launch(["exec", "hello", "--continue"])
        self.assertEqual(["exec", "hello"], args)

    def test_run_cli_routes_bare_resume_to_codex_when_codex_provider_selected(self):
        cfg = {"current_provider": "codex", "providers": {"codex": {"route_through_router": True}}}
        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "get_current_provider", return_value=("codex", cfg["providers"]["codex"])),
            mock.patch.object(ciel_runtime, "launch_codex", return_value=0) as launch_codex,
            mock.patch.object(ciel_runtime, "launch_claude") as launch_claude,
        ):
            rc = ciel_runtime.run_cli(["resume"])

        self.assertEqual(0, rc)
        launch_codex.assert_called_once_with(["resume"])
        launch_claude.assert_not_called()

    def test_launch_codex_skips_prelaunch_menu_for_resume_command(self):
        cfg = {"current_provider": "codex", "providers": {"codex": {"route_through_router": False, "base_url": "https://api.openai.com", "current_model": ""}}}
        pcfg = cfg["providers"]["codex"]
        captured = {}

        def subprocess_call(cmd, env):
            captured["cmd"] = cmd
            return 0

        with (
            mock.patch.object(ciel_runtime, "warn_if_multiple_ciel_runtime_installs"),
            mock.patch.object(ciel_runtime, "run_ciel_runtime_update_check"),
            mock.patch.object(ciel_runtime, "auto_import_passthrough_channels"),
            mock.patch.object(ciel_runtime, "run_prelaunch_menu", return_value=0) as prelaunch,
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "get_current_provider", return_value=("codex", pcfg)),
            mock.patch.object(ciel_runtime, "launch_readiness_errors", return_value=[]),
            mock.patch.object(ciel_runtime, "cleanup_managed_services_for_provider"),
            mock.patch.object(ciel_runtime, "install_codex_if_missing", return_value="codex"),
            mock.patch.object(ciel_runtime, "run_codex_update_check", return_value="codex"),
            mock.patch.object(ciel_runtime, "find_executable", return_value="codex"),
            mock.patch.object(ciel_runtime, "codex_alternate_screen_compat_args", return_value=[]),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime.subprocess, "call", side_effect=subprocess_call),
        ):
            rc = ciel_runtime.launch_codex(["resume"])

        self.assertEqual(0, rc)
        prelaunch.assert_called_once_with(["resume"], skip_menu=True, force_menu=False)
        self.assertEqual(["codex", "resume"], captured["cmd"])

    def test_launch_codex_builds_command_with_router_provider(self):
        cfg = {"providers": {"ollama": {"current_model": "qwen3", "base_url": "http://localhost:11434"}}}
        pcfg = cfg["providers"]["ollama"]
        captured = {}

        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        def subprocess_call(cmd, env):
            captured["cmd"] = cmd
            captured["env"] = env
            return 0

        with (
            mock.patch.object(ciel_runtime, "warn_if_multiple_ciel_runtime_installs"),
            mock.patch.object(ciel_runtime, "run_ciel_runtime_update_check"),
            mock.patch.object(ciel_runtime, "auto_import_passthrough_channels"),
            mock.patch.object(ciel_runtime, "run_prelaunch_menu", return_value=0),
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "get_current_provider", return_value=("ollama", pcfg)),
            mock.patch.object(ciel_runtime, "launch_readiness_errors", return_value=[]),
            mock.patch.object(ciel_runtime, "cleanup_managed_services_for_provider"),
            mock.patch.object(ciel_runtime, "start_router_if_needed", return_value=True),
            mock.patch.object(ciel_runtime, "ensure_model_cache_for_launch"),
            mock.patch.object(ciel_runtime, "install_codex_if_missing", return_value="codex") as install_codex,
            mock.patch.object(ciel_runtime, "run_codex_update_check", return_value="codex") as codex_update,
            mock.patch.object(ciel_runtime, "find_executable", return_value="codex"),
            mock.patch.object(ciel_runtime, "current_alias", return_value="ciel-runtime-ollama-qwen3"),
            mock.patch.object(ciel_runtime, "codex_alternate_screen_compat_args", return_value=[]),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=run_with_router_lifetime),
            mock.patch.object(ciel_runtime.subprocess, "call", side_effect=subprocess_call),
        ):
            rc = ciel_runtime.launch_codex(["--no-alt-screen"], skip_menu=True)

        self.assertEqual(0, rc)
        install_codex.assert_called_once_with()
        codex_update.assert_called_once_with("codex", enabled=True)
        self.assertTrue(captured["manage_router"])
        self.assertEqual("codex", captured["cmd"][0])
        self.assertIn("model_provider=\"ciel-runtime\"", captured["cmd"])
        self.assertIn("-m", captured["cmd"])
        self.assertIn("ciel-runtime-ollama-qwen3", captured["cmd"])
        self.assertIn("--no-alt-screen", captured["cmd"])
        self.assertEqual("ciel-runtime-router-local-key", captured["env"]["CIEL_RUNTIME_CODEX_API_KEY"])

    def test_launch_codex_native_uses_plain_codex_command(self):
        cfg = {"current_provider": "codex", "providers": {"codex": {"route_through_router": False, "base_url": "https://api.openai.com", "current_model": ""}}}
        pcfg = cfg["providers"]["codex"]
        captured = {}

        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        def subprocess_call(cmd, env):
            captured["cmd"] = cmd
            captured["env"] = env
            return 0

        with (
            mock.patch.object(ciel_runtime, "warn_if_multiple_ciel_runtime_installs"),
            mock.patch.object(ciel_runtime, "run_ciel_runtime_update_check"),
            mock.patch.object(ciel_runtime, "auto_import_passthrough_channels"),
            mock.patch.object(ciel_runtime, "run_prelaunch_menu", return_value=0),
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "get_current_provider", return_value=("codex", pcfg)),
            mock.patch.object(ciel_runtime, "launch_readiness_errors", return_value=[]),
            mock.patch.object(ciel_runtime, "cleanup_managed_services_for_provider"),
            mock.patch.object(ciel_runtime, "start_router_if_needed") as start_router,
            mock.patch.object(ciel_runtime, "install_codex_if_missing", return_value="codex"),
            mock.patch.object(ciel_runtime, "run_codex_update_check", return_value="codex"),
            mock.patch.object(ciel_runtime, "find_executable", return_value="codex"),
            mock.patch.object(ciel_runtime, "codex_alternate_screen_compat_args", return_value=[]),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=run_with_router_lifetime),
            mock.patch.object(ciel_runtime.subprocess, "call", side_effect=subprocess_call),
        ):
            rc = ciel_runtime.launch_codex(["exec", "hello"], skip_menu=True)

        self.assertEqual(0, rc)
        self.assertFalse(captured["manage_router"])
        start_router.assert_not_called()
        self.assertEqual(["codex", "exec", "hello"], captured["cmd"])
        self.assertNotIn("CIEL_RUNTIME_CODEX_API_KEY", captured["env"])

    def test_launch_codex_routed_uses_native_auth_router_provider(self):
        cfg = {"current_provider": "codex", "providers": {"codex": {"route_through_router": True, "base_url": "https://api.openai.com", "current_model": ""}}}
        pcfg = cfg["providers"]["codex"]
        captured = {}

        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        def subprocess_call(cmd, env):
            captured["cmd"] = cmd
            captured["env"] = env
            return 0

        with (
            mock.patch.object(ciel_runtime, "warn_if_multiple_ciel_runtime_installs"),
            mock.patch.object(ciel_runtime, "run_ciel_runtime_update_check"),
            mock.patch.object(ciel_runtime, "auto_import_passthrough_channels"),
            mock.patch.object(ciel_runtime, "run_prelaunch_menu", return_value=0),
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "get_current_provider", return_value=("codex", pcfg)),
            mock.patch.object(ciel_runtime, "launch_readiness_errors", return_value=[]),
            mock.patch.object(ciel_runtime, "cleanup_managed_services_for_provider"),
            mock.patch.object(ciel_runtime, "start_router_if_needed", return_value=True),
            mock.patch.object(ciel_runtime, "install_codex_if_missing", return_value="codex"),
            mock.patch.object(ciel_runtime, "run_codex_update_check", return_value="codex"),
            mock.patch.object(ciel_runtime, "find_executable", return_value="codex"),
            mock.patch.object(ciel_runtime, "codex_alternate_screen_compat_args", return_value=[]),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=run_with_router_lifetime),
            mock.patch.object(ciel_runtime.subprocess, "call", side_effect=subprocess_call),
        ):
            rc = ciel_runtime.launch_codex(["exec", "hello"], skip_menu=True)

        self.assertEqual(0, rc)
        self.assertTrue(captured["manage_router"])
        self.assertIn("model_provider=\"ciel-runtime-codex\"", captured["cmd"])
        self.assertIn(f"model_providers.ciel-runtime-codex.base_url=\"{ciel_runtime.ROUTER_BASE}/backend-api/codex\"", captured["cmd"])
        self.assertIn("model_providers.ciel-runtime-codex.requires_openai_auth=true", captured["cmd"])
        self.assertIn("model_providers.ciel-runtime-codex.supports_websockets=false", captured["cmd"])
        self.assertNotIn("CIEL_RUNTIME_CODEX_API_KEY", captured["env"])
        self.assertNotIn("-m", captured["cmd"])

    def test_launch_codex_maps_continue_to_resume_last(self):
        cfg = {"current_provider": "codex", "providers": {"codex": {"route_through_router": True, "base_url": "https://api.openai.com", "current_model": ""}}}
        pcfg = cfg["providers"]["codex"]
        captured = {}

        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        def subprocess_call(cmd, env):
            captured["cmd"] = cmd
            captured["env"] = env
            return 0

        with (
            mock.patch.object(ciel_runtime, "warn_if_multiple_ciel_runtime_installs"),
            mock.patch.object(ciel_runtime, "run_ciel_runtime_update_check"),
            mock.patch.object(ciel_runtime, "auto_import_passthrough_channels"),
            mock.patch.object(ciel_runtime, "run_prelaunch_menu", return_value=0),
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "get_current_provider", return_value=("codex", pcfg)),
            mock.patch.object(ciel_runtime, "launch_readiness_errors", return_value=[]),
            mock.patch.object(ciel_runtime, "cleanup_managed_services_for_provider"),
            mock.patch.object(ciel_runtime, "start_router_if_needed", return_value=True),
            mock.patch.object(ciel_runtime, "install_codex_if_missing", return_value="codex"),
            mock.patch.object(ciel_runtime, "run_codex_update_check", return_value="codex"),
            mock.patch.object(ciel_runtime, "find_executable", return_value="codex"),
            mock.patch.object(ciel_runtime, "codex_alternate_screen_compat_args", return_value=[]),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=run_with_router_lifetime),
            mock.patch.object(ciel_runtime.subprocess, "call", side_effect=subprocess_call),
            mock.patch("builtins.print"),
        ):
            rc = ciel_runtime.launch_codex(["--continue"], skip_menu=True)

        self.assertEqual(0, rc)
        self.assertTrue(captured["manage_router"])
        self.assertNotIn("--continue", captured["cmd"])
        self.assertIn("resume", captured["cmd"])
        self.assertIn("--last", captured["cmd"])
        self.assertLess(captured["cmd"].index("resume"), captured["cmd"].index("--last"))

    def test_codex_backend_upstream_url_maps_local_backend_prefix(self):
        self.assertEqual(
            "https://chatgpt.com/backend-api/codex/responses",
            ciel_runtime.codex_backend_upstream_url("/backend-api/codex/responses"),
        )
        self.assertEqual(
            "https://chatgpt.com/backend-api/codex/models?client_version=0.142.2",
            ciel_runtime.codex_backend_upstream_url("/backend-api/codex/models", "client_version=0.142.2"),
        )
        self.assertEqual(
            "https://chatgpt.com/backend-api/codex/responses",
            ciel_runtime.codex_backend_upstream_url("/v1/responses"),
        )

    def test_codex_routed_headers_forward_native_codex_auth_headers(self):
        headers = ciel_runtime.codex_routed_upstream_headers(
            {"api_key": "sk-ignored"},
            {
                "authorization": "Bearer native-token",
                "ChatGPT-Account-ID": "account_1",
                "X-OpenAI-Fedramp": "true",
                "accept-encoding": "gzip",
                "host": "127.0.0.1:8800",
            },
        )

        self.assertEqual("Bearer native-token", headers["authorization"])
        self.assertEqual("account_1", headers["ChatGPT-Account-ID"])
        self.assertEqual("true", headers["X-OpenAI-Fedramp"])
        self.assertEqual("identity", headers["accept-encoding"])
        self.assertNotIn("host", {key.lower(): value for key, value in headers.items()})

    def test_codex_routed_auth_error_explains_wrong_platform_endpoint(self):
        message = ciel_runtime.codex_routed_auth_error_message("invalid_request_error: Missing scopes: api.responses.write")

        self.assertIn("ChatGPT Codex backend", message)
        self.assertIn("/backend-api/codex", message)

    def test_codex_responses_channel_context_appends_responses_input(self):
        body = {"model": "gpt-5.5", "input": "hello"}

        def inject_channel(anthropic_body):
            out = dict(anthropic_body)
            out["messages"] = [
                *anthropic_body["messages"],
                {"role": "user", "content": [{"type": "text", "text": "[channel] wake up"}]},
            ]
            out["metadata"] = {"ciel_runtime_channel_cursor_last_id": "7"}
            return out

        with (
            mock.patch.object(ciel_runtime, "body_with_pending_channel_messages", side_effect=inject_channel),
            mock.patch.object(ciel_runtime, "body_with_pending_channel_summaries", side_effect=lambda value: value),
            mock.patch.object(ciel_runtime, "body_with_channel_tool_result_context", side_effect=lambda value: value),
        ):
            out, delivery = ciel_runtime.codex_responses_body_with_channel_context(body)

        self.assertEqual("7", delivery["metadata"]["ciel_runtime_channel_cursor_last_id"])
        self.assertEqual("hello", out["input"][0]["content"][0]["text"])
        self.assertEqual("[channel] wake up", out["input"][1]["content"][0]["text"])
        self.assertEqual("7", out["metadata"]["ciel_runtime_channel_cursor_last_id"])

    def test_headless_runtime_flag_launches_codex(self):
        with (
            mock.patch.object(
                ciel_runtime,
                "apply_headless_env_config",
                return_value=(True, None, None, None, False),
            ),
            mock.patch.object(ciel_runtime, "launch_codex", return_value=0) as launch_codex,
            mock.patch.object(ciel_runtime, "launch_claude") as launch_claude,
        ):
            rc = ciel_runtime.run_cli(["--ca-runtime", "codex", "--", "exec", "hello"])

        self.assertEqual(0, rc)
        launch_codex.assert_called_once_with(
            ["exec", "hello"],
            skip_menu=True,
            force_menu=False,
            update_check=True,
            self_update_check=True,
        )
        launch_claude.assert_not_called()


if __name__ == "__main__":
    unittest.main()
