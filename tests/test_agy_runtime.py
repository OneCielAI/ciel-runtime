import copy
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ciel_runtime


class AgyRuntimeTests(unittest.TestCase):
    def test_provider_menu_exposes_native_and_routed_agy_choices(self):
        cfg = {
            "current_provider": "agy",
            "providers": {
                "agy": {
                    "base_url": "https://antigravity.google",
                    "api_key": "",
                    "route_through_router": False,
                },
            },
        }

        rows, values = ciel_runtime.provider_panel_rows(cfg)

        self.assertIn(ciel_runtime.AGY_NATIVE_PROVIDER_CHOICE, values)
        self.assertIn(ciel_runtime.AGY_ROUTED_PROVIDER_CHOICE, values)
        self.assertTrue(any("AGY" in row and row.startswith("*") for row in rows))
        self.assertTrue(any("AGY Routed" in row and "channel/PTY wake support" in row for row in rows))
        labels = [row[2:18].strip() for row in rows]
        self.assertEqual(sorted(labels, key=str.casefold), labels)

    def test_provider_command_lists_agy_choice_labels(self):
        cfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        cfg["current_provider"] = "anthropic"

        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            ciel_runtime.cmd_provider(type("Args", (), {"name": ""})())

        output = stdout.getvalue()
        self.assertIn("AGY", output)
        self.assertIn("agy-native", output)
        self.assertIn("AGY Routed", output)
        self.assertIn("agy-routed", output)

    def test_main_menu_disables_opposite_runtimes_for_agy_provider(self):
        cfg = {"language": "en"}
        agy = {"route_through_router": True, "base_url": "https://antigravity.google", "current_model": ""}
        codex = {"route_through_router": True, "base_url": "https://api.openai.com", "current_model": ""}
        anthropic = {"route_through_router": True, "base_url": "https://api.anthropic.com", "current_model": "claude"}

        agy_rows = ciel_runtime.main_menu_rows(cfg, "agy", agy, "en")
        self.assertIn("9. Launch Claude Code [disabled: AGY provider selected]", agy_rows)
        self.assertIn("10. Launch Codex [disabled: AGY provider selected]", agy_rows)
        self.assertIn("11. Launch Codex App Server [disabled: AGY provider selected]", agy_rows)
        self.assertIn("12. Launch AGY", agy_rows)
        self.assertNotIn("12. Launch AGY [disabled", agy_rows)

        codex_rows = ciel_runtime.main_menu_rows(cfg, "codex", codex, "en")
        self.assertIn("10. Launch Codex", codex_rows)
        self.assertIn("11. Launch Codex App Server", codex_rows)
        self.assertIn("12. Launch AGY [disabled: select AGY provider]", codex_rows)

        claude_rows = ciel_runtime.main_menu_rows(cfg, "anthropic", anthropic, "en")
        self.assertIn("9. Launch Claude Code", claude_rows)
        self.assertIn("11. Launch Codex App Server [disabled: Anthropic provider selected]", claude_rows)
        self.assertIn("12. Launch AGY [disabled: select AGY provider]", claude_rows)

    def test_provider_choice_toggles_agy_routing(self):
        cfg = {
            "current_provider": "anthropic",
            "providers": {
                "anthropic": {"route_through_router": False},
                "agy": {"base_url": "https://antigravity.google", "route_through_router": False},
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
            lines = ciel_runtime.set_provider_choice_config(ciel_runtime.AGY_ROUTED_PROVIDER_CHOICE)

        self.assertEqual("agy", saved["current_provider"])
        self.assertTrue(saved["providers"]["agy"]["route_through_router"])
        self.assertIn("mode: agy-routed", lines)

    def test_agy_passthrough_maps_shared_runtime_flags(self):
        args, notes = ciel_runtime.agy_passthrough_args_for_launch(
            [
                "--resume",
                "conversation-1",
                "--permission-mode",
                "bypassPermissions",
                "--channels",
                "server:ai-net",
                "--print=hello",
            ]
        )

        self.assertEqual(
            [
                "--conversation",
                "conversation-1",
                "--dangerously-skip-permissions",
                "--print",
                "hello",
            ],
            args,
        )
        self.assertIn("--resume <session> -> --conversation <session>", notes)
        self.assertIn("--permission-mode bypassPermissions -> --dangerously-skip-permissions", notes)
        self.assertIn("--channels ignored for AGY launch", notes)

    def test_launch_agy_routed_uses_channel_wake_proxy(self):
        cfg = {"current_provider": "agy", "providers": {"agy": {"route_through_router": True, "base_url": "https://antigravity.google", "current_model": ""}}}
        pcfg = cfg["providers"]["agy"]
        captured = {}

        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        def subprocess_call(cmd, env, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = env
            captured["wake_for_llm_delivery"] = kwargs.get("wake_for_llm_delivery")
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
            mock.patch.object(ciel_runtime, "get_current_provider", return_value=("agy", pcfg)),
            mock.patch.object(ciel_runtime, "launch_readiness_errors", return_value=[]),
            mock.patch.object(ciel_runtime, "cleanup_managed_services_for_provider"),
            mock.patch.object(ciel_runtime, "restore_agy_mcp_config_from_managed") as restore_mcp,
            mock.patch.object(ciel_runtime, "start_router_if_needed", return_value=True),
            mock.patch.object(ciel_runtime, "install_agy_if_missing", return_value="agy"),
            mock.patch.object(ciel_runtime, "run_agy_update_check", return_value="agy"),
            mock.patch.object(ciel_runtime, "find_executable", return_value="agy"),
            mock.patch.object(ciel_runtime, "channel_delivery_mode", return_value="llm"),
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd"),
            mock.patch.object(ciel_runtime, "run_with_router_lifetime", side_effect=run_with_router_lifetime),
            mock.patch.object(ciel_runtime, "subprocess_call_with_channel_wake_proxy", side_effect=subprocess_call),
        ):
            rc = ciel_runtime.launch_agy(["--resume", "conversation-1"], skip_menu=True)

        self.assertEqual(0, rc)
        restore_mcp.assert_called_once()
        self.assertTrue(captured["manage_router"])
        self.assertEqual(["agy", "--dangerously-skip-permissions", "--conversation", "conversation-1"], captured["cmd"])
        self.assertTrue(captured["wake_for_llm_delivery"])
        self.assertEqual(4, captured["channel_wake_submit_retries"])
        self.assertTrue(captured["channel_wake_confirm_submit"])
        self.assertTrue(captured["channel_wake_bracketed_paste"])
        self.assertEqual(0.25, captured["channel_wake_submit_delay_seconds"])

    def test_headless_runtime_flag_launches_agy(self):
        with (
            mock.patch.object(
                ciel_runtime,
                "apply_headless_env_config",
                return_value=(True, None, None, None, False),
            ),
            mock.patch.object(ciel_runtime, "launch_agy", return_value=0) as launch_agy,
            mock.patch.object(ciel_runtime, "launch_claude") as launch_claude,
            mock.patch.object(ciel_runtime, "launch_codex") as launch_codex,
        ):
            rc = ciel_runtime.run_cli(["--ca-runtime", "agy", "--", "--continue"])

        self.assertEqual(0, rc)
        launch_agy.assert_called_once_with(
            ["--continue"],
            skip_menu=True,
            force_menu=False,
            update_check=True,
            self_update_check=True,
        )
        launch_claude.assert_not_called()
        launch_codex.assert_not_called()
