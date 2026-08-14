import tempfile
import unittest
import json
from pathlib import Path

from ciel_runtime_support.config_repository import (
    ConfigRepositoryProvider,
    WorkspaceConfigRepository,
    build_default_config,
    deep_merge,
    normalize_loaded_config,
)


class ConfigRepositoryTest(unittest.TestCase):
    def test_default_config_embeds_provider_registry(self):
        providers = {"example": {"base_url": "http://example"}}
        config = build_default_config(providers)
        self.assertIs(providers, config["providers"])
        self.assertEqual("nvidia-hosted", config["current_provider"])
        self.assertNotIn("channel_delivery", config["claude_code"])

    def test_deep_merge_preserves_nested_defaults(self):
        merged = deep_merge(
            {"provider": {"model": "default", "options": ["a"]}},
            {"provider": {"model": "custom"}},
        )
        self.assertEqual("custom", merged["provider"]["model"])
        self.assertEqual(["a"], merged["provider"]["options"])

    def test_normalization_migrates_legacy_key_and_model_ids(self):
        config = {
            "providers": {
                "ollama": {"api_key": "legacy-key"},
                "ollama-cloud": {"api_key": "", "current_model": " MODEL ", "custom_models": [" A ", ""]},
            }
        }
        normalize_loaded_config(config, lambda provider, model: model.strip().lower())
        cloud = config["providers"]["ollama-cloud"]
        self.assertEqual("legacy-key", cloud["api_key"])
        self.assertEqual("model", cloud["current_model"])
        self.assertEqual(["a"], cloud["custom_models"])

    def test_provider_reuses_same_path_and_rebuilds_for_new_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = ConfigRepositoryProvider()
            callbacks = {
                "defaults": {},
                "merge": deep_merge,
                "migrate": lambda config: None,
                "normalize": lambda config: None,
            }
            first = provider.get(path=Path(tmp) / "one.json", **callbacks)
            same = provider.get(path=Path(tmp) / "one.json", **callbacks)
            second = provider.get(path=Path(tmp) / "two.json", **callbacks)
        self.assertIs(first, same)
        self.assertIsNot(first, second)

    def test_workspace_repository_copies_legacy_config_once_then_is_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "config.json"
            workspace = root / "workspaces" / "alpha" / "config.json"
            legacy.write_text(
                json.dumps({"current_provider": "kimi", "providers": {"kimi": {"current_model": "k3"}}}),
                encoding="utf-8",
            )
            repository = ConfigRepositoryProvider().get(
                path=workspace,
                fallback_path=legacy,
                defaults={"current_provider": "default", "providers": {}},
                merge=deep_merge,
                migrate=lambda config: None,
                normalize=lambda config: None,
            )

            self.assertEqual("kimi", repository.load()["current_provider"])
            self.assertTrue(workspace.exists())
            legacy.write_text(
                json.dumps({"current_provider": "alitoken", "providers": {"alitoken": {}}}),
                encoding="utf-8",
            )
            repository.invalidate()

            self.assertEqual("kimi", repository.load()["current_provider"])

    def test_workspace_bootstrap_can_restore_last_launch_provider_and_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "config.json"
            workspace = root / "workspaces" / "alpha" / "config.json"
            legacy.write_text(
                json.dumps(
                    {
                        "current_provider": "alitoken",
                        "providers": {
                            "alitoken": {"current_model": "qwen"},
                            "kimi": {"current_model": "old"},
                        },
                    }
                ),
                encoding="utf-8",
            )

            def bootstrap(config):
                config["current_provider"] = "kimi"
                config["providers"]["kimi"]["current_model"] = "k3[1m]"

            repository = ConfigRepositoryProvider().get(
                path=workspace,
                fallback_path=legacy,
                bootstrap=bootstrap,
                defaults={"current_provider": "default", "providers": {}},
                merge=deep_merge,
                migrate=lambda config: None,
                normalize=lambda config: None,
            )
            config = repository.load()

            self.assertEqual("kimi", config["current_provider"])
            self.assertEqual("k3[1m]", config["providers"]["kimi"]["current_model"])
            persisted = json.loads(workspace.read_text(encoding="utf-8"))
            self.assertEqual("kimi", persisted["current_provider"])

    def test_workspace_selections_are_isolated_while_credentials_are_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            defaults = {
                "current_provider": "default",
                "providers": {
                    "kimi": {"current_model": "", "api_key": ""},
                    "alitoken": {"current_model": "", "api_key": ""},
                },
            }
            shared_path = root / "config.json"
            shared_path.write_text(
                json.dumps(
                    {
                        "current_provider": "alitoken",
                        "providers": {
                            "kimi": {"api_key": "shared-kimi"},
                            "alitoken": {"api_key": "shared-ali"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            alpha_path = root / "workspaces" / "alpha" / "config.json"
            beta_path = root / "workspaces" / "beta" / "config.json"
            alpha_path.parent.mkdir(parents=True)
            beta_path.parent.mkdir(parents=True)
            alpha_path.write_text(
                json.dumps(
                    {
                        "current_provider": "kimi",
                        "providers": {"kimi": {"current_model": "k3[1m]"}},
                    }
                ),
                encoding="utf-8",
            )
            beta_path.write_text(
                json.dumps(
                    {
                        "current_provider": "alitoken",
                        "providers": {"alitoken": {"current_model": "qwen3.8-max"}},
                    }
                ),
                encoding="utf-8",
            )
            callbacks = {
                "defaults": defaults,
                "merge": deep_merge,
                "migrate": lambda config: None,
                "normalize": lambda config: None,
            }
            shared = ConfigRepositoryProvider().get(path=shared_path, **callbacks)
            alpha = WorkspaceConfigRepository(
                workspace=ConfigRepositoryProvider().get(path=alpha_path, **callbacks),
                shared=shared,
            )
            beta = WorkspaceConfigRepository(
                workspace=ConfigRepositoryProvider().get(path=beta_path, **callbacks),
                shared=shared,
            )

            alpha_config = alpha.load()
            beta_config = beta.load()
            self.assertEqual("kimi", alpha_config["current_provider"])
            self.assertEqual("k3[1m]", alpha_config["providers"]["kimi"]["current_model"])
            self.assertEqual("alitoken", beta_config["current_provider"])
            self.assertEqual("qwen3.8-max", beta_config["providers"]["alitoken"]["current_model"])
            self.assertEqual("shared-kimi", alpha_config["providers"]["kimi"]["api_key"])
            self.assertEqual("shared-kimi", beta_config["providers"]["kimi"]["api_key"])

            alpha_config["providers"]["kimi"]["api_key"] = "rotated-kimi"
            alpha_config["providers"]["kimi"]["current_model"] = "k3-new"
            alpha.save(alpha_config)
            beta.invalidate()
            reloaded_beta = beta.load()

            self.assertEqual("rotated-kimi", reloaded_beta["providers"]["kimi"]["api_key"])
            self.assertEqual("qwen3.8-max", reloaded_beta["providers"]["alitoken"]["current_model"])
            persisted_global = json.loads(shared_path.read_text(encoding="utf-8"))
            persisted_alpha = json.loads(alpha_path.read_text(encoding="utf-8"))
            self.assertEqual("alitoken", persisted_global["current_provider"])
            self.assertEqual("rotated-kimi", persisted_global["providers"]["kimi"]["api_key"])
            self.assertNotIn("api_key", persisted_alpha["providers"]["kimi"])
            self.assertEqual("k3-new", persisted_alpha["providers"]["kimi"]["current_model"])


if __name__ == "__main__":
    unittest.main()
