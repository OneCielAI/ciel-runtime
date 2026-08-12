import unittest

import ciel_runtime


class MessageBridgeConfigurationTests(unittest.TestCase):
    def test_default_config_does_not_own_external_mcp_channels(self):
        claude = ciel_runtime.DEFAULT_CONFIG["claude_code"]
        self.assertNotIn("channels", claude)
        self.assertNotIn("development_channels", claude)
        self.assertNotIn("channel_delivery", claude)

    def test_migration_removes_retired_external_mcp_settings(self):
        config = ciel_runtime.deep_merge(
            ciel_runtime.DEFAULT_CONFIG,
            {
                "claude_code": {
                    "channels": ["server:old"],
                    "development_channels": True,
                    "channel_delivery": "native",
                }
            },
        )
        ciel_runtime.apply_config_migrations(config)
        claude = config["claude_code"]
        self.assertNotIn("channels", claude)
        self.assertNotIn("development_channels", claude)
        self.assertNotIn("channel_delivery", claude)

    def test_ciel_message_delivery_remains_llm_wake_only(self):
        self.assertEqual("llm", ciel_runtime.channel_delivery_mode({}))
        self.assertEqual("llm", ciel_runtime.normalize_channel_delivery("native"))
        self.assertIn("Web Chat", ciel_runtime.channel_status_text({}))


if __name__ == "__main__":
    unittest.main()
