import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import ciel_runtime
from ciel_runtime_support.prelaunch import guarded_zai_oauth_action
from ciel_runtime_support.zai_oauth import (
    ZaiOAuthError,
    ZaiOAuthRuntime,
    ZaiOAuthRuntimePorts,
)


class ZaiOAuthRuntimeTests(unittest.TestCase):
    def config(self):
        return {
            "current_provider": "deepseek",
            "providers": {
                name: copy.deepcopy(ciel_runtime.DEFAULT_CONFIG["providers"][name])
                for name in ("zai", "zai-coding-plan", "zai-start-plan")
            },
        }

    def runtime(self, config, saved, settings_path, native_login=None):
        return ZaiOAuthRuntime(
            ZaiOAuthRuntimePorts(
                load_config=lambda: config,
                save_config=lambda value: saved.append(copy.deepcopy(value)),
                clear_model_cache=lambda: None,
                mask=lambda value: f"masked:{value[-3:]}",
                fingerprint=lambda _value: "fp-test",
                output=lambda *_values, **_kwargs: None,
                native_login=native_login,
                native_settings_path=settings_path,
            )
        )

    @staticmethod
    def write_native_settings(path: Path, key: str = "coding-plan-key") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "provider": {
                        "zai": {
                            "kind": "anthropic",
                            "options": {
                                "apiKey": key,
                                "baseURL": "https://api.z.ai/api/anthropic",
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

    def test_login_delegates_to_native_zcode_then_imports_resolved_key(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".zcode" / "cli" / "config.json"
            self.write_native_settings(path)
            config, saved, calls = self.config(), [], []
            runtime = self.runtime(
                config,
                saved,
                path,
                native_login=lambda no_browser: calls.append(no_browser) or 0,
            )

            messages = runtime.action("login", no_browser=True)

        provider = config["providers"]["zai-coding-plan"]
        self.assertEqual([True], calls)
        self.assertEqual("coding-plan-key", provider["api_key"])
        self.assertEqual("native-zcode-oauth", provider["oauth_import_source"])
        self.assertEqual("zai-coding-plan", config["current_provider"])
        self.assertEqual(1, len(saved))
        self.assertNotIn("coding-plan-key", "\n".join(messages))

    def test_import_accepts_only_documented_native_zai_provider_shape(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            self.write_native_settings(path)
            config, saved = self.config(), []
            messages = self.runtime(config, saved, path).action("import")
        self.assertIn("imported", messages[0])
        self.assertEqual("coding-plan-key", config["providers"]["zai-coding-plan"]["api_key"])

    def test_import_rejects_an_unverified_base_without_mutating_config(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "provider": {
                            "zai": {
                                "kind": "anthropic",
                                "options": {
                                    "apiKey": "jwt",
                                    "baseURL": "https://zcode.z.ai/api/v1/zcode-plan",
                                },
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            config, saved = self.config(), []
            before = copy.deepcopy(config)
            with self.assertRaisesRegex(ZaiOAuthError, "not the documented"):
                self.runtime(config, saved, path).action("import")
        self.assertEqual(before, config)
        self.assertEqual([], saved)

    def test_failed_native_login_does_not_import_or_mutate(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            self.write_native_settings(path)
            config, saved = self.config(), []
            before = copy.deepcopy(config)
            with self.assertRaisesRegex(ZaiOAuthError, "exit 1"):
                self.runtime(config, saved, path, native_login=lambda _flag: 1).action(
                    "login"
                )
        self.assertEqual(before, config)
        self.assertEqual([], saved)

    def test_start_plan_jwt_import_is_blocked(self):
        config = self.config()
        with self.assertRaisesRegex(ZaiOAuthError, "not exposed"):
            self.runtime(config, [], Path("unused")).action(
                "login", profile="start-plan"
            )

    def test_manual_legacy_zai_key_is_never_overwritten(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            self.write_native_settings(path)
            config, saved = self.config(), []
            config["providers"]["zai"]["api_key"] = "manual-legacy-key"
            self.runtime(config, saved, path).action("import")
        self.assertEqual("manual-legacy-key", config["providers"]["zai"]["api_key"])

    def test_prelaunch_oauth_error_is_rendered_without_escaping_menu(self):
        def fail(_action):
            raise ZaiOAuthError("native ZCode failed")

        self.assertEqual(
            ["Z.AI OAuth failed: native ZCode failed"],
            guarded_zai_oauth_action("login", fail),
        )


if __name__ == "__main__":
    unittest.main()
