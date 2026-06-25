import unittest
from unittest import mock

import ciel_runtime


class RateLimitDefaultTests(unittest.TestCase):
    def test_hosted_rate_limit_defaults_are_off(self):
        for provider in ("ollama", "ollama-cloud", "lm-studio", "nvidia-hosted", "self-hosted-nim"):
            with self.subTest(provider=provider):
                pcfg = ciel_runtime.DEFAULT_CONFIG["providers"][provider]
                self.assertEqual(0, pcfg.get("rate_limit_rpm"))
                self.assertFalse(pcfg.get("rate_limit_status"))
                self.assertEqual(0, ciel_runtime.router_rate_limit_configured_rpm(provider, pcfg))

    def test_missing_rate_limit_is_not_implicitly_40_rpm(self):
        self.assertIsNone(ciel_runtime.router_rate_limit_configured_rpm("ollama-cloud", {}))
        self.assertIsNone(ciel_runtime.router_rate_limit_rpm("ollama-cloud", {}))

    def test_migration_flips_old_default_rate_limits_off(self):
        cfg = {
            "migrations": {},
            "providers": {
                "ollama": {"rate_limit_rpm": 40, "rate_limit_status": True},
                "ollama-cloud": {"rate_limit_rpm": "40", "rate_limit_status": True},
                "nvidia-hosted": {"rate_limit_rpm": 40, "rate_limit_status": True},
                "self-hosted-nim": {"rate_limit_rpm": 40, "rate_limit_status": True},
                "lm-studio": {"rate_limit_rpm": 0, "rate_limit_status": True},
            },
        }

        ciel_runtime.apply_config_migrations(cfg)

        for provider in ("ollama", "ollama-cloud", "nvidia-hosted", "self-hosted-nim", "lm-studio"):
            with self.subTest(provider=provider):
                self.assertEqual(0, int(cfg["providers"][provider]["rate_limit_rpm"]))
                self.assertFalse(cfg["providers"][provider]["rate_limit_status"])
        self.assertTrue(cfg["migrations"]["rate_limit_defaults_off_20260526"])

    def test_migration_preserves_explicit_non_default_rate_limit(self):
        cfg = {
            "migrations": {},
            "providers": {
                "ollama-cloud": {"rate_limit_rpm": 8, "rate_limit_status": True},
            },
        }

        ciel_runtime.apply_config_migrations(cfg)

        self.assertEqual(8, cfg["providers"]["ollama-cloud"]["rate_limit_rpm"])
        self.assertTrue(cfg["providers"]["ollama-cloud"]["rate_limit_status"])

    def test_llm_options_expose_explicit_rpm_limiter_toggle(self):
        pcfg = {"rate_limit_rpm": 0, "rate_limit_status": False}
        with (
            mock.patch.object(ciel_runtime, "router_debug_external_access_enabled", return_value=False),
            mock.patch.object(ciel_runtime, "router_debug_message_preview_chars", return_value=0),
        ):
            rows, values = ciel_runtime.llm_option_panel_rows("ollama-cloud", pcfg, "en")

        self.assertIn("rate_limit_enabled", values)
        limiter_row = rows[values.index("rate_limit_enabled")]
        rpm_row = rows[values.index("rate_limit_rpm")]
        self.assertIn("RPM limiter", limiter_row)
        self.assertIn("off", limiter_row)
        self.assertIn("0 (off)", rpm_row)

    def test_llm_options_expose_default_ip_family_for_ollama_cloud(self):
        pcfg = {"rate_limit_rpm": 0, "rate_limit_status": False}
        with (
            mock.patch.object(ciel_runtime, "router_debug_external_access_enabled", return_value=False),
            mock.patch.object(ciel_runtime, "router_debug_message_preview_chars", return_value=0),
        ):
            rows, values = ciel_runtime.llm_option_panel_rows("ollama-cloud", pcfg, "en")

        self.assertIn("ip_family", values)
        row = rows[values.index("ip_family")]
        self.assertIn("IP family", row)
        self.assertIn("auto", row)
        self.assertEqual("auto", ciel_runtime.llm_option_prompt_default("ollama-cloud", pcfg, "ip_family"))

    def test_llm_options_disable_rpm_limiter_sets_rpm_zero_and_status_off(self):
        cfg = {
            "providers": {
                "ollama-cloud": {"rate_limit_rpm": 8, "rate_limit_status": True},
            }
        }
        with (
            mock.patch.object(ciel_runtime, "load_config", return_value=cfg),
            mock.patch.object(ciel_runtime, "save_config") as save_config,
            mock.patch.object(ciel_runtime, "clear_model_cache"),
        ):
            messages = ciel_runtime.set_llm_option_config("ollama-cloud", "rate_limit_enabled", "false")

        self.assertEqual(0, cfg["providers"]["ollama-cloud"]["rate_limit_rpm"])
        self.assertFalse(cfg["providers"]["ollama-cloud"]["rate_limit_status"])
        self.assertIn("RPM limiter disabled.", messages)
        save_config.assert_called_once_with(cfg)

    def test_setting_rate_limit_rpm_zero_also_disables_status(self):
        pcfg = {"rate_limit_rpm": 8, "rate_limit_status": True}

        ciel_runtime.apply_ollama_option(pcfg, "rate_limit_rpm=0")

        self.assertEqual(0, pcfg["rate_limit_rpm"])
        self.assertFalse(pcfg["rate_limit_status"])


if __name__ == "__main__":
    unittest.main()
