import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import ciel_runtime
from ciel_runtime_support.architecture import LaunchSpec, ProviderConfig, RuntimeConfig
from ciel_runtime_support.runtime_adapters import RUNTIME_ADAPTERS, ZcodeRuntimeAdapter
from ciel_runtime_support.zcode_runtime_context import (
    ZcodeConfigurationPorts,
    ZcodeLifecyclePorts,
    ZcodeProcessPorts,
    ZcodeRuntimeContext,
    zcode_settings,
)


class ZcodeRuntimeTests(unittest.TestCase):
    def test_adapter_is_registered_and_preserves_zcode_arguments(self):
        adapter = RUNTIME_ADAPTERS.create(
            "zcode", executable="zcode", environment={}, channel_injection=False
        )
        self.assertIsInstance(adapter, ZcodeRuntimeAdapter)
        spec = LaunchSpec(
            runtime=RuntimeConfig(
                name="zcode",
                executable="zcode",
                options={},
            ),
            provider=ProviderConfig(name="zai", base_url="", model=""),
            mode="routed",
            protocol="anthropic_messages",
            passthrough=("--prompt", "hello"),
        )
        self.assertEqual(
            ("zcode", "--prompt", "hello"),
            adapter.build_command(spec).argv,
        )

    def test_settings_follow_zcode_custom_anthropic_provider_schema(self):
        payload = zcode_settings(
            model="ciel-runtime-zai-glm-5.3-1m",
            base_url="http://127.0.0.1:3456/",
            api_key="oauth-key",
        )
        provider = payload["provider"]["zai"]
        self.assertEqual("anthropic", provider["kind"])
        self.assertEqual("http://127.0.0.1:3456", provider["options"]["baseURL"])
        self.assertEqual("oauth-key", provider["options"]["apiKey"])
        self.assertEqual(
            "zai/ciel-runtime-zai-glm-5.3-1m", payload["model"]["main"]
        )

    def context(self, root: Path, captured: dict, oauth_action=lambda *_args, **_kwargs: []):
        config = {
            "current_provider": "zai",
            "providers": {"zai": {"api_key": "oauth-key", "current_model": "glm-5.3"}},
        }

        def save_json(path, value, _purpose):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding="utf-8")

        def materialize(*args, **kwargs):
            captured["materialize"] = (args, kwargs)
            return ["zcode", *kwargs["passthrough"]], dict(args[2])

        return ZcodeRuntimeContext(
            process=ZcodeProcessPorts(
                find_executable=lambda name: f"C:/bin/{name}.cmd",
                run=lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
                call=lambda command, **kwargs: captured.update(command=command, child=kwargs) or 0,
                print_line=lambda *values, **_kwargs: captured.setdefault("output", []).append(" ".join(map(str, values))),
                environment={"PATH": "original"},
                augment_path=lambda _env: "augmented",
            ),
            config=ZcodeConfigurationPorts(
                load=lambda: config,
                current_provider=lambda cfg: ("zai", cfg["providers"]["zai"]),
                current_alias=lambda _cfg: "ciel-runtime-zai-glm-5.3",
                router_auth_token=lambda _provider, pcfg: pcfg["api_key"],
                router_base="http://127.0.0.1:3456",
                zai_anthropic_base_url="https://api.z.ai/api/anthropic",
                settings_path=root / "zcode-home" / ".zcode" / "cli" / "config.json",
                save_json=save_json,
                import_oauth_api_key=lambda key: captured.update(imported=key) or ["imported"],
            ),
            lifecycle=ZcodeLifecyclePorts(
                oauth_action=oauth_action,
                start_router=lambda: True,
                run_with_router=lambda runner, managed: captured.update(managed=managed) or runner(),
                materialize_command=materialize,
                record_launch=lambda provider, model: captured.update(record=(provider, model)),
            ),
        )

    def test_launch_writes_workspace_settings_and_runs_through_router(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            captured = {}
            context = self.context(Path(temp_dir), captured)

            self.assertEqual(0, context.launch(["--prompt", "hello"]))

            payload = json.loads((Path(temp_dir) / "zcode-home" / ".zcode" / "cli" / "config.json").read_text(encoding="utf-8"))
            self.assertEqual("oauth-key", payload["provider"]["zai"]["options"]["apiKey"])
            self.assertEqual("http://127.0.0.1:3456", payload["provider"]["zai"]["options"]["baseURL"])
            self.assertEqual(["zcode", "--prompt", "hello"], captured["command"])
            self.assertEqual(str(Path(temp_dir) / "zcode-home"), captured["child"]["env"]["USERPROFILE"])
            self.assertEqual(str(Path(temp_dir) / "zcode-home" / ".zcode"), captured["child"]["env"]["ZCODE_STORAGE_DIR"])
            self.assertTrue(captured["managed"])
            self.assertEqual(("zai", "ciel-runtime-zai-glm-5.3"), captured["record"])

    def test_zcode_oauth_login_uses_shared_ciel_credential_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            captured = {}

            def oauth(action, **kwargs):
                captured["oauth"] = (action, kwargs)
                return ["Z.AI OAuth login completed."]

            context = self.context(Path(temp_dir), captured, oauth)
            self.assertEqual(0, context.launch(["login", "--oauth", "--no-browser"]))
            self.assertEqual(("login", {"no_browser": True}), captured["oauth"])
            self.assertNotIn("command", captured)

    def test_official_zcode_oauth_result_is_imported_after_tui_exit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            captured = {}
            context = self.context(Path(temp_dir), captured)
            path = context.config.settings_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "provider": {
                            "zai": {
                                "options": {
                                    "baseURL": "https://api.z.ai/api/anthropic",
                                    "apiKey": "oauth-from-zcode",
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(["imported"], context.import_zcode_oauth_credential(path))
            self.assertEqual("oauth-from-zcode", captured["imported"])

    def test_cli_and_launch_menu_expose_zcode(self):
        rows, values = ciel_runtime.launch_panel_rows(
            {"current_provider": "zai", "providers": {"zai": {}}}
        )
        self.assertIn("ZCode", rows)
        self.assertIn("launch-zcode", values)
        with (
            mock.patch.object(ciel_runtime, "apply_headless_env_config", return_value=(True, None, None, None, False)),
            mock.patch.object(ciel_runtime, "launch_zcode", return_value=0) as launch_zcode,
        ):
            self.assertEqual(0, ciel_runtime.run_cli(["--ca-runtime", "zcode", "--", "--version"]))
        launch_zcode.assert_called_once_with(
            ["--version"], skip_menu=True, force_menu=False, update_check=True, self_update_check=True
        )


if __name__ == "__main__":
    unittest.main()
