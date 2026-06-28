import unittest
import tempfile
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ciel_runtime


class HeadlessUpdateCheckTests(unittest.TestCase):
    def test_print_mode_disables_update_prompts(self):
        patches = [
            patch("ciel_runtime.run_prelaunch_menu", return_value=0),
            patch("ciel_runtime.load_config", return_value={}),
            patch("ciel_runtime.get_current_provider", return_value=("anthropic", {})),
            patch("ciel_runtime.launch_readiness_errors", return_value=[]),
            patch("ciel_runtime.native_anthropic_enabled", return_value=True),
            patch("ciel_runtime.ollama_native_compat_enabled", return_value=False),
            patch("ciel_runtime.provider_native_compat_enabled", return_value=False),
            patch("ciel_runtime.cleanup_managed_services_for_provider"),
            patch("ciel_runtime.env_vars", return_value={}),
            patch("ciel_runtime.find_executable", return_value="claude"),
            patch("ciel_runtime.should_attach_web_search", return_value=False),
            patch("ciel_runtime.should_append_compat_prompt", return_value=False),
            patch("ciel_runtime.subprocess.call", return_value=0),
        ]
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patches[7],
            patches[8],
            patches[9],
            patches[10],
            patches[11],
            patches[12],
            patch("ciel_runtime.run_ciel_runtime_update_check") as self_update,
            patch("ciel_runtime.run_claude_update_check") as claude_update,
        ):
            rc = ciel_runtime.launch_claude(["-p", "hello"])

        self.assertEqual(0, rc)
        self_update.assert_called_once_with(enabled=False)
        claude_update.assert_called_once_with("claude", enabled=True)

    def test_print_long_option_is_noninteractive(self):
        self.assertTrue(ciel_runtime.has_noninteractive_claude_args(["--print", "hello"]))
        self.assertTrue(ciel_runtime.has_noninteractive_claude_args(["--print=hello"]))

    def test_quiet_upgrade_flag_updates_and_exits(self):
        with (
            patch("ciel_runtime.run_quiet_upgrade_and_exit", return_value=0) as upgrade,
            patch("ciel_runtime.launch_claude") as launch,
        ):
            rc = ciel_runtime.run_cli(["--ca-upgrade-and-exit"])

        self.assertEqual(0, rc)
        upgrade.assert_called_once_with()
        launch.assert_not_called()

    def test_quiet_upgrade_runs_both_updaters(self):
        with (
            patch("ciel_runtime.quiet_upgrade_ciel_runtime", return_value=0) as any_update,
            patch("ciel_runtime.quiet_upgrade_claude_code", return_value=0) as claude_update,
            patch("ciel_runtime.quiet_upgrade_codex", return_value=0) as codex_update,
            patch("ciel_runtime.quiet_upgrade_agy", return_value=0) as agy_update,
        ):
            rc = ciel_runtime.run_quiet_upgrade_and_exit()

        self.assertEqual(0, rc)
        any_update.assert_called_once_with()
        claude_update.assert_called_once_with()
        codex_update.assert_called_once_with()
        agy_update.assert_called_once_with()

    def test_quiet_upgrade_reports_failure_when_any_updater_fails(self):
        with (
            patch("ciel_runtime.quiet_upgrade_ciel_runtime", return_value=0),
            patch("ciel_runtime.quiet_upgrade_claude_code", return_value=1),
            patch("ciel_runtime.quiet_upgrade_codex", return_value=0),
            patch("ciel_runtime.quiet_upgrade_agy", return_value=0),
        ):
            self.assertEqual(1, ciel_runtime.run_quiet_upgrade_and_exit())

    def test_self_update_uses_active_install_prefix_and_restarts_from_fresh_package(self):
        completed = type("Completed", (), {"returncode": 0, "stdout": ""})()
        package_root = Path("/home/user/.local/lib/node_modules/@oneciel-ai/ciel-runtime")
        with (
            patch.dict("os.environ", {"CIEL_RUNTIME_SKIP_SELF_UPDATE": "0"}, clear=False),
            patch("ciel_runtime.running_from_npm_package", return_value=True),
            patch("ciel_runtime.sys.stdin.isatty", return_value=True),
            patch("ciel_runtime.sys.stdout.isatty", return_value=True),
            patch("ciel_runtime.find_executable", return_value="npm"),
            patch("ciel_runtime.npm_latest_package_version", return_value="999.0.0"),
            patch("ciel_runtime.version_newer", return_value=True),
            patch("ciel_runtime.current_npm_package_root", return_value=package_root),
            patch("ciel_runtime.subprocess.run", return_value=completed) as run,
            patch("ciel_runtime.restart_ciel_runtime_after_update") as restart,
            patch("builtins.print"),
        ):
            ciel_runtime.run_ciel_runtime_update_check()

        self.assertEqual(
            ["npm", "install", "-g", "--prefix", str(Path("/home/user/.local")), "@oneciel-ai/ciel-runtime@latest"],
            run.call_args.args[0],
        )
        self.assertEqual("y\n", run.call_args.kwargs["input"])
        self.assertEqual("true", run.call_args.kwargs["env"]["NPM_CONFIG_YES"])
        self.assertEqual("1", run.call_args.kwargs["env"]["CI"])
        restart.assert_called_once_with("npm", package_root=package_root)

    def test_quiet_upgrade_uses_active_install_prefix(self):
        completed = type("Completed", (), {"returncode": 0, "stdout": ""})()
        package_root = Path("/usr/local/lib/node_modules/@oneciel-ai/ciel-runtime")
        with (
            patch("ciel_runtime.find_executable", return_value="npm"),
            patch("ciel_runtime.npm_latest_package_version", return_value="999.0.0"),
            patch("ciel_runtime.version_newer", return_value=True),
            patch("ciel_runtime.current_npm_package_root", return_value=package_root),
            patch("ciel_runtime.subprocess.run", return_value=completed) as run,
            patch("builtins.print"),
        ):
            rc = ciel_runtime.quiet_upgrade_ciel_runtime()

        self.assertEqual(0, rc)
        self.assertEqual(
            ["npm", "install", "-g", "--prefix", str(Path("/usr/local")), "@oneciel-ai/ciel-runtime@latest"],
            run.call_args.args[0],
        )
        self.assertEqual("y\n", run.call_args.kwargs["input"])
        self.assertEqual("true", run.call_args.kwargs["env"]["NPM_CONFIG_YES"])

    def test_upgrade_runner_forces_yes_without_prompt(self):
        completed = type("Completed", (), {"returncode": 0, "stdout": "ok\n"})()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("ciel_runtime.subprocess.run", return_value=completed) as run,
        ):
            rc, out = ciel_runtime.run_command_for_upgrade(["npm", "install", "-g", "pkg"])

        self.assertEqual(0, rc)
        self.assertEqual("ok", out)
        self.assertEqual("y\n", run.call_args.kwargs["input"])
        self.assertEqual("true", run.call_args.kwargs["env"]["NPM_CONFIG_YES"])
        self.assertEqual("true", run.call_args.kwargs["env"]["npm_config_yes"])
        self.assertEqual("1", run.call_args.kwargs["env"]["CI"])

    def test_claude_runtime_update_forces_npm_install_without_prompt(self):
        def find(name):
            return "npm" if name == "npm" else "claude"

        with (
            patch("ciel_runtime.find_executable", side_effect=find),
            patch("ciel_runtime.claude_code_current_version", return_value="1.0.0"),
            patch("ciel_runtime.npm_latest_package_version", return_value="2.0.0"),
            patch("ciel_runtime.version_newer", return_value=True),
            patch("ciel_runtime.current_npm_install_prefix", return_value=Path("/usr/local")),
            patch("ciel_runtime.run_command_for_upgrade", return_value=(0, "")) as run_cmd,
            patch("builtins.input") as input_mock,
            patch("builtins.print"),
        ):
            out = ciel_runtime.run_claude_update_check("claude")

        self.assertEqual("claude", out)
        self.assertEqual(
            ["npm", "install", "-g", "--prefer-online", "--prefix", str(Path("/usr/local")), "@anthropic-ai/claude-code@latest"],
            run_cmd.call_args.args[0],
        )
        input_mock.assert_not_called()

    def test_codex_missing_runtime_installs_latest_package(self):
        calls = []

        def find(name):
            calls.append(name)
            if name == "npm":
                return "npm"
            if name == "codex" and calls.count("codex") > 1:
                return "codex"
            return None

        with (
            patch("ciel_runtime.find_executable", side_effect=find),
            patch("ciel_runtime.current_npm_install_prefix", return_value=Path("/usr/local")),
            patch("ciel_runtime.run_command_for_upgrade", return_value=(0, "")) as run_cmd,
            patch("builtins.print"),
        ):
            out = ciel_runtime.install_codex_if_missing()

        self.assertEqual("codex", out)
        self.assertEqual(
            ["npm", "install", "-g", "--prefer-online", "--prefix", str(Path("/usr/local")), "@openai/codex@latest"],
            run_cmd.call_args.args[0],
        )

    def test_restart_after_update_prefers_npm_global_package_script(self):
        with tempfile.TemporaryDirectory() as td:
            package_root = Path(td)
            script = package_root / "ciel_runtime.py"
            script.write_text("print('new')\n", encoding="utf-8")
            with (
                patch.dict("os.environ", {}, clear=False),
                patch("ciel_runtime.sys.argv", ["ciel_runtime.py", "cli", "--ca-no-update-check"]),
                patch("ciel_runtime.npm_global_package_root", return_value=package_root),
                patch("ciel_runtime.os.execv", side_effect=RuntimeError("stop")) as execv,
            ):
                with self.assertRaises(RuntimeError):
                    ciel_runtime.restart_ciel_runtime_after_update("npm")

        self.assertEqual(
            [ciel_runtime.sys.executable, str(script), "cli", "--ca-no-update-check"],
            execv.call_args.args[1],
        )

    def test_configure_only_applies_setup_without_launching(self):
        with (
            patch("ciel_runtime.apply_headless_env_config", return_value=(False, None, None, None, False)),
            patch("ciel_runtime.cmd_provider") as provider,
            patch("ciel_runtime.cmd_model") as model,
            patch("ciel_runtime.launch_claude") as launch,
        ):
            rc = ciel_runtime.run_cli(["--ca-provider", "ollama-cloud", "--ca-model", "deepseek-v4-flash", "--ca-no-launch"])

        self.assertEqual(0, rc)
        provider.assert_called_once()
        model.assert_called_once()
        launch.assert_not_called()

    def test_configure_only_accepts_new_provider_flags(self):
        cases = (
            ("deepseek", "deepseek-v4-pro", "https://api.deepseek.com/anthropic"),
            ("opencode", "claude-sonnet-4-6", "https://opencode.ai/zen"),
            ("opencode-go", "qwen3.6-plus", "https://opencode.ai/zen/go"),
            ("zai", "glm-5.2[1m]", "https://api.z.ai/api/anthropic"),
        )
        for provider_name, model_name, base_url in cases:
            with self.subTest(provider=provider_name):
                with (
                    patch("ciel_runtime.apply_headless_env_config", return_value=(False, None, None, None, False)),
                    patch("ciel_runtime.cmd_provider") as provider,
                    patch("ciel_runtime.cmd_model") as model,
                    patch("ciel_runtime.cmd_base_url") as base,
                    patch("ciel_runtime.cmd_set_api_key") as api_key,
                    patch("ciel_runtime.launch_claude") as launch,
                ):
                    rc = ciel_runtime.run_cli(
                        [
                            "--ca-provider",
                            provider_name,
                            "--ca-base-url",
                            base_url,
                            "--ca-model",
                            model_name,
                            "--ca-api-key",
                            "sk-test",
                            "--ca-no-launch",
                        ]
                    )

                self.assertEqual(0, rc)
                self.assertEqual(provider_name, provider.call_args.args[0].name)
                self.assertEqual([model_name], model.call_args.args[0].value)
                self.assertEqual(base_url, base.call_args.args[0].url)
                self.assertEqual("sk-test", api_key.call_args.args[0].key)
                launch.assert_not_called()

    def test_provider_option_headless_applies_current_provider_option(self):
        with (
            patch("ciel_runtime.apply_headless_env_config", return_value=(False, None, None, None, False)),
            patch("ciel_runtime.cmd_provider_options") as provider_options,
            patch("ciel_runtime.launch_claude") as launch,
        ):
            rc = ciel_runtime.run_cli(["--ca-provider-option", "endpoint:custom-model=chat", "--ca-no-launch"])

        self.assertEqual(0, rc)
        self.assertEqual(["endpoint:custom-model=chat"], provider_options.call_args.args[0].values)
        launch.assert_not_called()

    def test_provider_option_headless_supports_explicit_provider(self):
        with (
            patch("ciel_runtime.apply_headless_env_config", return_value=(False, None, None, None, False)),
            patch("ciel_runtime.cmd_provider_options") as provider_options,
            patch("ciel_runtime.launch_claude") as launch,
        ):
            rc = ciel_runtime.run_cli(
                ["--ca-set-provider-option", "opencode-go", "endpoint:custom-model=chat", "--ca-no-launch"]
            )

        self.assertEqual(0, rc)
        self.assertEqual(["opencode-go", "endpoint:custom-model=chat"], provider_options.call_args.args[0].values)
        launch.assert_not_called()

    def test_configure_only_aliases_are_recognized(self):
        for flag in ("--ca-configure-only", "--ca-setup-only"):
            with self.subTest(flag=flag):
                with (
                    patch("ciel_runtime.apply_headless_env_config", return_value=(False, None, None, None, False)),
                    patch("ciel_runtime.launch_claude") as launch,
                ):
                    rc = ciel_runtime.run_cli([flag])

                self.assertEqual(0, rc)
                launch.assert_not_called()

    def test_auto_llm_options_uses_saved_model_when_no_model_is_given(self):
        with (
            patch("ciel_runtime.apply_headless_env_config", return_value=(False, None, None, None, False)),
            patch("ciel_runtime.apply_auto_llm_options_config", return_value=["ok"]) as auto_llm,
            patch("ciel_runtime.launch_claude") as launch,
            patch("builtins.print"),
        ):
            rc = ciel_runtime.run_cli(["--ca-auto-llm-options", "--ca-no-launch"])

        self.assertEqual(0, rc)
        auto_llm.assert_called_once_with(None)
        launch.assert_not_called()

    def test_auto_llm_options_accepts_model_argument(self):
        with (
            patch("ciel_runtime.apply_headless_env_config", return_value=(False, None, None, None, False)),
            patch("ciel_runtime.apply_auto_llm_options_config", return_value=["ok"]) as auto_llm,
            patch("ciel_runtime.launch_claude") as launch,
            patch("builtins.print"),
        ):
            rc = ciel_runtime.run_cli(["--ca-auto-llm-options", "deepseek-v4-flash", "--ca-no-launch"])

        self.assertEqual(0, rc)
        auto_llm.assert_called_once_with("deepseek-v4-flash")
        launch.assert_not_called()

    def test_auto_llm_options_equals_form_accepts_model_argument(self):
        with (
            patch("ciel_runtime.apply_headless_env_config", return_value=(False, None, None, None, False)),
            patch("ciel_runtime.apply_auto_llm_options_config", return_value=["ok"]) as auto_llm,
            patch("ciel_runtime.launch_claude") as launch,
            patch("builtins.print"),
        ):
            rc = ciel_runtime.run_cli(["--ca-auto-llm-options=deepseek-v4-pro", "--ca-no-launch"])

        self.assertEqual(0, rc)
        auto_llm.assert_called_once_with("deepseek-v4-pro")
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
