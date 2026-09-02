import json
import unittest

from ciel_runtime_support import runtime_launch


class ClaudeSessionSocketLaunchTests(unittest.TestCase):
    def test_interactive_claude_launch_adds_and_activates_session_socket(self):
        target = r"\\.\pipe\LOCAL\cc-msg-0123456789abcdef0123456789abcdef"
        configured: list[str | None] = []
        launched: list[list[str]] = []
        settings_configs: list[dict] = []

        def has_option(args, *options):
            return any(
                str(value) == option or str(value).startswith(option + "=")
                for value in args
                for option in options
            )

        def append_settings(extra_args, passthrough, _provider, config):
            settings_configs.append(dict(config))
            if not has_option(passthrough, "--settings"):
                extra_args.extend(
                    ["--settings", json.dumps({"crossSessionInbound": "accept"})]
                )

        def materialize(_runtime, executable, env, _provider, _config, **kwargs):
            return [executable, *kwargs["options"]["extra_args"]], env

        def call_with_wake(command, _env, **_kwargs):
            launched.append(command)
            return 0

        def no_op(*_args, **_kwargs):
            return None

        services = runtime_launch.ClaudeLaunchServices(
            constants=runtime_launch.build_default_claude_launch_constants(),
            process=runtime_launch.ClaudeLaunchProcess(
                no_op,
                lambda *_args, **_kwargs: 0,
                lambda *_args, **_kwargs: False,
                lambda _config: {
                    "ANTHROPIC_AUTH_TOKEN": "test-token",
                    "CIEL_RUNTIME_MODEL_ALIAS": "test-model",
                },
                lambda _path: 0,
                lambda env: env.get("PATH", ""),
                no_op,
                call_with_wake,
                lambda *_args, **_kwargs: 0,
            ),
            installation=runtime_launch.ClaudeLaunchInstallation(
                lambda _name: "claude",
                no_op,
                no_op,
                lambda: "claude",
                no_op,
                no_op,
                lambda _config: [],
                no_op,
            ),
            dispatch=runtime_launch.ClaudeLaunchDispatch(
                lambda *_args, **_kwargs: 0,
                lambda *_args, **_kwargs: 0,
                lambda *_args, **_kwargs: 0,
                materialize,
                no_op,
                lambda executable, enabled=True: executable,
                lambda *_args, **_kwargs: 0,
                lambda _provider: True,
            ),
            config=runtime_launch.ClaudeLaunchConfig(
                lambda: {"provider": "test"},
                no_op,
                lambda _config: ("test", {"current_model": "test-model"}),
                lambda _provider, _config: (True, []),
                no_op,
                lambda _config, _runtime: [],
                lambda provider, _config: provider,
                lambda *_args: "routed",
                lambda: "cwd-key",
            ),
            routing=runtime_launch.ClaudeLaunchRouting(
                lambda *_args: False,
                lambda *_args: False,
                no_op,
                lambda: True,
                no_op,
                lambda: "healthy",
                no_op,
                lambda **_kwargs: False,
                lambda callback, _managed: callback(),
                no_op,
            ),
            policy=runtime_launch.ClaudeLaunchPolicy(
                append_settings,
                lambda _executable: False,
                lambda _args: False,
                has_option,
                lambda *_args: False,
                lambda *_args: False,
                lambda *_args: False,
                lambda *_args: (False, ""),
                lambda *_args: False,
            ),
            channel_delivery=runtime_launch.ClaudeLaunchChannelDelivery(
                lambda *_args: True,
                lambda *_args: True,
                lambda: 0.0,
                lambda: 1,
                no_op,
                lambda _config, _args: target,
                configured.append,
            ),
            mcp_config=runtime_launch.ClaudeLaunchMcpConfig(no_op, no_op),
        )

        result = runtime_launch.run_claude(
            [],
            skip_menu=True,
            update_check=False,
            self_update_check=False,
            services=services,
        )

        self.assertEqual(0, result)
        self.assertEqual([target, None], configured)
        self.assertTrue(settings_configs[0]["_ciel_session_socket_enabled"])
        self.assertIn("--messaging-socket-path", launched[0])
        self.assertEqual(target, launched[0][launched[0].index("--messaging-socket-path") + 1])
        self.assertIn("--settings", launched[0])


if __name__ == "__main__":
    unittest.main()
