import copy
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ciel_runtime
from ciel_runtime_support.architecture import ProviderRuntimeCompactionPolicy
from ciel_runtime_support.codex_launch_configuration import (
    CodexLaunchCatalogPorts,
    CodexLaunchConfigurationConstants,
    CodexLaunchConfigurationEffects,
    CodexLaunchConfigurationService,
    CodexLaunchModelPorts,
    CodexLaunchPolicyPorts,
)
from ciel_runtime_support.codex_launch_policy import (
    current_model_args,
    native_routed_config_args,
)


class CompactionLaunchContractTests(unittest.TestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(
            ciel_runtime,
            "terminate_existing_codex_processes_for_launch",
            return_value=False,
        )
        self.addCleanup(patcher.stop)
        patcher.start()

    @staticmethod
    def _unknown_context_native_service() -> CodexLaunchConfigurationService:
        return CodexLaunchConfigurationService(
            constants=CodexLaunchConfigurationConstants(
                runtime_provider_id="ciel-runtime",
                runtime_api_key_env="CIEL_KEY",
                native_provider_id_env="NATIVE_PROVIDER",
                routed_provider_id="ciel-codex",
                alternate_screen_key="tui.alternate_screen",
            ),
            policy=CodexLaunchPolicyPorts(
                has_option=lambda args, *names: any(name in args for name in names),
                config_override_keys=lambda _args: set(),
                config_paths=lambda *_args, **_kwargs: [],
                alternate_screen_value=lambda _text: None,
                toml_string=json.dumps,
            ),
            model=CodexLaunchModelPorts(
                current_provider=lambda cfg: (cfg["provider"], cfg["config"]),
                native_enabled=lambda _provider: True,
                current_alias=lambda cfg: cfg.get("alias", ""),
                context_window=lambda _provider, _config: None,
                compaction_policy=lambda _provider, _config: (
                    ProviderRuntimeCompactionPolicy()
                ),
            ),
            catalog=CodexLaunchCatalogPorts(
                write=lambda _codex, _spec, _env: None,
                provider_label=str.upper,
                path_value=lambda _env: "runtime-path",
                current_model_args=current_model_args,
                native_routed_args=native_routed_config_args,
            ),
            effects=CodexLaunchConfigurationEffects(
                environ=lambda: {},
                router_base=lambda: "http://router",
                read_text=lambda path: path.read_text(encoding="utf-8"),
                log=lambda _level, _message: None,
                output=lambda _message: None,
            ),
        )

    @staticmethod
    def _kimi_k3_config() -> tuple[dict, dict]:
        provider_config = copy.deepcopy(
            ciel_runtime.DEFAULT_CONFIG["providers"]["kimi"]
        )
        provider_config.update(
            {
                "api_key": "sk-kimi-test",
                "current_model": "k3",
            }
        )
        ciel_runtime.apply_kimi_model_profile("kimi", provider_config)
        config = {
            "current_provider": "kimi",
            "providers": {"kimi": provider_config},
        }
        return config, provider_config

    @staticmethod
    def _bundled_codex_models():
        return mock.Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "models": [
                        {
                            "slug": "gpt-5.2",
                            "display_name": "GPT-5.2",
                            "supported_reasoning_levels": [],
                        }
                    ]
                }
            ),
            stderr="",
        )

    @staticmethod
    def _capture_runner(captured):
        def run_with_router_lifetime(runner, manage_router):
            captured["manage_router"] = manage_router
            return runner()

        return run_with_router_lifetime

    @staticmethod
    def _capture_app_server(captured):
        def subprocess_call(command, environment, pid_path=None):
            captured["cmd"] = command
            captured["env"] = environment
            captured["pid_path"] = pid_path
            return 0

        return subprocess_call

    @staticmethod
    def _common_app_server_patches(
        stack: ExitStack,
        config: dict,
        provider: str,
        provider_config: dict,
        captured: dict,
    ) -> None:
        stack.enter_context(
            mock.patch.object(ciel_runtime, "warn_if_multiple_ciel_runtime_installs")
        )
        stack.enter_context(
            mock.patch.object(ciel_runtime, "run_ciel_runtime_update_check")
        )
        stack.enter_context(
            mock.patch.object(ciel_runtime, "run_prelaunch_menu", return_value=0)
        )
        stack.enter_context(
            mock.patch.object(
                ciel_runtime, "apply_launch_endpoint_policy", return_value=[]
            )
        )
        stack.enter_context(
            mock.patch.object(ciel_runtime, "load_config", return_value=config)
        )
        stack.enter_context(
            mock.patch.object(
                ciel_runtime,
                "get_current_provider",
                return_value=(provider, provider_config),
            )
        )
        stack.enter_context(
            mock.patch.object(ciel_runtime, "launch_readiness_errors", return_value=[])
        )
        stack.enter_context(
            mock.patch.object(
                ciel_runtime, "cleanup_managed_services_for_provider"
            )
        )
        stack.enter_context(
            mock.patch.object(
                ciel_runtime, "start_router_if_needed", return_value=True
            )
        )
        stack.enter_context(
            mock.patch.object(
                ciel_runtime, "install_codex_if_missing", return_value="codex"
            )
        )
        stack.enter_context(
            mock.patch.object(
                ciel_runtime, "run_codex_update_check", return_value="codex"
            )
        )
        stack.enter_context(
            mock.patch.object(ciel_runtime, "find_executable", return_value="codex")
        )
        stack.enter_context(
            mock.patch.object(
                ciel_runtime,
                "codex_app_server_default_listen_url",
                return_value="ws://127.0.0.1:8899",
            )
        )
        stack.enter_context(
            mock.patch.object(ciel_runtime, "record_launch_state_for_cwd")
        )
        stack.enter_context(
            mock.patch.object(
                ciel_runtime,
                "run_with_router_lifetime",
                side_effect=CompactionLaunchContractTests._capture_runner(captured),
            )
        )
        stack.enter_context(
            mock.patch.object(
                ciel_runtime,
                "subprocess_call_with_child_pid_record",
                side_effect=CompactionLaunchContractTests._capture_app_server(
                    captured
                ),
            )
        )

    def _launch_kimi_codex(self, passthrough: list[str]) -> tuple[int, dict]:
        config, provider_config = self._kimi_k3_config()
        captured = {}
        completed = self._bundled_codex_models()

        def subprocess_call(command, environment, **_kwargs):
            captured["cmd"] = command
            captured["env"] = environment
            return 0

        with tempfile.TemporaryDirectory() as temp_dir, ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(ciel_runtime, "CONFIG_DIR", Path(temp_dir))
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime.subprocess, "run", return_value=completed
                )
            )
            for name in (
                "warn_if_multiple_ciel_runtime_installs",
                "run_ciel_runtime_update_check",
                "cleanup_managed_services_for_provider",
                "ensure_model_cache_for_launch",
                "record_launch_state_for_cwd",
            ):
                stack.enter_context(mock.patch.object(ciel_runtime, name))
            stack.enter_context(
                mock.patch.object(ciel_runtime, "run_prelaunch_menu", return_value=0)
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime, "apply_launch_endpoint_policy", return_value=[]
                )
            )
            stack.enter_context(
                mock.patch.object(ciel_runtime, "load_config", return_value=config)
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime,
                    "get_current_provider",
                    return_value=("kimi", provider_config),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime, "launch_readiness_errors", return_value=[]
                )
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime, "start_router_if_needed", return_value=True
                )
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime, "install_codex_if_missing", return_value="codex"
                )
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime, "run_codex_update_check", return_value="codex"
                )
            )
            stack.enter_context(
                mock.patch.object(ciel_runtime, "find_executable", return_value="codex")
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime, "codex_alternate_screen_compat_args", return_value=[]
                )
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime,
                    "run_with_router_lifetime",
                    side_effect=self._capture_runner(captured),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime,
                    "subprocess_call_with_channel_wake_proxy",
                    side_effect=subprocess_call,
                )
            )

            result = ciel_runtime.launch_codex.delegate(
                passthrough,
                skip_menu=True,
            )
        return result, captured

    def test_native_explicit_threshold_is_not_clamped_when_context_is_unknown(self):
        service = self._unknown_context_native_service()
        config = {
            "provider": "codex",
            "alias": "gpt-explicit",
            "config": {"codex_auto_compact_window": 900_000},
        }

        args = service.runtime_model_catalog_args("codex", config)

        self.assertEqual(
            ["-c", "model_auto_compact_token_limit=900000"],
            args,
        )

    def test_routed_kimi_app_server_uses_immutable_k3_catalog(self):
        config, provider_config = self._kimi_k3_config()
        captured = {}
        completed = self._bundled_codex_models()

        with tempfile.TemporaryDirectory() as temp_dir, ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(ciel_runtime, "CONFIG_DIR", Path(temp_dir))
            )
            stack.enter_context(
                mock.patch.object(ciel_runtime.subprocess, "run", return_value=completed)
            )
            stack.enter_context(
                mock.patch.object(ciel_runtime, "ensure_model_cache_for_launch")
            )
            self._common_app_server_patches(
                stack, config, "kimi", provider_config, captured
            )

            result = ciel_runtime.launch_codex_app_server.delegate(
                [], skip_menu=True
            )

            catalog_argument = next(
                argument
                for argument in captured["cmd"]
                if str(argument).startswith("model_catalog_json=")
            )
            catalog_path = Path(
                json.loads(str(catalog_argument).split("=", 1)[1])
            )
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

        self.assertEqual(0, result)
        self.assertEqual("codex-model-catalogs", catalog_path.parent.name)
        self.assertEqual(64, len(catalog_path.stem))
        int(catalog_path.stem, 16)
        routed = next(
            model
            for model in catalog["models"]
            if model["slug"] == ciel_runtime.current_alias(config)
        )
        self.assertEqual(891_289, routed["auto_compact_token_limit"])

    def test_native_app_server_keeps_explicit_compaction_threshold(self):
        provider_config = {
            "route_through_router": False,
            "base_url": "https://api.openai.com",
            "current_model": "",
            "codex_auto_compact_window": 240_000,
            "context_window": 300_000,
        }
        config = {
            "current_provider": "codex",
            "providers": {"codex": provider_config},
        }
        captured = {}

        with ExitStack() as stack:
            self._common_app_server_patches(
                stack, config, "codex", provider_config, captured
            )

            result = ciel_runtime.launch_codex_app_server.delegate(
                [], skip_menu=True
            )

        self.assertEqual(0, result)
        self.assertIn(
            "model_auto_compact_token_limit=240000",
            captured["cmd"],
        )

    def test_codex_model_override_does_not_inherit_persisted_k3_policy(self):
        result, captured = self._launch_kimi_codex(
            ["--model", "kimi-for-coding", "exec", "hello"]
        )

        self.assertEqual(0, result)
        self.assertIn("kimi-for-coding", captured["cmd"])
        self.assertFalse(
            any(
                str(argument).startswith("model_catalog_json=")
                for argument in captured["cmd"]
            )
        )
        self.assertNotIn(
            "model_auto_compact_token_limit=891289",
            captured["cmd"],
        )

    def test_codex_config_model_override_is_the_only_launch_model(self):
        user_model_override = 'model="kimi-for-coding"'

        result, captured = self._launch_kimi_codex(
            ["-c", user_model_override, "exec", "hello"]
        )

        self.assertEqual(0, result)
        self.assertIn(user_model_override, captured["cmd"])
        self.assertNotIn("-m", captured["cmd"])
        self.assertNotIn(ciel_runtime.alias_for("kimi", "k3[1m]"), captured["cmd"])
        self.assertFalse(
            any(
                str(argument).startswith("model_catalog_json=")
                for argument in captured["cmd"]
            )
        )
        self.assertNotIn(
            "model_auto_compact_token_limit=891289",
            captured["cmd"],
        )

    def test_claude_model_override_defers_compaction_to_cli_defaults(self):
        config, provider_config = self._kimi_k3_config()

        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.dict("os.environ", {"PATH": "runtime-path"}, clear=True)
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime, "warn_if_multiple_ciel_runtime_installs"
                )
            )
            stack.enter_context(
                mock.patch.object(ciel_runtime, "run_ciel_runtime_update_check")
            )
            stack.enter_context(
                mock.patch.object(ciel_runtime, "run_prelaunch_menu", return_value=0)
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime, "apply_launch_endpoint_policy", return_value=[]
                )
            )
            stack.enter_context(
                mock.patch.object(ciel_runtime, "load_config", return_value=config)
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime,
                    "get_current_provider",
                    return_value=("kimi", provider_config),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime, "launch_readiness_errors", return_value=[]
                )
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime, "cleanup_managed_services_for_provider"
                )
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime, "start_router_if_needed", return_value=True
                )
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime,
                    "run_with_router_lifetime",
                    side_effect=lambda runner, _managed: runner(),
                )
            )
            stack.enter_context(
                mock.patch.object(ciel_runtime, "ensure_model_cache_for_launch")
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime, "upstream_model_ids", return_value=["k3"]
                )
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime,
                    "ensure_current_model_from_provider_list",
                    return_value=("k3", []),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime,
                    "find_executable",
                    return_value="runtime-claude",
                )
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime,
                    "install_claude_code_if_missing",
                    return_value="runtime-claude",
                )
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime,
                    "run_claude_update_check",
                    return_value="runtime-claude",
                )
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime,
                    "claude_supports_permission_mode_arg",
                    return_value=True,
                )
            )
            stack.enter_context(
                mock.patch.object(ciel_runtime, "install_ciel_runtime_slash_commands")
            )
            stack.enter_context(
                mock.patch.object(ciel_runtime, "install_tool_guard_hooks")
            )
            stack.enter_context(
                mock.patch.object(ciel_runtime, "install_ciel_runtime_statusline")
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime, "should_attach_web_search", return_value=False
                )
            )
            stack.enter_context(
                mock.patch.object(
                    ciel_runtime, "should_append_compat_prompt", return_value=False
                )
            )
            proxy = stack.enter_context(
                mock.patch.object(
                    ciel_runtime,
                    "subprocess_call_with_channel_wake_proxy",
                    return_value=0,
                )
            )
            direct_call = stack.enter_context(
                mock.patch.object(ciel_runtime.subprocess, "call", return_value=0)
            )

            result = ciel_runtime.launch_claude.delegate(
                ["--model", "kimi-for-coding"],
                skip_menu=True,
                update_check=False,
                self_update_check=False,
            )

        self.assertEqual(0, result)
        invocation = proxy if proxy.called else direct_call
        launch_command = invocation.call_args.args[0]
        launch_environment = (
            invocation.call_args.args[1]
            if proxy.called
            else invocation.call_args.kwargs["env"]
        )
        self.assertIn("kimi-for-coding", launch_command)
        for key in (
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
            "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
            "CLAUDE_CODE_EFFORT_LEVEL",
        ):
            self.assertNotIn(key, launch_environment)


if __name__ == "__main__":
    unittest.main()
