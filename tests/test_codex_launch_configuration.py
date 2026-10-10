import json
import unittest
from pathlib import Path

from ciel_runtime_support.architecture import ProviderRuntimeCompactionPolicy
from ciel_runtime_support.codex_launch_configuration import (
    CodexLaunchCatalogPorts,
    CodexLaunchConfigurationConstants,
    CodexLaunchConfigurationEffects,
    CodexLaunchConfigurationService,
    CodexLaunchModelPorts,
    CodexLaunchPolicyPorts,
    build_default_codex_launch_constants,
    build_default_codex_launch_policy,
)
from ciel_runtime_support.codex_app_server_session import remote_tui_command
from ciel_runtime_support.codex_config import (
    codex_config_override_keys,
    codex_config_profile,
    codex_config_sets_reasoning_effort,
)
from ciel_runtime_support.codex_launch_policy import current_model_args, native_routed_config_args

MEDIUM = ["-c", 'model_reasoning_effort="medium"']


class CodexLaunchConfigurationServiceTests(unittest.TestCase):
    def test_default_factories_own_routed_constants_and_config_policy(self):
        constants = build_default_codex_launch_constants()
        policy = build_default_codex_launch_policy(
            lambda args, *names: any(name in args for name in names)
        )
        self.assertEqual("ciel-runtime", constants.runtime_provider_id)
        self.assertEqual("tui.alternate_screen", constants.alternate_screen_key)
        self.assertTrue(policy.has_option(["--model"], "--model"))
        self.assertEqual('"value"', policy.toml_string("value"))

    def service(
        self,
        *,
        native=False,
        files=None,
        writes=None,
        compaction_policy=None,
        logs=None,
    ):
        files = files or {}
        logs = logs if logs is not None else []
        writes = writes if writes is not None else []
        compaction_policy = compaction_policy or (
            lambda _provider, _config: ProviderRuntimeCompactionPolicy()
        )
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
                config_override_keys=codex_config_override_keys,
                config_paths=lambda *_args, **_kwargs: list(files),
                alternate_screen_value=lambda text: "never" if "false" in text else None,
                toml_string=json.dumps,
            ),
            model=CodexLaunchModelPorts(
                current_provider=lambda cfg: (cfg["provider"], cfg["config"]),
                native_enabled=lambda _provider: native,
                current_alias=lambda cfg: cfg.get("alias", ""),
                context_window=lambda _provider, config: int(
                    config.get("context_window") or 1000
                ),
                compaction_policy=compaction_policy,
            ),
            catalog=CodexLaunchCatalogPorts(
                write=lambda codex, spec, env: writes.append((codex, spec, env))
                or Path("catalog.json"),
                provider_label=lambda provider: provider.upper(),
                path_value=lambda _env: "runtime-path",
                current_model_args=current_model_args,
                native_routed_args=native_routed_config_args,
            ),
            effects=CodexLaunchConfigurationEffects(
                environ=lambda: {"NATIVE_PROVIDER": "custom"},
                router_base=lambda: "http://router",
                read_text=lambda path: files[path],
                log=lambda level, message: logs.append((level, message)),
                output=lambda _message: None,
            ),
        )

    def test_native_provider_still_receives_the_compaction_threshold(self):
        # A native provider keeps its own bundled catalog, so no catalog file is
        # written -- but the trigger for Codex's own compaction is still ours to
        # place, and a session that crossed providers depends on it firing
        # before the smaller window is already exceeded.
        writes = []
        service = self.service(native=True, writes=writes)
        cfg = {"provider": "codex", "alias": "gpt-5.6-sol",
               "config": {"codex_auto_compact_window": 240000, "context_window": 300000}}

        args = service.runtime_model_catalog_args("codex", cfg)

        self.assertEqual(["-c", "model_auto_compact_token_limit=240000", *MEDIUM], args)
        self.assertEqual([], writes)

    def test_native_provider_without_a_configured_threshold_adds_nothing(self):
        service = self.service(
            native=True, files={Path("config.toml"): 'model_reasoning_effort = "high"'}
        )

        self.assertEqual(
            [], service.runtime_model_catalog_args("codex", {"provider": "codex", "config": {}})
        )
        self.assertEqual(
            [],
            service.runtime_model_catalog_args(
                "codex", {"provider": "codex", "config": {"codex_auto_compact_window": 0}}
            ),
        )

    def test_launch_snapshot_recomputes_threshold_after_provider_model_change(self):
        writes = []
        service = self.service(
            writes=writes,
            compaction_policy=lambda provider, config: ProviderRuntimeCompactionPolicy(
                trigger_percent=85
                if provider == "kimi" and config.get("current_model") == "k3"
                else None
            ),
        )
        kimi = {
            "provider": "kimi",
            "alias": "ciel-runtime-kimi-k3[1m]",
            "config": {"current_model": "k3", "context_window": 1_048_576},
        }
        other = {
            "provider": "other",
            "alias": "ciel-runtime-other-model",
            "config": {"current_model": "model", "context_window": 262_144},
        }

        service.runtime_model_catalog_args("codex", kimi)
        service.runtime_model_catalog_args("codex", other)

        self.assertEqual(891_289, writes[0][1].auto_compact_token_limit)
        self.assertEqual(235_929, writes[1][1].auto_compact_token_limit)


    def test_runtime_config_uses_responses_provider(self):
        args = self.service().runtime_config_args()

        joined = "\n".join(args)
        self.assertIn('model_provider="ciel-runtime"', joined)
        self.assertIn('base_url="http://router/v1"', joined)
        self.assertIn('env_key="CIEL_KEY"', joined)

    def test_alternate_screen_reads_configuration_through_effect_port(self):
        path = Path("config.toml")
        args = self.service(files={path: "[tui]\nalternate_screen = false"}).alternate_screen_compat_args([])

        self.assertEqual(["-c", 'tui.alternate_screen="never"'], args)

    def test_catalog_projection_uses_model_ports(self):
        writes = []
        service = self.service(writes=writes)
        cfg = {
            "provider": "zai",
            "config": {
                "effort_level": "MAX",
                "codex_model_catalog": {"supports_parallel_tool_calls": False},
            },
            "alias": "zai-model",
        }

        path = service.write_runtime_model_catalog("codex", cfg)

        self.assertEqual(Path("catalog.json"), path)
        _, spec, env = writes[0]
        self.assertEqual("zai-model", spec.alias)
        self.assertEqual(1000, spec.context_window)
        self.assertEqual("max", spec.effort)
        self.assertEqual({"supports_parallel_tool_calls": False}, spec.metadata)
        self.assertEqual("runtime-path", env["PATH"])

    def test_native_provider_skips_routed_catalog(self):
        cfg = {"provider": "codex", "config": {}, "alias": "model"}
        self.assertIsNone(self.service(native=True).write_runtime_model_catalog("codex", cfg))


