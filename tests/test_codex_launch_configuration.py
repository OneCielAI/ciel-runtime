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
from ciel_runtime_support.codex_launch_policy import current_model_args, native_routed_config_args


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
    ):
        files = files or {}
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
                config_override_keys=lambda _args: set(),
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
                log=lambda _level, _message: None,
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

        self.assertEqual(["-c", "model_auto_compact_token_limit=240000"], args)
        self.assertEqual([], writes)

    def test_native_provider_without_a_configured_threshold_adds_nothing(self):
        service = self.service(native=True)

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


if __name__ == "__main__":
    unittest.main()
