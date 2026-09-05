from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest import mock

import ciel_runtime
from ciel_runtime_support.architecture import LaunchSpec, ProviderConfig, RuntimeConfig
from ciel_runtime_support.muse_runtime_context import (
    MuseConfigurationPorts,
    MuseLifecyclePorts,
    MuseProcessPorts,
    MuseRuntimeContext,
)
from ciel_runtime_support.runtime_adapters import RUNTIME_ADAPTERS


class MuseRuntimeTests(unittest.TestCase):
    def test_adapter_builds_official_model_and_effort_flags_after_wsl_prefix(self):
        adapter = RUNTIME_ADAPTERS.create(
            "muse", executable="wsl.exe", environment={}, channel_injection=True
        )
        command = adapter.build_command(
            LaunchSpec(
                runtime=RuntimeConfig(
                    name="muse",
                    executable="wsl.exe",
                    enable_channels=True,
                    options={
                        "prefix_args": ("-e", "/home/test/.local/bin/muse"),
                        "yolo_args": ("--yolo",),
                        "model": "muse-spark-1.3",
                        "reasoning_effort": "high",
                    },
                ),
                provider=ProviderConfig(name="meta", base_url="", model=""),
                mode="native",
                protocol="native",
                passthrough=("--trust-workspace",),
            )
        )
        self.assertEqual(
            (
                "wsl.exe", "-e", "/home/test/.local/bin/muse",
                "--yolo",
                "--model", "muse-spark-1.3",
                "--reasoning-effort", "high",
                "--trust-workspace",
            ),
            command.argv,
        )

    def test_adapter_does_not_put_model_flags_before_utility_subcommands(self):
        adapter = RUNTIME_ADAPTERS.create(
            "muse", executable="muse", environment={}, channel_injection=True
        )
        command = adapter.build_command(
            LaunchSpec(
                runtime=RuntimeConfig(
                    name="muse",
                    executable="muse",
                    options={
                        "yolo_args": ("--yolo",),
                        "model": "muse-spark-1.3",
                        "reasoning_effort": "high",
                    },
                ),
                provider=ProviderConfig(name="meta", base_url="", model=""),
                mode="native",
                protocol="native",
                passthrough=("session-message", "--help"),
            )
        )
        self.assertEqual(("muse", "session-message", "--help"), command.argv)

    @staticmethod
    def context(captured: dict, *, platform_name: str = "posix", web: bool = True):
        def find(name: str):
            if name == "muse" and platform_name != "nt":
                return "/home/test/.local/bin/muse"
            if name in {"wsl", "wsl.exe"} and platform_name == "nt":
                return "C:/Windows/System32/wsl.exe"
            return None

        def run(command, **kwargs):
            captured.setdefault("runs", []).append((command, kwargs))
            if "command -v muse" in command:
                return SimpleNamespace(returncode=0, stdout="/home/test/.local/bin/muse\n")
            if 'wslpath -w "$HOME/.local/share/muse"' in command:
                return SimpleNamespace(
                    returncode=0,
                    stdout="\\\\wsl.localhost\\Ubuntu\\home\\test\\.local\\share\\muse\n",
                )
            return SimpleNamespace(returncode=0, stdout="")

        def materialize(runtime, executable, env, provider, provider_config, **kwargs):
            captured["materialize"] = (runtime, executable, dict(env), provider, provider_config, kwargs)
            prefix = list(kwargs["options"].get("prefix_args", ()))
            flags = list(kwargs["options"].get("yolo_args", ()))
            if kwargs["options"].get("model"):
                flags += ["--model", kwargs["options"]["model"]]
            if kwargs["options"].get("reasoning_effort"):
                flags += ["--reasoning-effort", kwargs["options"]["reasoning_effort"]]
            return [executable, *prefix, *flags, *kwargs["passthrough"]], dict(env)

        environment = {
            "PATH": "test-path",
            "META_API_KEY": "must-not-reach-muse",
            "MODEL_API_KEY": "must-not-reach-muse",
        }

        def start_router():
            captured["router_starts"] = captured.get("router_starts", 0) + 1
            return True

        def run_with_router(action, managed):
            captured["router_managed"] = managed
            return action()

        def proxy(command, env, **kwargs):
            captured["proxy"] = (command, dict(env), kwargs)
            return 0

        return MuseRuntimeContext(
            process=MuseProcessPorts(
                find, run,
                lambda command, **kwargs: captured.setdefault("calls", []).append((command, kwargs)) or 0,
                lambda *args, **kwargs: captured.setdefault("prints", []).append((args, kwargs)),
                environment,
                lambda _env: "augmented-path",
                platform_name,
            ),
            config=MuseConfigurationPorts(
                lambda: {
                    "current_provider": "meta",
                    "providers": {"meta": {"current_model": "muse-spark-1.3", "effort_level": "xhigh"}},
                },
                lambda config: ("meta", config["providers"]["meta"]),
            ),
            lifecycle=MuseLifecyclePorts(
                materialize,
                start_router,
                run_with_router,
                proxy,
                lambda _config: "native",
                lambda _config: web,
                lambda provider, model: captured.setdefault("launch", (provider, model)),
                lambda runtime, **kwargs: captured.setdefault(
                    "transcript_scope", (runtime, kwargs)
                ),
            ),
        )

    def test_native_launch_preserves_browser_subscription_and_uses_channel_proxy(self):
        captured: dict = {}
        context = self.context(captured)

        self.assertEqual(0, context.launch(["--trust-workspace"]))

        command, env, proxy_options = captured["proxy"]
        self.assertEqual("/home/test/.local/bin/muse", command[0])
        self.assertIn("muse-spark-1.3", command)
        self.assertIn("xhigh", command)
        self.assertIn("--yolo", command)
        self.assertNotIn("META_API_KEY", env)
        self.assertNotIn("MODEL_API_KEY", env)
        self.assertEqual("augmented-path", env["PATH"])
        self.assertFalse(proxy_options["channel_wake_confirm_submit"])
        self.assertEqual(1, proxy_options["channel_wake_submit_retries"])
        self.assertTrue(captured["router_managed"])
        self.assertEqual(("meta", "muse-spark-1.3"), captured["launch"])
        self.assertEqual("muse", captured["transcript_scope"][0])

    def test_windows_launch_uses_wsl_and_unsets_linux_api_key_environment(self):
        captured: dict = {}
        context = self.context(captured, platform_name="nt")

        discovered = context.discover()

        self.assertIsNotNone(discovered)
        assert discovered is not None
        self.assertEqual("wsl", discovered.platform)
        self.assertEqual(
            (
                "-e", "env", "-u", "META_API_KEY", "-u", "MODEL_API_KEY",
                "/home/test/.local/bin/muse",
            ),
            discovered.prefix_args,
        )
        self.assertEqual(
            "\\\\wsl.localhost\\Ubuntu\\home\\test\\.local\\share\\muse",
            str(discovered.transcript_root),
        )

    def test_effort_is_meta_only_and_maps_ciel_max_to_muse_ultra(self):
        self.assertEqual("", MuseRuntimeContext._effort("zai", {"effort_level": "max"}))
        self.assertEqual("ultra", MuseRuntimeContext._effort("meta", {"effort_level": "max"}))

    def test_explicit_echo_provider_suppresses_meta_model_options(self):
        captured: dict = {}
        context = self.context(captured)

        self.assertEqual(0, context.launch(["exec", "--provider", "echo", "hello"]))

        options = captured["materialize"][-1]["options"]
        self.assertEqual(("--yolo",), options["yolo_args"])
        self.assertNotIn("model", options)
        self.assertNotIn("reasoning_effort", options)
        self.assertNotIn("proxy", captured)

    def test_version_is_direct_and_does_not_start_router_or_channel_proxy(self):
        captured: dict = {}
        context = self.context(captured)

        self.assertEqual(0, context.launch(["--version"]))

        self.assertNotIn("proxy", captured)
        self.assertNotIn("router_starts", captured)
        self.assertEqual("--version", captured["calls"][0][0][-1])

    def test_explicit_yolo_is_not_duplicated(self):
        captured: dict = {}
        context = self.context(captured)

        self.assertEqual(0, context.launch(["--yolo", "--trust-workspace"]))

        command = captured["proxy"][0]
        self.assertEqual(1, command.count("--yolo"))

    def test_cli_and_launch_menu_expose_muse(self):
        rows, values = ciel_runtime.launch_panel_rows(
            {"current_provider": "meta", "providers": {"meta": {}}}
        )
        self.assertIn("Muse Code (subscription)", rows)
        self.assertIn("launch-muse", values)
        with (
            mock.patch.object(ciel_runtime, "apply_headless_env_config", return_value=(True, None, None, None, False)),
            mock.patch.object(ciel_runtime, "launch_muse", return_value=0) as launch_muse,
        ):
            self.assertEqual(0, ciel_runtime.run_cli(["--ca-runtime", "muse", "--", "--version"]))
        launch_muse.assert_called_once_with(
            ["--version"], skip_menu=True, force_menu=False,
            update_check=True, self_update_check=True,
        )


if __name__ == "__main__":
    unittest.main()