class CodexReasoningEffortDefaultTests(unittest.TestCase):
    service = CodexLaunchConfigurationServiceTests.service
    native_cfg = {"provider": "codex", "config": {}}

    def args(self, files=None, passthrough=None, native=True, logs=None):
        return self.service(native=native, files=files, logs=logs).runtime_model_catalog_args(
            "codex", self.native_cfg if native else {"provider": "zai", "alias": "m", "config": {}},
            passthrough or [],
        )

    def test_unset_effort_becomes_medium_and_is_logged(self):
        logs = []
        config = Path("config.toml")
        self.assertEqual(MEDIUM, self.args({config: "[tui]\nalternate_screen = true\n"}, logs=logs))
        self.assertIn(("INFO", "codex_reasoning_effort_default effort=medium reason=unset"), logs)
        self.assertEqual(MEDIUM, self.args())

    def test_an_effort_the_operator_chose_is_kept(self):
        config = Path("config.toml")
        for text in ('model_reasoning_effort = "high"', 'model = "gpt-6.1-sol"\nmodel_reasoning_effort = "low" # chosen'):
            self.assertEqual([], self.args({config: text}), text)
        self.assertEqual([], self.args(passthrough=["-c", 'model_reasoning_effort="xhigh"']))
        self.assertEqual([], self.args(passthrough=["--config=model_reasoning_effort=high"]))

    def test_profile_effort_counts_only_for_the_active_profile(self):
        config = Path("config.toml")
        profiled = '[profiles.deep]\nmodel_reasoning_effort = "high"\n'
        self.assertEqual([], self.args({config: profiled}, passthrough=["-p", "deep"]))
        self.assertEqual([], self.args({config: 'profile = "deep"\n' + profiled}))
        self.assertEqual(MEDIUM, self.args({config: profiled}))
        self.assertEqual(MEDIUM, self.args({config: profiled}, passthrough=["--profile=other"]))

    def test_explicit_model_launch_still_gets_medium(self):
        self.assertEqual(MEDIUM, self.args(passthrough=["-m", "gpt-6.1-sol"]))

    def test_routed_providers_keep_their_catalog_effort(self):
        self.assertNotIn("model_reasoning_effort", " ".join(self.args(native=False)))

    def test_routed_effort_is_named_so_a_config_default_cannot_outrank_it(self):
        def routed(config, passthrough=()):
            cfg = {"provider": "kimi", "alias": "k3", "config": config}
            return self.service().runtime_model_catalog_args("codex", cfg, list(passthrough))

        self.assertEqual(["-c", 'model_catalog_json="catalog.json"', "-c", 'model_reasoning_effort="max"'][2:],
                         routed({"effort_level": "MAX"})[2:])
        self.assertEqual(["-c", 'model_reasoning_effort="high"'],
                         routed({"codex_model_catalog": {"default_reasoning_level": "high"}})[2:])
        self.assertEqual([], routed({"effort_level": "max"}, ["-c", 'model_reasoning_effort="low"'])[2:])
        self.assertEqual([], routed({})[2:])

    def test_unreadable_config_does_not_block_the_default(self):
        missing = Path("missing.toml")
        service = self.service(native=True, files={})
        policy = CodexLaunchPolicyPorts(
            has_option=service.policy.has_option,
            config_override_keys=service.policy.config_override_keys,
            config_paths=lambda *_a, **_k: [missing],
            alternate_screen_value=service.policy.alternate_screen_value,
            toml_string=service.policy.toml_string,
        )
        service = CodexLaunchConfigurationService(
            constants=service.constants, policy=policy, model=service.model,
            catalog=service.catalog, effects=service.effects,
        )
        self.assertEqual(MEDIUM, service.runtime_model_catalog_args("codex", self.native_cfg, []))

    def test_remote_tui_receives_the_same_effort(self):
        server = ["codex", "app-server", "-c", 'model_reasoning_effort="medium"', "--listen", "ws://127.0.0.1:1"]
        tui = remote_tui_command(server, "ws://127.0.0.1:1")
        self.assertEqual(["codex", "-c", 'model_reasoning_effort="medium"', "--remote", "ws://127.0.0.1:1"], tui)


class CodexConfigReasoningEffortTests(unittest.TestCase):
    def test_commented_and_other_table_keys_do_not_count(self):
        self.assertFalse(codex_config_sets_reasoning_effort('# model_reasoning_effort = "high"', []))
        self.assertFalse(codex_config_sets_reasoning_effort('[tui]\nmodel_reasoning_effort = "high"', []))
        self.assertFalse(codex_config_sets_reasoning_effort('note = "model_reasoning_effort = high"', []))
        self.assertTrue(codex_config_sets_reasoning_effort('[profiles."deep"]\nmodel_reasoning_effort = "high"', ["deep"]))
        self.assertTrue(codex_config_sets_reasoning_effort('profiles.deep.model_reasoning_effort = "high"', ["deep"]))

    def test_top_level_profile_is_read(self):
        self.assertEqual("deep", codex_config_profile('profile = "deep"\n[profiles.deep]\nmodel = "x"'))
        self.assertIsNone(codex_config_profile('[profiles.deep]\nprofile = "nested"'))


if __name__ == "__main__":
    unittest.main()
