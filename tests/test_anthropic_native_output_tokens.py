import unittest

import claude_any


def _anthropic_cfg(**overrides):
    pcfg = {
        "base_url": "https://api.anthropic.com",
        "api_key": "",
        "current_model": "claude-fable-5",
        "route_through_router": True,
    }
    pcfg.update(overrides)
    return {"current_provider": "anthropic", "providers": {"anthropic": pcfg}}


class AnthropicPresetOutputTokensTests(unittest.TestCase):
    def test_balanced_preset_does_not_set_max_output_tokens(self):
        pcfg = {"current_model": "claude-fable-5", "route_through_router": True}
        claude_any.apply_llm_preset_to_provider("anthropic", pcfg, "balanced")
        self.assertNotIn("max_output_tokens", pcfg)

    def test_preset_clears_stale_preset_value(self):
        pcfg = {
            "current_model": "claude-fable-5",
            "route_through_router": True,
            "max_output_tokens": 4096,
        }
        claude_any.apply_llm_preset_to_provider("anthropic", pcfg, "coding")
        self.assertNotIn("max_output_tokens", pcfg)

    def test_routed_env_omits_output_cap_without_explicit_value(self):
        cfg = _anthropic_cfg()
        claude_any.apply_llm_preset_to_provider("anthropic", cfg["providers"]["anthropic"], "balanced")
        env = claude_any.env_vars(cfg)
        self.assertNotIn("CLAUDE_CODE_MAX_OUTPUT_TOKENS", env)

    def test_routed_env_emits_explicit_user_value(self):
        cfg = _anthropic_cfg(max_output_tokens=120000)
        env = claude_any.env_vars(cfg)
        self.assertEqual("120000", env.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS"))


class AnthropicOutputTokenLimitTests(unittest.TestCase):
    def test_limit_none_without_value(self):
        pcfg = {"route_through_router": True}
        self.assertIsNone(claude_any.claude_code_output_token_limit("anthropic", pcfg))

    def test_limit_returns_explicit_value(self):
        pcfg = {"max_output_tokens": 100000}
        self.assertEqual(100000, claude_any.claude_code_output_token_limit("anthropic", pcfg))


class AnthropicOutputTokensMigrationTests(unittest.TestCase):
    def _migrate(self, pcfg):
        cfg = {"providers": {"anthropic": pcfg}, "migrations": {}}
        claude_any.apply_config_migrations(cfg)
        return cfg["providers"]["anthropic"]

    def test_stale_preset_value_dropped(self):
        for stale in (2048, 4096, 6144, 8192):
            pcfg = self._migrate({"max_output_tokens": stale, "route_through_router": True})
            self.assertNotIn("max_output_tokens", pcfg, f"value {stale} should be dropped")

    def test_non_preset_value_preserved(self):
        pcfg = self._migrate({"max_output_tokens": 50000, "route_through_router": True})
        self.assertEqual(50000, pcfg.get("max_output_tokens"))

    def test_migration_runs_once(self):
        cfg = {
            "providers": {"anthropic": {"max_output_tokens": 4096}},
            "migrations": {},
        }
        claude_any.apply_config_migrations(cfg)
        # User re-sets the same round number after the one-shot migration.
        cfg["providers"]["anthropic"]["max_output_tokens"] = 4096
        claude_any.apply_config_migrations(cfg)
        self.assertEqual(4096, cfg["providers"]["anthropic"].get("max_output_tokens"))


class NonAnthropicUnaffectedTests(unittest.TestCase):
    def test_deepseek_output_limit_unchanged(self):
        pcfg = {"max_output_tokens": 8192}
        self.assertEqual(8192, claude_any.claude_code_output_token_limit("deepseek", pcfg))

    def test_migration_does_not_touch_other_providers(self):
        cfg = {
            "providers": {
                "anthropic": {"max_output_tokens": 4096},
                "deepseek": {"max_output_tokens": 4096},
            },
            "migrations": {},
        }
        claude_any.apply_config_migrations(cfg)
        self.assertNotIn("max_output_tokens", cfg["providers"]["anthropic"])
        self.assertEqual(4096, cfg["providers"]["deepseek"].get("max_output_tokens"))


if __name__ == "__main__":
    unittest.main()
