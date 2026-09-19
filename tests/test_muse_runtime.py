from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import ciel_runtime
from ciel_runtime_support.architecture import LaunchSpec, ProviderConfig, RuntimeConfig
from ciel_runtime_support.muse_runtime_context import (
    MUSE_STALE_LOCK_SWEEP,
    MuseConfigurationPorts,
    MuseLifecyclePorts,
    MuseProcessPorts,
    MuseRuntimeContext,
    WSL_PROBE_TIMEOUT_SECONDS,
    latest_tui_history_session,
    session_exists_script,
    tui_history_sessions,
    wsl_workspace_path,
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
    def context(
        captured: dict,
        *,
        platform_name: str = "posix",
        web: bool = True,
        router_mcp: bool | None = None,
    ):
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
            if kwargs["options"].get("provider"):
                flags += ["--provider", kwargs["options"]["provider"]]
            if kwargs["options"].get("base_url"):
                flags += ["--base-url", kwargs["options"]["base_url"]]
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
                    **(
                        {"muse": {"router_mcp": router_mcp}}
                        if router_mcp is not None
                        else {}
                    ),
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
                lambda provider, model, mode="": captured.setdefault("launch", (provider, model, mode)),
                lambda runtime, **kwargs: captured.setdefault(
                    "transcript_scope", (runtime, kwargs)
                ),
                lambda: (
                    captured.get("router_base", "http://127.0.0.1:9611"),
                    captured.get("router_token", "ciel-runtime-router-local-key"),
                ),
                lambda level, message: captured.setdefault("logs", []).append(
                    f"{level} {message}"
                ),
            ),
        )

    def launch_with_mcp_capture(self, context, captured, passthrough):
        """Launch while recording what the router MCP sync was asked to write."""

        entries: list[object] = []
        store = SimpleNamespace(
            sync=lambda entry, name=None: entries.append(entry) or "updated"
        )
        with mock.patch(
            "ciel_runtime_support.muse_runtime_context.settings_store_for",
            return_value=store,
        ):
            code = context.launch(passthrough)
        captured["mcp_entries"] = entries
        return code

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
        self.assertEqual(("meta", "muse-spark-1.3", ""), captured["launch"])
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

    def test_routed_launch_points_muse_at_the_router_responses_route(self):
        captured: dict = {}
        context = self.context(captured, web=False)

        self.assertEqual(0, context.launch(["--ca-router", "exec", "say hi"]))

        command = captured["calls"][0][0]
        env = captured["materialize"][2]
        self.assertNotIn("--ca-router", command)
        self.assertEqual(["exec", "say hi"], command[-2:])
        self.assertEqual(
            ["--base-url", "http://127.0.0.1:9611/v1"],
            command[command.index("--base-url") - 0:command.index("--base-url") + 2],
        )
        self.assertIn("--provider", command)
        self.assertEqual("meta", command[command.index("--provider") + 1])
        self.assertEqual("say hi", command[-1])
        # The router owns the Model API key; Muse receives the local placeholder.
        self.assertEqual("ciel-runtime-router-local-key", env["META_API_KEY"])
        self.assertEqual("ciel-runtime-router-local-key", env["MODEL_API_KEY"])
        self.assertEqual("routed", captured["materialize"][5]["mode"])
        self.assertEqual("native", captured["materialize"][5]["protocol"])
        self.assertEqual(
            "http://127.0.0.1:9611/v1", captured["materialize"][5]["options"]["base_url"]
        )
        # A headless routed run still needs the router for every model call.
        self.assertTrue(captured["router_managed"])
        self.assertEqual(1, captured["router_starts"])
        self.assertEqual(("meta", "muse-spark-1.3", "muse-router"), captured["launch"])

    def test_routed_launch_requires_the_meta_provider(self):
        captured: dict = {}
        context = self.context(captured)

        self.assertEqual(
            2, context.launch(["--ca-router", "exec", "--provider", "echo", "hello"])
        )

        self.assertNotIn("materialize", captured)
        self.assertIn("requires the meta provider", captured["prints"][0][0][0])

    def test_routed_wsl_launch_refuses_a_loopback_router(self):
        captured: dict = {}
        context = self.context(captured, platform_name="nt")

        result = context.launch(["--ca-router", "exec", "hi"])

        # The nt harness discovers Muse through WSL, so a loopback router base
        # must be refused with the fix instead of a connection error per call.
        self.assertEqual(2, result)
        self.assertNotIn("materialize", captured)
        self.assertIn("cannot reach the Ciel Router", captured["prints"][0][0][0])

    def test_routed_wsl_launch_uses_a_reachable_router_host(self):
        captured: dict = {"router_base": "http://172.29.112.1:9611"}
        context = self.context(captured, platform_name="nt")

        self.assertEqual(0, context.launch(["--ca-router", "exec", "hi"]))

        command = captured["calls"][0][0]
        self.assertIn("http://172.29.112.1:9611/v1", command)

    def test_routed_flag_is_stripped_from_native_launches(self):
        captured: dict = {}
        context = self.context(captured)

        self.assertEqual(0, context.launch(["exec", "hello"]))

        command = captured["calls"][0][0]
        env = captured["materialize"][2]
        self.assertNotIn("--base-url", command)
        self.assertNotIn("--provider", command)
        self.assertNotIn("META_API_KEY", env)
        self.assertNotIn("MODEL_API_KEY", env)
        self.assertFalse(captured["router_managed"])

    def test_adapter_places_router_flags_after_the_exec_subcommand(self):
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
                        "provider": "meta",
                        "base_url": "http://127.0.0.1:9611/v1",
                        "model": "muse-spark-1.3",
                    },
                ),
                provider=ProviderConfig(name="meta", base_url="", model=""),
                mode="routed",
                protocol="native",
                passthrough=("exec", "say hi"),
            )
        )

        self.assertEqual(
            (
                "muse", "exec", "--yolo", "--provider", "meta",
                "--base-url", "http://127.0.0.1:9611/v1",
                "--model", "muse-spark-1.3", "say hi",
            ),
            command.argv,
        )

    def test_continue_is_translated_into_the_resume_subcommand(self):
        captured: dict = {}
        context = self.context(captured)

        code = self.launch_with_mcp_capture(context, captured, ["--continue"])

        command = captured["proxy"][0]
        self.assertEqual(0, code)
        self.assertIn("resume", command)
        self.assertIn("--last", command)
        self.assertNotIn("--continue", command)
        self.assertTrue(
            any("muse_passthrough_mapping" in line for line in captured.get("logs", [])),
            captured.get("logs"),
        )

    def test_continue_with_an_existing_command_is_dropped(self):
        captured: dict = {}
        context = self.context(captured)

        self.launch_with_mcp_capture(context, captured, ["--continue", "exec", "hi"])

        command = captured["calls"][-1][0] if captured.get("calls") else captured["proxy"][0]
        self.assertIn("exec", command)
        self.assertNotIn("--continue", command)

    def test_native_launch_attaches_the_router_mcp_entry(self):
        captured: dict = {}
        context = self.context(captured)

        code = self.launch_with_mcp_capture(context, captured, ["--trust-workspace"])

        self.assertEqual(0, code)
        self.assertEqual(
            [
                {
                    "type": "streamable-http",
                    "url": "http://127.0.0.1:9611/ca/mcp",
                    "headers": {"Authorization": "Bearer ciel-runtime-router-local-key"},
                    "mode": "optional",
                }
            ],
            captured["mcp_entries"],
        )

    def test_wsl_launch_skips_the_router_mcp_for_a_loopback_router(self):
        captured: dict = {}
        context = self.context(captured, platform_name="nt")

        code = self.launch_with_mcp_capture(context, captured, ["--trust-workspace"])

        self.assertEqual(0, code)
        self.assertEqual([None], captured["mcp_entries"])
        self.assertTrue(
            any("WSL cannot reach" in line for line in captured.get("logs", [])),
            captured.get("logs"),
        )

    def test_wsl_launch_attaches_when_the_router_host_is_reachable(self):
        captured: dict = {
            "router_base": "http://172.29.112.1:9491",
            "router_token": "external-token",
        }
        context = self.context(captured, platform_name="nt")

        self.launch_with_mcp_capture(context, captured, ["--trust-workspace"])

        self.assertEqual(
            "http://172.29.112.1:9491/ca/mcp",
            captured["mcp_entries"][0]["url"],
        )
        self.assertEqual(
            {"Authorization": "Bearer external-token"},
            captured["mcp_entries"][0]["headers"],
        )

    def test_router_mcp_can_be_disabled_by_config(self):
        captured: dict = {}
        context = self.context(captured, router_mcp=False)

        self.launch_with_mcp_capture(context, captured, ["--trust-workspace"])

        self.assertEqual([None], captured["mcp_entries"])
        self.assertTrue(
            any("muse.router_mcp" in line for line in captured.get("logs", [])),
            captured.get("logs"),
        )

    def test_utility_command_does_not_touch_the_muse_settings(self):
        captured: dict = {}
        context = self.context(captured)

        self.launch_with_mcp_capture(context, captured, ["--version"])

        self.assertEqual([], captured["mcp_entries"])

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


