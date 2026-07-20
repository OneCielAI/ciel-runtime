import unittest

from ciel_runtime_support.claude_environment import (
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
            )
        )
        self.assertEqual(512, policy.output_token_limit("openai", {"max_output_tokens": 1024}))
        self.assertEqual(512, policy.output_token_limit("ollama", {"options": {"num_predict": 768}}))
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

    def test_native_projection_only_sets_marker_and_meaningful_key(self):
        projection = self._projection(native=True, key="secret")
        self.assertEqual(
            {"CIEL_RUNTIME_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "secret"},
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

    def test_shell_renderer_quotes_values_and_unsets_missing_keys(self):
        lines = ClaudeEnvironmentShellRenderer.lines({"ANTHROPIC_BASE_URL": "http://router"})
        self.assertEqual('export ANTHROPIC_BASE_URL="http://router"', lines[0])
        self.assertIn("unset ANTHROPIC_API_KEY", lines)
        self.assertIn("unset CIEL_RUNTIME_PROVIDER", lines)

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
            )
        )
        return ClaudeEnvironmentProjection(
            "http://router",
            limits,
            self._model_policy(),
            ClaudeEnvironmentSourcePorts(
                load_config=lambda: {"provider": "test"},
                current_provider=lambda config: (config["provider"], {"max_output_tokens": 1024}),
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
