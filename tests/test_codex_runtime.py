import copy
import contextlib
import io
import json
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
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(ciel_runtime, "terminate_existing_codex_processes_for_launch", return_value=False)
        self.addCleanup(patcher.stop)
        self.terminate_existing_codex_processes_for_launch = patcher.start()

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
        zai = {"route_through_router": True, "base_url": "https://api.z.ai/api/anthropic", "current_model": "glm-5.2"}

        codex_rows = ciel_runtime.main_menu_rows(cfg, "codex", codex, "en")
        self.assertIn("9. Launch Claude Code [disabled: Codex provider selected]", codex_rows)
        self.assertIn("10. Launch Codex", codex_rows)
        self.assertNotIn("10. Launch Codex [disabled", codex_rows)
        self.assertIn("11. Launch Codex App Server", codex_rows)
        self.assertNotIn("11. Launch Codex App Server [disabled", codex_rows)

        claude_rows = ciel_runtime.main_menu_rows(cfg, "anthropic", anthropic, "en")
        self.assertIn("9. Launch Claude Code", claude_rows)
        self.assertIn("10. Launch Codex [disabled: Anthropic provider selected]", claude_rows)
        self.assertIn("11. Launch Codex App Server [disabled: Anthropic provider selected]", claude_rows)

        zai_rows = ciel_runtime.main_menu_rows(cfg, "zai", zai, "en")
        self.assertIn("9. Launch Claude Code", zai_rows)
        self.assertNotIn("9. Launch Claude Code [disabled", zai_rows)
        self.assertIn("10. Launch Codex", zai_rows)
        self.assertNotIn("10. Launch Codex [disabled", zai_rows)
        self.assertIn("11. Launch Codex App Server", zai_rows)
        self.assertNotIn("11. Launch Codex App Server [disabled", zai_rows)

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

        def subprocess_call(cmd, env, **kwargs):
            captured["cmd"] = cmd
            captured["wake_for_llm_delivery"] = kwargs.get("wake_for_llm_delivery")
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
            mock.patch.object(ciel_runtime, "write_codex_mcp_config_for_channel_discovery", return_value=None),
            mock.patch.object(ciel_runtime, "start_codex_mcp_channel_sse_for_launch", return_value=[]),
            mock.patch.object(ciel_runtime, "install_codex_if_missing", return_value="codex"),
            mock.patch.object(ciel_runtime, "run_codex_update_check", return_value="codex"),
            mock.patch.object(ciel_runtime, "find_executable", return_value="codex"),
            mock.patch.object(ciel_runtime, "codex_alternate_screen_compat_args", return_value=[]),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime, "subprocess_call_with_channel_wake_proxy", side_effect=subprocess_call),
        ):
            rc = ciel_runtime.launch_codex(["resume"])

        self.assertEqual(0, rc)
        prelaunch.assert_called_once_with(["resume"], skip_menu=True, force_menu=False)
        self.assertEqual(["codex", "--yolo", "resume"], captured["cmd"])
        self.assertTrue(captured["wake_for_llm_delivery"])

    def test_launch_codex_builds_command_with_router_provider(self):
        cfg = {"providers": {"ollama": {"current_model": "qwen3", "base_url": "http://localhost:11434"}}}
        pcfg = cfg["providers"]["ollama"]
        captured = {}

        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        def subprocess_call(cmd, env, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = env
            captured["wake_for_llm_delivery"] = kwargs.get("wake_for_llm_delivery")
            captured["synthetic_enter_bytes"] = kwargs.get("synthetic_enter_bytes")
            captured["normalize_bare_cr_for_synthetic_enter"] = kwargs.get("normalize_bare_cr_for_synthetic_enter")
            captured["channel_wake_submit_retries"] = kwargs.get("channel_wake_submit_retries")
            captured["channel_wake_confirm_submit"] = kwargs.get("channel_wake_confirm_submit")
            captured["channel_wake_bracketed_paste"] = kwargs.get("channel_wake_bracketed_paste")
            captured["channel_wake_submit_delay_seconds"] = kwargs.get("channel_wake_submit_delay_seconds")
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
            mock.patch.object(ciel_runtime, "write_codex_mcp_config_for_channel_discovery", return_value=None),
            mock.patch.object(ciel_runtime, "start_codex_mcp_channel_sse_for_launch", return_value=[]),
            mock.patch.object(ciel_runtime, "start_router_if_needed", return_value=True),
            mock.patch.object(ciel_runtime, "ensure_model_cache_for_launch"),
            mock.patch.object(ciel_runtime, "install_codex_if_missing", return_value="codex") as install_codex,
            mock.patch.object(ciel_runtime, "run_codex_update_check", return_value="codex") as codex_update,
            mock.patch.object(ciel_runtime, "find_executable", return_value="codex"),
            mock.patch.object(ciel_runtime, "current_alias", return_value="ciel-runtime-ollama-qwen3"),
            mock.patch.object(ciel_runtime, "codex_alternate_screen_compat_args", return_value=[]),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=run_with_router_lifetime),
            mock.patch.object(ciel_runtime, "subprocess_call_with_channel_wake_proxy", side_effect=subprocess_call),
        ):
            rc = ciel_runtime.launch_codex(["--no-alt-screen"], skip_menu=True)

        self.assertEqual(0, rc)
        install_codex.assert_called_once_with()
        codex_update.assert_called_once_with("codex", enabled=True)
        self.assertTrue(captured["manage_router"])
        self.assertEqual("codex", captured["cmd"][0])
        self.assertEqual("--yolo", captured["cmd"][1])
        self.assertIn("model_provider=\"ciel-runtime\"", captured["cmd"])
        self.assertIn("-m", captured["cmd"])
        self.assertIn("ciel-runtime-ollama-qwen3", captured["cmd"])
        self.assertIn("--no-alt-screen", captured["cmd"])
        self.assertEqual("ciel-runtime-router-local-key", captured["env"]["CIEL_RUNTIME_CODEX_API_KEY"])
        self.assertTrue(captured["wake_for_llm_delivery"])
        self.assertTrue(captured["channel_wake_bracketed_paste"])
        self.assertEqual(0.25, captured["channel_wake_submit_delay_seconds"])

    def test_launch_codex_keeps_native_mcp_and_starts_channel_sse(self):
        cfg = {"providers": {"ollama": {"current_model": "qwen3", "base_url": "http://localhost:11434"}}}
        pcfg = cfg["providers"]["ollama"]
        codex_mcp_config = Path("codex-mcp.json")
        compat_args = [
            "-c",
            'mcp_servers.ai-net.url="http://127.0.0.1:8800/ca/codex-mcp/ai-net"',
        ]
        captured = {}

        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        def subprocess_call(cmd, env, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = env
            return 0

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(ciel_runtime, "warn_if_multiple_ciel_runtime_installs"))
            stack.enter_context(mock.patch.object(ciel_runtime, "run_ciel_runtime_update_check"))
            stack.enter_context(mock.patch.object(ciel_runtime, "auto_import_passthrough_channels"))
            stack.enter_context(mock.patch.object(ciel_runtime, "run_prelaunch_menu", return_value=0))
            stack.enter_context(mock.patch.object(ciel_runtime, "load_config", return_value=cfg))
            stack.enter_context(mock.patch.object(ciel_runtime, "get_current_provider", return_value=("ollama", pcfg)))
            stack.enter_context(mock.patch.object(ciel_runtime, "launch_readiness_errors", return_value=[]))
            stack.enter_context(mock.patch.object(ciel_runtime, "cleanup_managed_services_for_provider"))
            stack.enter_context(mock.patch.object(ciel_runtime, "write_codex_mcp_config_for_channel_discovery", return_value=codex_mcp_config))
            compat = stack.enter_context(mock.patch.object(ciel_runtime, "codex_mcp_native_http_compat_args", return_value=compat_args))
            channel_owned = stack.enter_context(mock.patch.object(ciel_runtime, "codex_channel_capable_mcp_server_names", return_value=["ai-net"]))
            terminate_clients = stack.enter_context(mock.patch.object(ciel_runtime, "terminate_existing_router_clients_for_launch", return_value=False))
            start_sse = stack.enter_context(mock.patch.object(ciel_runtime, "start_codex_mcp_channel_sse_for_launch", return_value=[]))
            stack.enter_context(mock.patch.object(ciel_runtime, "start_router_if_needed", return_value=True))
            stack.enter_context(mock.patch.object(ciel_runtime, "ensure_model_cache_for_launch"))
            stack.enter_context(mock.patch.object(ciel_runtime, "install_codex_if_missing", return_value="codex"))
            stack.enter_context(mock.patch.object(ciel_runtime, "run_codex_update_check", return_value="codex"))
            stack.enter_context(mock.patch.object(ciel_runtime, "find_executable", return_value="codex"))
            stack.enter_context(mock.patch.object(ciel_runtime, "current_alias", return_value="ciel-runtime-ollama-qwen3"))
            stack.enter_context(mock.patch.object(ciel_runtime, "codex_alternate_screen_compat_args", return_value=[]))
            stack.enter_context(mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"))
            stack.enter_context(mock.patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=run_with_router_lifetime))
            stack.enter_context(mock.patch.object(ciel_runtime, "subprocess_call_with_channel_wake_proxy", side_effect=subprocess_call))
            rc = ciel_runtime.launch_codex(["exec", "hello"], skip_menu=True)

        self.assertEqual(0, rc)
        self.assertIn('mcp_servers.ai-net.url="http://127.0.0.1:8800/ca/codex-mcp/ai-net"', captured["cmd"])
        self.assertNotIn("mcp_servers.ai-net.enabled=false", captured["cmd"])
        self.assertNotIn("mcp_servers.ai-net.type=null", captured["cmd"])
        self.assertFalse(any("ciel-runtime-proxy" in str(arg) for arg in captured["cmd"]))
        channel_owned.assert_called_once_with(cfg, codex_mcp_config)
        compat.assert_called_once_with(codex_mcp_config, split_http_proxy=False, channel_owned_server_names=["ai-net"])
        self.terminate_existing_codex_processes_for_launch.assert_called_once()
        terminate_clients.assert_called_once_with("codex_prelaunch_active_clients", quiet=True)
        start_sse.assert_called_once_with(cfg, codex_mcp_config, allowed_server_names=["ai-net"])

    def test_launch_codex_native_uses_plain_codex_command(self):
        cfg = {"current_provider": "codex", "providers": {"codex": {"route_through_router": False, "base_url": "https://api.openai.com", "current_model": ""}}}
        pcfg = cfg["providers"]["codex"]
        captured = {}

        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        def subprocess_call(cmd, env, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = env
            captured["wake_for_llm_delivery"] = kwargs.get("wake_for_llm_delivery")
            captured["synthetic_enter_bytes"] = kwargs.get("synthetic_enter_bytes")
            captured["normalize_bare_cr_for_synthetic_enter"] = kwargs.get("normalize_bare_cr_for_synthetic_enter")
            captured["channel_wake_submit_retries"] = kwargs.get("channel_wake_submit_retries")
            captured["channel_wake_confirm_submit"] = kwargs.get("channel_wake_confirm_submit")
            captured["channel_wake_bracketed_paste"] = kwargs.get("channel_wake_bracketed_paste")
            captured["channel_wake_submit_delay_seconds"] = kwargs.get("channel_wake_submit_delay_seconds")
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
            mock.patch.object(ciel_runtime, "write_codex_mcp_config_for_channel_discovery", return_value=None),
            mock.patch.object(ciel_runtime, "terminate_existing_router_clients_for_launch") as terminate_clients,
            mock.patch.object(ciel_runtime, "start_codex_mcp_channel_sse_for_launch", return_value=[]),
            mock.patch.object(ciel_runtime, "start_router_if_needed") as start_router,
            mock.patch.object(ciel_runtime, "install_codex_if_missing", return_value="codex"),
            mock.patch.object(ciel_runtime, "run_codex_update_check", return_value="codex"),
            mock.patch.object(ciel_runtime, "find_executable", return_value="codex"),
            mock.patch.object(ciel_runtime, "codex_alternate_screen_compat_args", return_value=[]),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=run_with_router_lifetime),
            mock.patch.object(ciel_runtime, "subprocess_call_with_channel_wake_proxy", side_effect=subprocess_call),
        ):
            rc = ciel_runtime.launch_codex(["exec", "hello"], skip_menu=True)

        self.assertEqual(0, rc)
        self.assertFalse(captured["manage_router"])
        start_router.assert_not_called()
        terminate_clients.assert_not_called()
        self.assertEqual(["codex", "--yolo", "exec", "hello"], captured["cmd"])
        self.assertNotIn("CIEL_RUNTIME_CODEX_API_KEY", captured["env"])
        self.assertTrue(captured["wake_for_llm_delivery"])
        self.assertEqual(4, captured["channel_wake_submit_retries"])
        self.assertTrue(captured["channel_wake_confirm_submit"])
        self.assertTrue(captured["channel_wake_bracketed_paste"])
        self.assertEqual(0.25, captured["channel_wake_submit_delay_seconds"])

    def test_launch_codex_routed_uses_native_auth_router_provider(self):
        cfg = {"current_provider": "codex", "providers": {"codex": {"route_through_router": True, "base_url": "https://api.openai.com", "current_model": ""}}}
        pcfg = cfg["providers"]["codex"]
        captured = {}

        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        def subprocess_call(cmd, env, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = env
            captured["wake_for_llm_delivery"] = kwargs.get("wake_for_llm_delivery")
            captured["synthetic_enter_bytes"] = kwargs.get("synthetic_enter_bytes")
            captured["normalize_bare_cr_for_synthetic_enter"] = kwargs.get("normalize_bare_cr_for_synthetic_enter")
            captured["channel_wake_submit_retries"] = kwargs.get("channel_wake_submit_retries")
            captured["channel_wake_confirm_submit"] = kwargs.get("channel_wake_confirm_submit")
            captured["channel_wake_bracketed_paste"] = kwargs.get("channel_wake_bracketed_paste")
            captured["channel_wake_submit_delay_seconds"] = kwargs.get("channel_wake_submit_delay_seconds")
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
            mock.patch.object(ciel_runtime, "write_codex_mcp_config_for_channel_discovery", return_value=None),
            mock.patch.object(ciel_runtime, "terminate_existing_router_clients_for_launch") as terminate_clients,
            mock.patch.object(ciel_runtime, "start_codex_mcp_channel_sse_for_launch", return_value=[]),
            mock.patch.object(ciel_runtime, "start_router_if_needed", return_value=True),
            mock.patch.object(ciel_runtime, "install_codex_if_missing", return_value="codex"),
            mock.patch.object(ciel_runtime, "run_codex_update_check", return_value="codex"),
            mock.patch.object(ciel_runtime, "find_executable", return_value="codex"),
            mock.patch.object(ciel_runtime, "codex_alternate_screen_compat_args", return_value=[]),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=run_with_router_lifetime),
            mock.patch.object(ciel_runtime, "subprocess_call_with_channel_wake_proxy", side_effect=subprocess_call),
        ):
            rc = ciel_runtime.launch_codex(["exec", "hello"], skip_menu=True)

        self.assertEqual(0, rc)
        self.assertTrue(captured["manage_router"])
        terminate_clients.assert_called_once_with("codex_prelaunch_active_clients", quiet=True)
        self.assertEqual(["codex", "--yolo"], captured["cmd"][:2])
        self.assertIn("model_provider=\"ciel-runtime-codex\"", captured["cmd"])
        self.assertIn(f"model_providers.ciel-runtime-codex.base_url=\"{ciel_runtime.ROUTER_BASE}/backend-api/codex\"", captured["cmd"])
        self.assertIn("model_providers.ciel-runtime-codex.requires_openai_auth=true", captured["cmd"])
        self.assertIn("model_providers.ciel-runtime-codex.supports_websockets=false", captured["cmd"])
        self.assertNotIn("CIEL_RUNTIME_CODEX_API_KEY", captured["env"])
        self.assertNotIn("-m", captured["cmd"])
        self.assertTrue(captured["wake_for_llm_delivery"])
        self.assertEqual(b"\r", captured["synthetic_enter_bytes"])
        self.assertFalse(captured["normalize_bare_cr_for_synthetic_enter"])
        self.assertEqual(4, captured["channel_wake_submit_retries"])
        self.assertTrue(captured["channel_wake_confirm_submit"])
        self.assertTrue(captured["channel_wake_bracketed_paste"])
        self.assertEqual(0.25, captured["channel_wake_submit_delay_seconds"])

    def test_launch_codex_routed_passes_configured_current_model(self):
        cfg = {"current_provider": "codex", "providers": {"codex": {"route_through_router": True, "base_url": "https://api.openai.com", "current_model": "gpt-5.1-codex"}}}
        pcfg = cfg["providers"]["codex"]
        captured = {}

        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        def subprocess_call(cmd, env, **kwargs):
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
            mock.patch.object(ciel_runtime, "write_codex_mcp_config_for_channel_discovery", return_value=None),
            mock.patch.object(ciel_runtime, "start_codex_mcp_channel_sse_for_launch", return_value=[]),
            mock.patch.object(ciel_runtime, "start_router_if_needed", return_value=True),
            mock.patch.object(ciel_runtime, "install_codex_if_missing", return_value="codex"),
            mock.patch.object(ciel_runtime, "run_codex_update_check", return_value="codex"),
            mock.patch.object(ciel_runtime, "find_executable", return_value="codex"),
            mock.patch.object(ciel_runtime, "codex_alternate_screen_compat_args", return_value=[]),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=run_with_router_lifetime),
            mock.patch.object(ciel_runtime, "subprocess_call_with_channel_wake_proxy", side_effect=subprocess_call),
        ):
            rc = ciel_runtime.launch_codex(["exec", "hello"], skip_menu=True)

        self.assertEqual(0, rc)
        self.assertIn("-m", captured["cmd"])
        self.assertIn("gpt-5.1-codex", captured["cmd"])

    def test_launch_codex_routed_respects_explicit_model_passthrough(self):
        cfg = {"current_provider": "codex", "providers": {"codex": {"route_through_router": True, "base_url": "https://api.openai.com", "current_model": "gpt-5.1-codex"}}}
        pcfg = cfg["providers"]["codex"]
        captured = {}

        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        def subprocess_call(cmd, env, **kwargs):
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
            mock.patch.object(ciel_runtime, "write_codex_mcp_config_for_channel_discovery", return_value=None),
            mock.patch.object(ciel_runtime, "start_codex_mcp_channel_sse_for_launch", return_value=[]),
            mock.patch.object(ciel_runtime, "start_router_if_needed", return_value=True),
            mock.patch.object(ciel_runtime, "install_codex_if_missing", return_value="codex"),
            mock.patch.object(ciel_runtime, "run_codex_update_check", return_value="codex"),
            mock.patch.object(ciel_runtime, "find_executable", return_value="codex"),
            mock.patch.object(ciel_runtime, "codex_alternate_screen_compat_args", return_value=[]),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=run_with_router_lifetime),
            mock.patch.object(ciel_runtime, "subprocess_call_with_channel_wake_proxy", side_effect=subprocess_call),
        ):
            rc = ciel_runtime.launch_codex(["--model", "gpt-explicit", "exec", "hello"], skip_menu=True)

        self.assertEqual(0, rc)
        self.assertNotIn("gpt-5.1-codex", captured["cmd"])
        self.assertIn("gpt-explicit", captured["cmd"])

    def test_launch_codex_app_server_routed_uses_native_auth_router_provider(self):
        cfg = {"current_provider": "codex", "providers": {"codex": {"route_through_router": True, "base_url": "https://api.openai.com", "current_model": ""}}}
        pcfg = cfg["providers"]["codex"]
        captured = {}

        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        def subprocess_call(cmd, env, pid_path=None):
            captured["cmd"] = cmd
            captured["env"] = env
            captured["pid_path"] = pid_path
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
            mock.patch.object(ciel_runtime, "codex_app_server_default_listen_url", return_value="ws://127.0.0.1:8899"),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=run_with_router_lifetime),
            mock.patch.object(ciel_runtime, "subprocess_call_with_child_pid_record", side_effect=subprocess_call),
        ):
            rc = ciel_runtime.launch_codex_app_server([], skip_menu=True)

        self.assertEqual(0, rc)
        self.assertTrue(captured["manage_router"])
        self.assertEqual(["codex", "app-server"], captured["cmd"][:2])
        self.assertIn("model_provider=\"ciel-runtime-codex\"", captured["cmd"])
        self.assertIn(f"model_providers.ciel-runtime-codex.base_url=\"{ciel_runtime.ROUTER_BASE}/backend-api/codex\"", captured["cmd"])
        self.assertIn("model_providers.ciel-runtime-codex.requires_openai_auth=true", captured["cmd"])
        self.assertIn("--listen", captured["cmd"])
        self.assertIn("ws://127.0.0.1:8899", captured["cmd"])
        self.assertNotIn("CIEL_RUNTIME_CODEX_API_KEY", captured["env"])
        self.assertIn("app-server", str(captured["pid_path"]))

    def test_launch_codex_app_server_routes_channel_owned_mcp_through_split_proxy(self):
        cfg = {"current_provider": "codex", "providers": {"codex": {"route_through_router": True, "base_url": "https://api.openai.com", "current_model": ""}}}
        pcfg = cfg["providers"]["codex"]
        codex_mcp_config = Path("codex-mcp.json")
        compat_args = [
            "-c",
            'mcp_servers.ai-net.url="http://127.0.0.1:8800/ca/codex-mcp/ai-net"',
        ]
        captured = {}

        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        def subprocess_call(cmd, env, pid_path=None):
            captured["cmd"] = cmd
            captured["env"] = env
            captured["pid_path"] = pid_path
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
            mock.patch.object(ciel_runtime, "write_codex_mcp_config_for_channel_discovery", return_value=codex_mcp_config),
            mock.patch.object(ciel_runtime, "codex_channel_capable_mcp_server_names", return_value=["ai-net"]) as channel_owned,
            mock.patch.object(ciel_runtime, "codex_mcp_native_http_compat_args", return_value=compat_args) as compat,
            mock.patch.object(ciel_runtime, "start_codex_mcp_channel_sse_for_launch", return_value=[]) as start_sse,
            mock.patch.object(ciel_runtime, "start_router_if_needed", return_value=True),
            mock.patch.object(ciel_runtime, "install_codex_if_missing", return_value="codex"),
            mock.patch.object(ciel_runtime, "run_codex_update_check", return_value="codex"),
            mock.patch.object(ciel_runtime, "find_executable", return_value="codex"),
            mock.patch.object(ciel_runtime, "codex_app_server_default_listen_url", return_value="ws://127.0.0.1:8899"),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=run_with_router_lifetime),
            mock.patch.object(ciel_runtime, "subprocess_call_with_child_pid_record", side_effect=subprocess_call),
        ):
            rc = ciel_runtime.launch_codex_app_server([], skip_menu=True)

        self.assertEqual(0, rc)
        self.assertIn('mcp_servers.ai-net.url="http://127.0.0.1:8800/ca/codex-mcp/ai-net"', captured["cmd"])
        self.assertNotIn("mcp_servers.ai-net.enabled=false", captured["cmd"])
        self.assertNotIn("mcp_servers.ai-net.type=null", captured["cmd"])
        channel_owned.assert_called_once_with(cfg, codex_mcp_config)
        compat.assert_called_once_with(codex_mcp_config, split_http_proxy=False, channel_owned_server_names=["ai-net"])
        start_sse.assert_called_once_with(cfg, codex_mcp_config, allowed_server_names=["ai-net"])

    def test_launch_codex_app_server_routed_passes_configured_current_model(self):
        cfg = {"current_provider": "codex", "providers": {"codex": {"route_through_router": True, "base_url": "https://api.openai.com", "current_model": "gpt-5.1-codex"}}}
        pcfg = cfg["providers"]["codex"]
        captured = {}

        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        def subprocess_call(cmd, env, pid_path=None):
            captured["cmd"] = cmd
            captured["env"] = env
            captured["pid_path"] = pid_path
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
            mock.patch.object(ciel_runtime, "codex_app_server_default_listen_url", return_value="ws://127.0.0.1:8899"),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=run_with_router_lifetime),
            mock.patch.object(ciel_runtime, "subprocess_call_with_child_pid_record", side_effect=subprocess_call),
        ):
            rc = ciel_runtime.launch_codex_app_server([], skip_menu=True)

        self.assertEqual(0, rc)
        self.assertIn("-c", captured["cmd"])
        self.assertIn('model="gpt-5.1-codex"', captured["cmd"])
        self.assertIn("app-server", str(captured["pid_path"]))

    def test_launch_codex_app_server_builds_router_provider_for_zai(self):
        cfg = {
            "current_provider": "zai",
            "providers": {
                "zai": {
                    "route_through_router": True,
                    "base_url": "https://api.z.ai/api/anthropic",
                    "api_key": "sk-zai-test",
                    "current_model": "glm-5.2",
                }
            },
        }
        pcfg = cfg["providers"]["zai"]
        captured = {}

        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        def subprocess_call(cmd, env, pid_path=None):
            captured["cmd"] = cmd
            captured["env"] = env
            captured["pid_path"] = pid_path
            return 0

        with (
            mock.patch.object(ciel_runtime, "warn_if_multiple_ciel_runtime_installs"),
            mock.patch.object(ciel_runtime, "run_ciel_runtime_update_check"),
            mock.patch.object(ciel_runtime, "auto_import_passthrough_channels"),
            mock.patch.object(ciel_runtime, "run_prelaunch_menu", return_value=0),
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "get_current_provider", return_value=("zai", pcfg)),
            mock.patch.object(ciel_runtime, "launch_readiness_errors", return_value=[]),
            mock.patch.object(ciel_runtime, "cleanup_managed_services_for_provider"),
            mock.patch.object(ciel_runtime, "start_router_if_needed", return_value=True),
            mock.patch.object(ciel_runtime, "ensure_model_cache_for_launch"),
            mock.patch.object(ciel_runtime, "install_codex_if_missing", return_value="codex"),
            mock.patch.object(ciel_runtime, "run_codex_update_check", return_value="codex"),
            mock.patch.object(ciel_runtime, "find_executable", return_value="codex"),
            mock.patch.object(ciel_runtime, "codex_app_server_default_listen_url", return_value="ws://127.0.0.1:8899"),
            mock.patch.object(ciel_runtime, "current_alias", return_value="ciel-runtime-zai-glm-5.2[1m]"),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=run_with_router_lifetime),
            mock.patch.object(ciel_runtime, "subprocess_call_with_child_pid_record", side_effect=subprocess_call),
        ):
            rc = ciel_runtime.launch_codex_app_server([], skip_menu=True)

        self.assertEqual(0, rc)
        self.assertTrue(captured["manage_router"])
        self.assertEqual(["codex", "app-server"], captured["cmd"][:2])
        self.assertIn("model_provider=\"ciel-runtime\"", captured["cmd"])
        self.assertIn(f"model_providers.ciel-runtime.base_url=\"{ciel_runtime.ROUTER_BASE}/v1\"", captured["cmd"])
        self.assertIn("model_providers.ciel-runtime.wire_api=\"responses\"", captured["cmd"])
        self.assertIn("-c", captured["cmd"])
        self.assertIn('model="ciel-runtime-zai-glm-5.2[1m]"', captured["cmd"])
        self.assertEqual("ciel-runtime-router-local-key", captured["env"]["CIEL_RUNTIME_CODEX_API_KEY"])
        self.assertIn("app-server", str(captured["pid_path"]))

    def test_launch_codex_app_server_native_uses_plain_provider_and_default_listen(self):
        cfg = {"current_provider": "codex", "providers": {"codex": {"route_through_router": False, "base_url": "https://api.openai.com", "current_model": ""}}}
        pcfg = cfg["providers"]["codex"]
        captured = {}

        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        def subprocess_call(cmd, env, pid_path=None):
            captured["cmd"] = cmd
            captured["env"] = env
            captured["pid_path"] = pid_path
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
            mock.patch.object(ciel_runtime, "codex_app_server_default_listen_url", return_value="ws://127.0.0.1:8899"),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=run_with_router_lifetime),
            mock.patch.object(ciel_runtime, "subprocess_call_with_child_pid_record", side_effect=subprocess_call),
        ):
            rc = ciel_runtime.launch_codex_app_server([], skip_menu=True)

        self.assertEqual(0, rc)
        self.assertFalse(captured["manage_router"])
        start_router.assert_not_called()
        self.assertEqual(["codex", "app-server", "--listen", "ws://127.0.0.1:8899"], captured["cmd"])
        self.assertNotIn("CIEL_RUNTIME_CODEX_API_KEY", captured["env"])
        self.assertIn("app-server", str(captured["pid_path"]))

    def test_launch_codex_maps_continue_to_resume_last(self):
        cfg = {"current_provider": "codex", "providers": {"codex": {"route_through_router": True, "base_url": "https://api.openai.com", "current_model": ""}}}
        pcfg = cfg["providers"]["codex"]
        captured = {}

        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        def subprocess_call(cmd, env, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = env
            captured["wake_for_llm_delivery"] = kwargs.get("wake_for_llm_delivery")
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
            mock.patch.object(ciel_runtime, "write_codex_mcp_config_for_channel_discovery", return_value=None),
            mock.patch.object(ciel_runtime, "start_codex_mcp_channel_sse_for_launch", return_value=[]),
            mock.patch.object(ciel_runtime, "start_router_if_needed", return_value=True),
            mock.patch.object(ciel_runtime, "install_codex_if_missing", return_value="codex"),
            mock.patch.object(ciel_runtime, "run_codex_update_check", return_value="codex"),
            mock.patch.object(ciel_runtime, "find_executable", return_value="codex"),
            mock.patch.object(ciel_runtime, "codex_alternate_screen_compat_args", return_value=[]),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=run_with_router_lifetime),
            mock.patch.object(ciel_runtime, "subprocess_call_with_channel_wake_proxy", side_effect=subprocess_call),
            mock.patch("builtins.print"),
        ):
            rc = ciel_runtime.launch_codex(["--continue"], skip_menu=True)

        self.assertEqual(0, rc)
        self.assertTrue(captured["manage_router"])
        self.assertIn("--yolo", captured["cmd"])
        self.assertNotIn("--continue", captured["cmd"])
        self.assertIn("resume", captured["cmd"])
        self.assertIn("--last", captured["cmd"])
        self.assertLess(captured["cmd"].index("resume"), captured["cmd"].index("--last"))
        self.assertTrue(captured["wake_for_llm_delivery"])

    def test_launch_codex_does_not_duplicate_explicit_yolo_flag(self):
        cfg = {"current_provider": "codex", "providers": {"codex": {"route_through_router": False, "base_url": "https://api.openai.com", "current_model": ""}}}
        pcfg = cfg["providers"]["codex"]
        captured = {}

        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        def subprocess_call(cmd, env, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = env
            captured["wake_for_llm_delivery"] = kwargs.get("wake_for_llm_delivery")
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
            mock.patch.object(ciel_runtime, "write_codex_mcp_config_for_channel_discovery", return_value=None),
            mock.patch.object(ciel_runtime, "start_codex_mcp_channel_sse_for_launch", return_value=[]),
            mock.patch.object(ciel_runtime, "start_router_if_needed") as start_router,
            mock.patch.object(ciel_runtime, "install_codex_if_missing", return_value="codex"),
            mock.patch.object(ciel_runtime, "run_codex_update_check", return_value="codex"),
            mock.patch.object(ciel_runtime, "find_executable", return_value="codex"),
            mock.patch.object(ciel_runtime, "codex_alternate_screen_compat_args", return_value=[]),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=run_with_router_lifetime),
            mock.patch.object(ciel_runtime, "subprocess_call_with_channel_wake_proxy", side_effect=subprocess_call),
        ):
            rc = ciel_runtime.launch_codex(["--yolo", "exec", "hello"], skip_menu=True)

        self.assertEqual(0, rc)
        self.assertFalse(captured["manage_router"])
        start_router.assert_not_called()
        self.assertEqual(["codex", "--yolo", "exec", "hello"], captured["cmd"])
        self.assertTrue(captured["wake_for_llm_delivery"])

    def test_terminate_tracked_codex_processes_kills_recorded_child(self):
        with tempfile.TemporaryDirectory() as td:
            process_dir = Path(td) / "codex-processes"
            process_dir.mkdir()
            record = process_dir / "100-client.json"
            record.write_text(json.dumps({"pid": 12345, "cmd": ["codex", "--yolo"]}), encoding="utf-8")

            with (
                mock.patch.object(ciel_runtime, "CODEX_PROCESS_DIR", process_dir),
                mock.patch.object(ciel_runtime, "pid_is_running", return_value=True),
                mock.patch.object(ciel_runtime, "_process_command_line", return_value="/usr/bin/node /usr/bin/codex --yolo"),
                mock.patch.object(ciel_runtime, "_process_environ_contains", return_value=False),
                mock.patch.object(ciel_runtime, "terminate_pid_tree", return_value=True) as terminate_tree,
            ):
                stopped = ciel_runtime.terminate_tracked_codex_processes("test", quiet=True)
            record_exists_after = record.exists()

        self.assertTrue(stopped)
        terminate_tree.assert_called_once_with(12345, "previous Codex", quiet=True)
        self.assertFalse(record_exists_after)

    def test_codex_process_match_includes_app_server_without_yolo(self):
        with mock.patch.object(ciel_runtime, "_process_environ_contains", return_value=False):
            self.assertTrue(
                ciel_runtime._looks_like_ciel_managed_codex_process(
                    12345,
                    "/home/user/.npm-global/bin/codex app-server --listen ws://127.0.0.1:8899",
                )
            )

    def test_child_pid_record_wrapper_terminates_child_on_interrupt(self):
        class FakeProc:
            pid = 23456

            def wait(self, timeout=None):
                if timeout is None:
                    raise KeyboardInterrupt
                return 0

            def poll(self):
                return None

            def kill(self):
                return None

        with tempfile.TemporaryDirectory() as td:
            record = Path(td) / "codex-processes" / "23456-client.json"
            fake_proc = FakeProc()

            with (
                mock.patch.object(ciel_runtime.subprocess, "Popen", return_value=fake_proc),
                mock.patch.object(ciel_runtime, "terminate_pid_tree", return_value=True) as terminate_tree,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    ciel_runtime.subprocess_call_with_child_pid_record(["codex", "--yolo"], {}, record)
            record_exists_after = record.exists()

        terminate_tree.assert_called_once_with(23456, "current Codex", quiet=True)
        self.assertFalse(record_exists_after)

    def test_codex_mcp_config_writer_normalizes_http_servers_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            codex_home = root / ".codex"
            codex_home.mkdir()
            config = codex_home / "config.toml"
            config.write_text(
                """
[mcp_servers.ai-net]
url = "http://example.test/mcp"
bearer_token_env_var = "AINET_API_KEY"

[mcp_servers.local-stdio]
command = "python3"
args = ["server.py"]
""",
                encoding="utf-8",
            )
            generated = root / "codex-mcp.json"

            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CODEX_MCP_CONFIG", generated),
            ):
                written = ciel_runtime.write_codex_mcp_config_for_channel_discovery(
                    [],
                    env={"CODEX_HOME": str(codex_home)},
                    cwd=root,
                )

            self.assertEqual(generated, written)
            data = json.loads(generated.read_text(encoding="utf-8"))
            self.assertEqual(["ai-net"], sorted(data["mcpServers"]))
            self.assertEqual("http", data["mcpServers"]["ai-net"]["type"])
            self.assertEqual("AINET_API_KEY", data["mcpServers"]["ai-net"]["bearer_token_env_var"])

    def test_codex_mcp_native_http_compat_args_can_route_http_through_split_proxy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            codex_home = root / ".codex"
            codex_home.mkdir()
            config = codex_home / "config.toml"
            config.write_text(
                """
[mcp_servers.ai-net]
type = "http"
url = "http://example.test/mcp"
bearer_token_env_var = "AINET_API_KEY"
""",
                encoding="utf-8",
            )
            generated = root / "codex-mcp.json"

            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CODEX_MCP_CONFIG", generated),
                mock.patch.object(ciel_runtime, "cached_channel_capable_server_names", return_value=[]),
            ):
                codex_mcp_config = ciel_runtime.write_codex_mcp_config_for_channel_discovery(
                    [],
                    env={"CODEX_HOME": str(codex_home)},
                    cwd=root,
                )
                args = ciel_runtime.codex_mcp_native_http_compat_args(codex_mcp_config)

            self.assertNotIn("mcp_servers.ai-net.type=null", args)
            self.assertNotIn("mcp_servers.ai-net.enabled=false", args)
            self.assertFalse(any("ciel-runtime-proxy" in str(arg) for arg in args))

            with mock.patch.object(ciel_runtime, "ROUTER_BASE", "http://127.0.0.1:8800"):
                split_args = ciel_runtime.codex_mcp_native_http_compat_args(codex_mcp_config, split_http_proxy=True)

            self.assertNotIn("mcp_servers.ai-net.type=null", split_args)
            self.assertIn('mcp_servers.ai-net.url="http://127.0.0.1:8800/ca/codex-mcp/ai-net"', split_args)
            self.assertNotIn("mcp_servers.ai-net.enabled=false", split_args)
            self.assertFalse(any("ciel-runtime-proxy" in str(arg) for arg in split_args))

    def test_codex_mcp_native_http_compat_does_not_null_implicit_oauth_type(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            codex_home = root / ".codex"
            codex_home.mkdir()
            config = codex_home / "config.toml"
            config.write_text(
                """
[mcp_servers.supabase]
url = "https://mcp.supabase.com/mcp"

[mcp_servers.supabase.env_http_headers]
Authorization = "SUPABASE_MCP_AUTHORIZATION"
""",
                encoding="utf-8",
            )
            generated = root / "codex-mcp.json"

            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CODEX_MCP_CONFIG", generated),
            ):
                codex_mcp_config = ciel_runtime.write_codex_mcp_config_for_channel_discovery(
                    [],
                    env={"CODEX_HOME": str(codex_home)},
                    cwd=root,
                )
                args = ciel_runtime.codex_mcp_native_http_compat_args(codex_mcp_config)

                with mock.patch.object(ciel_runtime, "ROUTER_BASE", "http://127.0.0.1:8800"):
                    split_args = ciel_runtime.codex_mcp_native_http_compat_args(codex_mcp_config, split_http_proxy=True)

        self.assertNotIn("mcp_servers.supabase.type=null", args)
        self.assertNotIn("mcp_servers.supabase.type=null", split_args)
        self.assertIn('mcp_servers.supabase.url="http://127.0.0.1:8800/ca/codex-mcp/supabase"', split_args)

    def test_codex_mcp_native_http_compat_routes_implicit_channel_owned_http_server_through_split_proxy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            codex_home = root / ".codex"
            codex_home.mkdir()
            config = codex_home / "config.toml"
            config.write_text(
                """
[mcp_servers.ai-net]
url = "http://example.test/mcp"
bearer_token_env_var = "AINET_API_KEY"
""",
                encoding="utf-8",
            )
            generated = root / "codex-mcp.json"

            with (
                mock.patch.object(ciel_runtime, "CONFIG_DIR", root),
                mock.patch.object(ciel_runtime, "CODEX_MCP_CONFIG", generated),
            ):
                codex_mcp_config = ciel_runtime.write_codex_mcp_config_for_channel_discovery(
                    [],
                    env={"CODEX_HOME": str(codex_home)},
                    cwd=root,
                )
                args = ciel_runtime.codex_mcp_native_http_compat_args(
                    codex_mcp_config,
                    channel_owned_server_names=["ai-net"],
                )

        self.assertTrue(
            any(str(arg).startswith('mcp_servers.ai-net.url="http://127.0.0.1:') and str(arg).endswith('/ca/codex-mcp/ai-net"') for arg in args),
            args,
        )
        self.assertNotIn("mcp_servers.ai-net.enabled=false", args)
        self.assertNotIn("mcp_servers.ai-net.type=null", args)

    def test_codex_mcp_split_proxy_is_opt_in(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(ciel_runtime.codex_mcp_split_proxy_enabled())
        with mock.patch.dict("os.environ", {"CIEL_RUNTIME_CODEX_MCP_SPLIT_PROXY": "1"}, clear=True):
            self.assertTrue(ciel_runtime.codex_mcp_split_proxy_enabled())

    def test_codex_responses_import_session_short_circuits_before_upstream(self):
        handler = object()
        with tempfile.TemporaryDirectory() as td:
            transcript = Path(td) / "claude.jsonl"
            transcript.write_text(
                ciel_runtime.json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": "claude task"}],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            cfg = {
                "current_provider": "kimi",
                "providers": {
                    "kimi": {
                        "current_model": "kimi-for-coding",
                        "api_key": "sk-kimi-test",
                    }
                },
            }
            pcfg = cfg["providers"]["kimi"]
            body = {
                "model": "ciel-runtime-kimi-kimi-for-coding",
                "stream": False,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": f"CIEL_RUNTIME_IMPORT_SESSION\n\nArguments: Claude {transcript}",
                            }
                        ],
                    }
                ],
            }

            with (
                mock.patch.object(ciel_runtime, "write_openai_responses_response") as write,
                mock.patch.object(ciel_runtime, "collect_provider_message_for_responses") as collect,
            ):
                ciel_runtime.handle_openai_responses_post(handler, cfg, "kimi", pcfg, body)  # type: ignore[arg-type]

        write.assert_called_once()
        collect.assert_not_called()
        message = write.call_args.args[1]
        self.assertIn("User: claude task", message["content"][0]["text"])

    def test_mcp_runtime_headers_resolve_bearer_token_env_var(self):
        with mock.patch.dict("os.environ", {"AINET_API_KEY": "token-123"}, clear=False):
            headers = ciel_runtime.mcp_server_runtime_headers({"bearer_token_env_var": "AINET_API_KEY"})

        self.assertEqual("Bearer token-123", headers["Authorization"])

    def test_codex_channel_sse_starts_only_capable_unowned_codex_servers(self):
        cfg = {"claude_code": {"channel_delivery": "llm", "channels": ["server:already-owned"]}}
        captured = {}

        def fake_auto_start(passthrough, extra_config_paths=None, allowed_server_names=None, include_default_paths=True):
            captured["passthrough"] = passthrough
            captured["extra"] = extra_config_paths
            captured["allowed"] = allowed_server_names
            captured["include_default_paths"] = include_default_paths
            return [{"name": "mcp-ai-net"}]

        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / "codex-mcp.json"
            config.write_text(
                '{"mcpServers": {"ai-net": {"type": "http", "url": "http://example.test/mcp"}}}',
                encoding="utf-8",
            )
            with (
                mock.patch.object(ciel_runtime, "ensure_channel_probe_cache_for_launch") as ensure_probe,
                mock.patch.object(
                    ciel_runtime,
                    "cached_channel_probe_servers",
                    return_value=[
                        {"name": "ai-net", "capable": True, "source_path": str(config)},
                        {"name": "already-owned", "capable": True, "source_path": str(config)},
                        {"name": "ai-net-http", "capable": True, "source_path": str(Path(td) / ".mcp.json")},
                    ],
                ),
                mock.patch.object(ciel_runtime, "auto_start_sse_channels_from_mcp_configs", side_effect=fake_auto_start),
            ):
                started = ciel_runtime.start_codex_mcp_channel_sse_for_launch(cfg, config)

        self.assertEqual([{"name": "mcp-ai-net"}], started)
        ensure_probe.assert_called_once_with(cfg, [], extra_config_paths=[config])
        self.assertEqual([], captured["passthrough"])
        self.assertEqual([config], captured["extra"])
        self.assertEqual(["ai-net"], captured["allowed"])
        self.assertFalse(captured["include_default_paths"])

    def test_codex_channel_capable_names_filter_to_codex_mcp_config_source(self):
        cfg = {"claude_code": {"channel_delivery": "llm", "channels": []}}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            codex_config = root / "codex-mcp.json"
            default_mcp = root / ".mcp.json"
            codex_config.write_text(
                json.dumps({"mcpServers": {"ai-net": {"type": "http", "url": "http://example.test/mcp"}}}),
                encoding="utf-8",
            )
            default_mcp.write_text(
                json.dumps({"mcpServers": {"ai-net-http": {"type": "http", "url": "http://example.test/mcp"}}}),
                encoding="utf-8",
            )
            cache = {
                "servers": [
                    {
                        "name": "ai-net",
                        "capable": True,
                        "transport": "streamable-http",
                        "source_path": str(codex_config),
                        "url": "http://example.test/mcp",
                    },
                    {
                        "name": "ai-net-http",
                        "capable": True,
                        "transport": "streamable-http",
                        "source_path": str(default_mcp),
                        "url": "http://example.test/mcp",
                    },
                ]
            }

            with (
                mock.patch.object(ciel_runtime, "ensure_channel_probe_cache_for_launch"),
                mock.patch.object(ciel_runtime, "cached_channel_probe_servers", return_value=cache["servers"]),
            ):
                names = ciel_runtime.codex_channel_capable_mcp_server_names(cfg, codex_config)

        self.assertEqual(["ai-net"], names)

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
            mock.patch.object(ciel_runtime, "body_with_channel_tool_result_context", side_effect=lambda value: value),
        ):
            out, delivery = ciel_runtime.codex_responses_body_with_channel_context(body)

        self.assertEqual("7", delivery["metadata"]["ciel_runtime_channel_cursor_last_id"])
        self.assertEqual("hello", out["input"][0]["content"][0]["text"])
        self.assertEqual("[channel] wake up", out["input"][1]["content"][0]["text"])
        self.assertNotIn("metadata", out)

    def test_codex_responses_channel_context_strips_upstream_metadata(self):
        body = {"model": "gpt-5.5", "input": "hello", "metadata": {"client": "native"}}

        with (
            mock.patch.object(ciel_runtime, "body_with_pending_channel_messages", side_effect=lambda value: value),
            mock.patch.object(ciel_runtime, "body_with_channel_tool_result_context", side_effect=lambda value: value),
        ):
            out, delivery = ciel_runtime.codex_responses_body_with_channel_context(body)

        self.assertNotIn("metadata", out)
        self.assertNotIn("metadata", delivery)

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