class MuseWslProbeDeadlineTests(unittest.TestCase):
    """A wedged WSL must fail a Muse launch fast instead of freezing it.

    Live 2026-09-19: `wsl -e sh -lc "command -v muse"` from F:\\aap.ezonebot
    never returned while the F: 9p mount was wedged; the launch sat at that
    probe for 46 minutes with no log output. Probes now run from the user's
    home directory (no cwd translation) under WSL_PROBE_TIMEOUT_SECONDS.
    """

    @staticmethod
    def hardened(captured, run):
        base = MuseRuntimeTests.context(captured, platform_name="nt")
        return dataclasses.replace(
            base, process=dataclasses.replace(base.process, run=run)
        )

    def test_discovery_probe_runs_from_home_under_a_deadline(self):
        captured = {}
        context = MuseRuntimeTests.context(captured, platform_name="nt")

        executable = context.discover()

        self.assertIsNotNone(executable)
        command, kwargs = captured["runs"][0]
        self.assertEqual("command -v muse", command[-1])
        self.assertEqual(WSL_PROBE_TIMEOUT_SECONDS, kwargs["timeout"])
        self.assertTrue(kwargs["cwd"])

    def test_install_is_skipped_with_a_message_when_wsl_is_wedged(self):
        captured = {}

        def run(command, **kwargs):
            captured.setdefault("runs", []).append((command, kwargs))
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout") or 0.0)

        context = self.hardened(captured, run)

        self.assertIsNone(context.install_if_missing())

        printed = " ".join(str(entry) for entry in captured.get("prints", []))
        self.assertIn("WSL did not answer", printed)
        self.assertFalse(
            any("install.sh" in " ".join(command) for command, _ in captured["runs"])
        )

    @unittest.skipUnless(sys.platform == "win32", "needs a Windows cwd drive")
    def test_launch_stops_with_a_message_when_the_workspace_cwd_hangs(self):
        captured = {}

        def run(command, **kwargs):
            captured.setdefault("runs", []).append((command, kwargs))
            if "--cd" in command:
                raise subprocess.TimeoutExpired(command, kwargs.get("timeout") or 0.0)
            if "command -v muse" in command:
                return SimpleNamespace(
                    returncode=0, stdout="/home/test/.local/bin/muse\n"
                )
            if 'wslpath -w "$HOME/.local/share/muse"' in command:
                return SimpleNamespace(
                    returncode=0,
                    stdout="\\\\wsl.localhost\\Ubuntu\\home\\test\\.local\\share\\muse\n",
                )
            return SimpleNamespace(returncode=0, stdout="")

        context = self.hardened(captured, run)

        self.assertEqual(2, context.launch(["--continue"]))

        printed = " ".join(str(entry) for entry in captured.get("prints", []))
        self.assertIn("WSL did not answer", printed)
        self.assertNotIn("proxy", captured)


