import unittest
from types import SimpleNamespace

from ciel_runtime_support.architecture import ProviderRuntimeCompactionPolicy
from ciel_runtime_support.claude_environment import (
    CLAUDE_PROJECTED_ENV_KEYS,
    ClaudeEnvironmentFeaturePorts,
    ClaudeEnvironmentProjection,
    ClaudeEnvironmentShellRenderer,
    ClaudeEnvironmentSourcePorts,
    ClaudeLimitPolicy,
    ClaudeLimitPorts,
    ClaudeModelAliasPolicy,
    ClaudeModelPorts,
    ClaudeRuntimeSettingsPolicy,
    ClaudeRuntimeSettingsPorts,
)


class ClaudeEnvironmentPolicyTests(unittest.TestCase):
    def test_limit_policy_caps_output_and_compact_windows(self):
        policy = ClaudeLimitPolicy(
            ClaudeLimitPorts(
                positive_int=lambda value: int(value) if value else None,
                cap_output_tokens=lambda _provider, _config, value: min(value, 512),
                ollama_options=lambda config: config.get("options", {}),
                context_limit=lambda _provider, _config: 4096,
                compaction_policy=lambda _provider, _config: ProviderRuntimeCompactionPolicy(),
            )
        )
        self.assertEqual(512, policy.output_token_limit("openai", {"max_output_tokens": 1024}))
        self.assertIsNone(
            policy.output_token_limit(
                "ollama", {"options": {"num_predict": 768}}
            )
        )
        self.assertEqual(
            512,
            policy.output_token_limit(
                "ollama",
                {
                    "options": {"num_predict": 768},
                    "output_tokens_explicit": True,
                },
            ),
        )
        self.assertEqual(4096, policy.auto_compact_window("openai", {}))
        self.assertEqual(2048, policy.auto_compact_window("openai", {"auto_compact_window": 2048}))

    def test_model_alias_policy_marks_only_million_context_models(self):
        policy = self._model_policy()
        self.assertEqual(
            "ciel-runtime-test-million[1m]",
            policy.context_model_alias("test", {}, "ciel-runtime-test-million"),
        )
        self.assertEqual("regular", policy.context_model_alias("test", {}, "regular"))
        self.assertTrue(policy.matches_family("claude-opus-4-1", "opus"))
        self.assertFalse(policy.matches_family("claude-sonnet-4-1", "opus"))

    def test_provider_compaction_percent_does_not_reduce_model_context(self):
        policy = ClaudeLimitPolicy(
            ClaudeLimitPorts(
                positive_int=lambda value: int(value) if value else None,
                cap_output_tokens=lambda _provider, _config, value: value,
                ollama_options=lambda _config: {},
                context_limit=lambda _provider, _config: 1_048_576,
                compaction_policy=lambda _provider, _config: ProviderRuntimeCompactionPolicy(
                    trigger_percent=85
                ),
            )
        )

        snapshot = policy.compaction_snapshot("test", {})

        self.assertEqual(1_048_576, snapshot.context_window)
        self.assertEqual(1_000_000, snapshot.auto_compact_window)
        self.assertEqual(89, snapshot.auto_compact_percent)

    def _guarded_policy(self, budget):
        return ClaudeLimitPolicy(
            ClaudeLimitPorts(
                positive_int=lambda value: int(value) if value else None,
                cap_output_tokens=lambda _provider, _config, value: value,
                ollama_options=lambda _config: {},
                context_limit=lambda _provider, _config: 872_000,
                compaction_policy=lambda _provider, _config: ProviderRuntimeCompactionPolicy(),
                guarded_input_budget=lambda _provider, _config: budget,
            )
        )

    def test_routed_launch_pins_the_trigger_at_the_forwarded_budget(self):
        policy = self._guarded_policy(716_750)

        snapshot = policy.compaction_snapshot("cielairouter", {})

        self.assertEqual(872_000, snapshot.auto_compact_window)
        self.assertEqual(82, snapshot.auto_compact_percent)
        self.assertLessEqual(
            872_000 * snapshot.auto_compact_percent // 100, 716_750
        )
        # A direct data path bypasses the router, so nothing is trimmed.
        self.assertIsNone(policy.compaction_snapshot("cielairouter", {}, guarded=False).auto_compact_percent)
        # The window itself is the model's real context; only the trigger moves.
        self.assertEqual(872_000, policy.auto_compact_window("cielairouter", {}, guarded=True))
        self.assertEqual(872_000, policy.auto_compact_window("cielairouter", {}))

    def test_guarded_percent_is_only_added_below_the_window_and_never_raised(self):
        policy = self._guarded_policy(716_750)
        self.assertIsNone(
            policy.compaction_snapshot("x", {"auto_compact_window": 400_000}).auto_compact_percent
        )
        raised = policy.compaction_snapshot("x", {"auto_compact_window": 900_000})
        self.assertEqual(872_000, raised.auto_compact_window)  # capped to the model limit
        self.assertEqual(82, raised.auto_compact_percent)
        configured = ClaudeLimitPolicy(
            ClaudeLimitPorts(
                positive_int=lambda value: int(value) if value else None,
                cap_output_tokens=lambda _provider, _config, value: value,
                ollama_options=lambda _config: {},
                context_limit=lambda _provider, _config: 1_048_576,
                compaction_policy=lambda _provider, _config: ProviderRuntimeCompactionPolicy(trigger_percent=60),
                guarded_input_budget=lambda _provider, _config: 716_750,
            )
        )
        # 60% of 1,048,576 rescaled to the 1,000,000 window is 62; the guard is
        # looser than that, so the configured trigger wins.
        self.assertEqual(62, configured.compaction_snapshot("x", {}).auto_compact_percent)

    def test_native_projection_only_sets_marker_key_and_chrome_floor_off(self):
        projection = self._projection(native=True, key="secret")
        self.assertEqual(
            {
                "CIEL_RUNTIME_PROVIDER": "anthropic",
                "ANTHROPIC_API_KEY": "secret",
                "CLAUDE_CHROME_CLASSIFIER_FLOOR": "0",
            },
            projection.build({"provider": "anthropic"}),
        )

    def test_routed_projection_builds_gateway_models_and_common_features(self):
        projection = self._projection(native=False, key="")
        env = projection.build({"provider": "test"})
        self.assertEqual("http://router", env["ANTHROPIC_BASE_URL"])
        self.assertEqual("current", env["ANTHROPIC_MODEL"])
        self.assertEqual("token", env["ANTHROPIC_AUTH_TOKEN"])
        self.assertEqual("1024", env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"])
        self.assertEqual("1", env["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"])
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", env)

    def test_cielairouter_routed_launch_leaves_retries_to_the_router(self):
        projection = self._projection(native=False, key="")
        for limit in (None, 0, 3, 10):
            with self.subTest(limit=limit):
                env = projection.build({"provider": "cielairouter", "gateway_retries": limit})
                self.assertEqual("0", env["CLAUDE_CODE_MAX_RETRIES"])
                self.assertEqual("0", env["CLAUDE_CODE_RETRY_WATCHDOG"])
                self.assertEqual("0", env["CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES"])
                self.assertEqual("1", env["CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK"])

    def test_cielairouter_direct_launch_uses_shared_bounded_retry_limit(self):
        projection = self._projection(native=False, key="")
        route = SimpleNamespace(direct=True, endpoint="http://gateway", api_key="fixture-key")
        for limit, expected in ((None, "3"), (0, "0"), (2, "2"), ("5", "5"),
                                (100, "10"), (-1, "0"), (True, "3"), (1.5, "3"),
                                ("invalid", "3")):
            with self.subTest(limit=limit):
                env = projection.build(
                    {"provider": "cielairouter", "gateway_retries": limit}, data_path=route
                )
                self.assertEqual(expected, env["CLAUDE_CODE_MAX_RETRIES"])
                self.assertEqual("0", env["CLAUDE_CODE_RETRY_WATCHDOG"])
                self.assertEqual("0", env["CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES"])
                self.assertEqual("1", env["CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK"])
                self.assertEqual("http://gateway", env["ANTHROPIC_BASE_URL"])

    def test_retry_projection_cleans_inherited_and_restarted_environment(self):
        projection = self._projection(native=False, key="")
        route = SimpleNamespace(direct=True, endpoint="http://gateway", api_key="fixture-key")
        keys = ("CLAUDE_CODE_MAX_RETRIES", "CLAUDE_CODE_RETRY_WATCHDOG",
                "CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES", "CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK")
        env = dict.fromkeys(keys, "inherited")
        for provider, data_path, expected in (
            ("cielairouter", route, "3"), ("cielairouter", None, "0"),
            ("test", None, None), ("test", route, None),
        ):
            with self.subTest(provider=provider, direct=data_path is not None):
                snapshot = projection.build({"provider": provider}, data_path=data_path)
                # runtime_launch uses this shared list before each initial/restart snapshot.
                for key in CLAUDE_PROJECTED_ENV_KEYS:
                    env.pop(key, None)
                env.update(snapshot)
                self.assertEqual(expected, env.get("CLAUDE_CODE_MAX_RETRIES"))
                shell = ClaudeEnvironmentShellRenderer.lines(snapshot)
                for key in keys:
                    self.assertIn(key, CLAUDE_PROJECTED_ENV_KEYS)
                    if expected is None:
                        self.assertNotIn(key, env)
                        self.assertIn(f"unset {key}", shell)
                    else:
                        self.assertIn(f'export {key}="{snapshot[key]}"', shell)

    def test_retry_settings_do_not_change_other_providers_or_native_anthropic(self):
        keys = {"CLAUDE_CODE_MAX_RETRIES", "CLAUDE_CODE_RETRY_WATCHDOG",
                "CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES", "CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK"}
        route = SimpleNamespace(direct=True, endpoint="http://gateway", api_key="fixture-key")
        for provider, native, data_path in (
            ("anthropic", True, None), ("anthropic", False, None),
            ("test", False, route), ("ollama", False, None),
        ):
            with self.subTest(provider=provider, native=native):
                env = self._projection(native=native, key="").build(
                    {"provider": provider, "gateway_retries": 2}, data_path=data_path
                )
                self.assertFalse(keys.intersection(env))

    def test_chrome_classifier_floor_is_disabled_for_every_claude_launch(self):
        # Claude Code 2.1.278 re-routes Claude-in-Chrome MCP tools to its
        # auto-mode classifier even under bypassPermissions unless this triBool
        # env var disables the floor; ciel-runtime always launches Claude Code
        # with --dangerously-skip-permissions, so every projection must carry it.
        for native in (True, False):
            env = self._projection(native=native, key="").build({"provider": "anthropic"})
            self.assertEqual(
                "0",
                env["CLAUDE_CHROME_CLASSIFIER_FLOOR"],
                f"native={native}",
            )

    def test_configured_subagent_model_is_routed_and_forced(self):
        projection = self._projection(native=False, key="")
        env = projection.build(
            {"provider": "test", "subagent_model": "worker-model"}
        )

        self.assertEqual("worker-model", env["CLAUDE_CODE_SUBAGENT_MODEL"])
        self.assertEqual("1", env["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"])

    def test_runtime_settings_respect_explicit_passthrough(self):
        messages = []
        policy = ClaudeRuntimeSettingsPolicy(
            ClaudeRuntimeSettingsPorts(
                ultracode_enabled=lambda _provider, _config: True,
                has_passthrough_option=lambda args, option: option in args,
                log=lambda level, message: messages.append((level, message)),
            )
        )
        extra_args = []
        policy.append_args(extra_args, [], "test", {})
        self.assertEqual(["--settings", '{"ultracode":true}'], extra_args)
        policy.append_args(extra_args, ["--settings", "{}"], "test", {})
        self.assertEqual("WARN", messages[0][0])

    def test_runtime_settings_accept_cross_session_input_for_ciel_socket(self):
        policy = ClaudeRuntimeSettingsPolicy(
            ClaudeRuntimeSettingsPorts(
                ultracode_enabled=lambda _provider, _config: False,
                has_passthrough_option=lambda args, option: option in args,
                log=lambda _level, _message: None,
            )
        )

        self.assertEqual(
            {"crossSessionInbound": "accept"},
            policy.settings("test", {"_ciel_session_socket_enabled": True}),
        )

    def test_shell_renderer_quotes_values_and_unsets_missing_keys(self):
        lines = ClaudeEnvironmentShellRenderer.lines({"ANTHROPIC_BASE_URL": "http://router"})
        self.assertEqual('export ANTHROPIC_BASE_URL="http://router"', lines[0])
        self.assertIn("unset ANTHROPIC_API_KEY", lines)
        self.assertIn("unset CLAUDE_CODE_SUBAGENT_MODEL_FORCE", lines)
        self.assertIn("unset CIEL_RUNTIME_PROVIDER", lines)
        self.assertIn("unset CLAUDE_CHROME_CLASSIFIER_FLOOR", lines)

    @staticmethod
    def _model_policy():
        return ClaudeModelAliasPolicy(
            ClaudeModelPorts(
                strip_context_suffix=lambda value: str(value or "").replace("[1m]", ""),
                current_upstream_model=lambda _provider, _config: "",
                unslug_alias=lambda _provider, alias, _model_map: alias.removeprefix("ciel-runtime-test-"),
                model_map=lambda _provider, _config, fetch=False: {},
                context_hint=lambda model: 1_000_000 if model == "million" else None,
                anthropic_limit_hints=lambda _model: {},
                positive_int=lambda value: int(value) if value else None,
                configured_model_ids=lambda _provider, _config: [],
                normalize_model_id=lambda _provider, model: model,
                alias_for=lambda _provider, model: model,
            )
        )

    def _projection(self, *, native, key):
        limits = ClaudeLimitPolicy(
            ClaudeLimitPorts(
                positive_int=lambda value: int(value) if value else None,
                cap_output_tokens=lambda _provider, _config, value: value,
                ollama_options=lambda _config: {},
                context_limit=lambda _provider, _config: 4096,
                compaction_policy=lambda _provider, _config: ProviderRuntimeCompactionPolicy(),
            )
        )
        return ClaudeEnvironmentProjection(
            "http://router",
            limits,
            self._model_policy(),
            ClaudeEnvironmentSourcePorts(
                load_config=lambda: {"provider": "test"},
                current_provider=lambda config: (
                    config["provider"],
                    {**config, "max_output_tokens": 1024},
                ),
                direct_native=lambda _provider, _config: native,
                primary_api_key=lambda _provider, _config: key,
                meaningful_key=bool,
                current_alias=lambda _config: "current",
            ),
            ClaudeEnvironmentFeaturePorts(
                capability_string=lambda _provider, _config, _model: "tools",
                current_upstream_model=lambda _provider, _config: "upstream",
                resolve_requested_model=lambda _provider, _config, model: model,
                workflows_enabled=lambda _provider, _config: False,
                router_auth_token=lambda _provider, _config: "token",
                context_limit=lambda _provider, _config: 4096,
            ),
        )


if __name__ == "__main__":
    unittest.main()
