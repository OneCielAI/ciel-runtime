import json
import unittest
from pathlib import Path

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

    def service(self, *, native=False, files=None, writes=None):
        files = files or {}
        writes = writes if writes is not None else []
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
                context_limit=lambda _provider, _config: 1000,
                context_capacity=lambda _provider, _config: None,
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
        cfg = {"provider": "zai", "config": {"effort_level": "MAX"}, "alias": "zai-model"}

        path = service.write_runtime_model_catalog("codex", cfg)

        self.assertEqual(Path("catalog.json"), path)
        _, spec, env = writes[0]
        self.assertEqual("zai-model", spec.alias)
        self.assertEqual(1000, spec.context_window)
        self.assertEqual("max", spec.effort)
        self.assertEqual("runtime-path", env["PATH"])

    def test_native_provider_skips_routed_catalog(self):
        cfg = {"provider": "codex", "config": {}, "alias": "model"}
        self.assertIsNone(self.service(native=True).write_runtime_model_catalog("codex", cfg))


if __name__ == "__main__":
    unittest.main()