class MuseContinueResolutionTests(unittest.TestCase):
    """`--continue` must resume the session the user actually used last.

    Muse resolves `resume --last` through its session index; live 2026-09-19 a
    real F:\\omini-router session was indexed as `missing_metadata`, so
    `--last` started an empty session while `resume <id>` restored it. The TUI
    history (project -> last session) decides instead.
    """

    SESSION = "01a0b860-89c9-7110-a9a6-2c8b63fba897"

    def test_latest_history_entry_for_the_workspace_wins(self):
        history = "\n".join(
            [
                json.dumps({"project": "/mnt/f/other", "session": self.SESSION}),
                "some prompt text",
                json.dumps({"project": "/mnt/f/omini-router", "session": "01a0b860-89c9-7110-a9a6-2c8b63fba897"}),
                json.dumps({"project": "/mnt/f/omini-router", "session": "01a0bb5d-cbe8-7082-8bff-c12a73f45c78"}),
                json.dumps({"project": "/mnt/f/omini-router", "session": "not-a-uuid"}),
            ]
        )

        self.assertEqual(
            "01a0bb5d-cbe8-7082-8bff-c12a73f45c78",
            latest_tui_history_session(history, "/mnt/f/omini-router"),
        )
        self.assertEqual("", latest_tui_history_session(history, "/mnt/f/absent"))

    def test_windows_cwd_maps_to_the_wsl_path(self):
        self.assertEqual(
            "/mnt/f/omini-router", wsl_workspace_path(Path(r"F:\omini-router"))
        )
        self.assertEqual("", wsl_workspace_path(Path("/home/test")))

    def test_launch_rewrites_continue_to_the_history_session(self):
        captured = {}
        session = self.SESSION
        base = MuseRuntimeTests.context(captured, platform_name="nt")
        workspace = wsl_workspace_path(Path.cwd())

        def run(command, **kwargs):
            captured.setdefault("runs", []).append((command, kwargs))
            if "tui-history.jsonl" in " ".join(command):
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({"project": workspace, "session": session}),
                )
            if "command -v muse" in command:
                return SimpleNamespace(
                    returncode=0, stdout="/home/test/.local/bin/muse\n"
                )
            if 'wslpath -w "$HOME/.local/share/muse"' in command:
                return SimpleNamespace(
                    returncode=0,
                    stdout="\\\\wsl.localhost\\Ubuntu\\home\\test\\.local\\share\\muse\n",
                )
            return SimpleNamespace(returncode=0, stdout="")

        context = dataclasses.replace(
            base, process=dataclasses.replace(base.process, run=run)
        )

        self.assertEqual(0, context.launch(["--continue"]))

        passthrough = captured["materialize"][5]["passthrough"]
        self.assertEqual(["resume", session], list(passthrough))

    def test_launch_keeps_last_when_the_history_has_nothing(self):
        captured = {}
        base = MuseRuntimeTests.context(captured, platform_name="nt")

        context = base
        self.assertEqual(0, context.launch(["--continue"]))

        passthrough = captured["materialize"][5]["passthrough"]
        self.assertEqual(["resume", "--last"], list(passthrough))

    def test_history_sessions_come_back_newest_first_without_duplicates(self):
        older = "01a0b860-89c9-7110-a9a6-2c8b63fba897"
        newer = "01a0bb7e-4f99-7bc1-aaf4-7d9eddaacdfa"
        history = "\n".join(
            [
                json.dumps({"project": "/mnt/f/omini-router", "session": older}),
                json.dumps({"project": "/mnt/f/omini-router", "session": older}),
                json.dumps({"project": "/mnt/f/other", "session": newer}),
                json.dumps({"project": "/mnt/f/omini-router", "session": newer}),
                json.dumps({"project": "/mnt/f/omini-router", "session": "junk"}),
            ]
        )

        self.assertEqual(
            [newer, older], tui_history_sessions(history, "/mnt/f/omini-router")
        )
        self.assertEqual([], tui_history_sessions(history, "/mnt/f/absent"))

    def test_existence_probe_script_checks_candidate_directories(self):
        script = session_exists_script(["aaaa", "bbbb"])

        self.assertIn("for id in aaaa bbbb", script)
        self.assertIn(".local/share/muse/sessions/*/*/*/", script)
        self.assertIn('echo "$id"', script)

    def test_picker_launch_resolves_to_an_existing_history_session(self):
        captured = {}
        dead = "01a0bb7e-4f99-7bc1-aaf4-7d9eddaacdfa"
        alive = self.SESSION
        base = MuseRuntimeTests.context(captured, platform_name="nt")
        workspace = wsl_workspace_path(Path.cwd())

        def run(command, **kwargs):
            captured.setdefault("runs", []).append((command, kwargs))
            joined = " ".join(command)
            if "tui-history.jsonl" in joined:
                return SimpleNamespace(
                    returncode=0,
                    stdout="\n".join(
                        [
                            json.dumps({"project": workspace, "session": alive}),
                            json.dumps({"project": workspace, "session": dead}),
                        ]
                    ),
                )
            if "command -v muse" in joined:
                return SimpleNamespace(
                    returncode=0, stdout="/home/test/.local/bin/muse\n"
                )
            if 'wslpath -w "$HOME/.local/share/muse"' in joined:
                return SimpleNamespace(
                    returncode=0,
                    stdout="\\\\wsl.localhost\\Ubuntu\\home\\test\\.local\\share\\muse\n",
                )
            if "for id in " in joined:
                # The newest candidate (dead) has no directory; the older one
                # does, so the probe answers with it.
                return SimpleNamespace(returncode=0, stdout=f"{alive}\n")
            return SimpleNamespace(returncode=0, stdout="")

        context = dataclasses.replace(
            base, process=dataclasses.replace(base.process, run=run)
        )

        self.assertEqual(0, context.launch(["resume"]))

        passthrough = captured["materialize"][5]["passthrough"]
        self.assertEqual(["resume", alive], list(passthrough))
        probe = [
            command
            for command, _ in captured["runs"]
            if "for id in " in " ".join(command)
        ]
        self.assertTrue(probe, "existence probe did not run")
        self.assertIn(dead, probe[0][-1])
        self.assertIn(alive, probe[0][-1])

    def test_resume_with_an_explicit_reference_is_left_alone(self):
        captured = {}
        base = MuseRuntimeTests.context(captured, platform_name="nt")

        self.assertEqual(0, base.launch(["resume", "brown-procyon"]))

        passthrough = captured["materialize"][5]["passthrough"]
        self.assertEqual(["resume", "brown-procyon"], list(passthrough))
        self.assertFalse(
            any(
                "tui-history.jsonl" in " ".join(command)
                for command, _ in captured.get("runs", [])
            )
        )

    def test_resume_falls_back_to_the_newest_history_session(self):
        captured = {}
        base = MuseRuntimeTests.context(captured, platform_name="nt")
        workspace = wsl_workspace_path(Path.cwd())

        def run(command, **kwargs):
            captured.setdefault("runs", []).append((command, kwargs))
            joined = " ".join(command)
            if "tui-history.jsonl" in joined:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({"project": workspace, "session": self.SESSION}),
                )
            if "command -v muse" in joined:
                return SimpleNamespace(
                    returncode=0, stdout="/home/test/.local/bin/muse\n"
                )
            if 'wslpath -w "$HOME/.local/share/muse"' in joined:
                return SimpleNamespace(
                    returncode=0,
                    stdout="\\\\wsl.localhost\\Ubuntu\\home\\test\\.local\\share\\muse\n",
                )
            return SimpleNamespace(returncode=0, stdout="")

        context = dataclasses.replace(
            base, process=dataclasses.replace(base.process, run=run)
        )

        self.assertEqual(0, context.launch(["--resume"]))

        passthrough = captured["materialize"][5]["passthrough"]
        self.assertEqual(["resume", self.SESSION], list(passthrough))

    def test_stale_lock_sweep_runs_for_resume_launches_only(self):
        captured = {}
        base = MuseRuntimeTests.context(captured, platform_name="nt")

        self.assertEqual(0, base.launch(["--continue"]))

        sweeps = [
            command
            for command, _ in captured.get("runs", [])
            if command and command[-1] == MUSE_STALE_LOCK_SWEEP
        ]
        self.assertEqual(1, len(sweeps))
        logs = " ".join(str(entry) for entry in captured.get("logs", []))
        self.assertIn("muse_stale_lock_sweep", logs)

        captured.clear()
        self.assertEqual(0, base.launch(["exec", "hi"]))

        sweeps = [
            command
            for command, _ in captured.get("runs", [])
            if command and command[-1] == MUSE_STALE_LOCK_SWEEP
        ]
        self.assertEqual([], sweeps)


if __name__ == "__main__":
    unittest.main()
