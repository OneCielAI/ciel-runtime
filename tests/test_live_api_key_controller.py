import unittest
from unittest.mock import Mock

from ciel_runtime_support.live_api_key_controller import (
    LiveApiKeyController,
    LiveApiKeyPorts,
)


class LiveApiKeyControllerTests(unittest.TestCase):
    def setUp(self):
        self.config = {"providers": {"provider": {"api_keys": ["masked"]}}}
        self.store = Mock(return_value=["stored"])
        self.controller = LiveApiKeyController(
            LiveApiKeyPorts(
                load_config=lambda: self.config,
                current_provider=lambda config: (
                    "provider",
                    config["providers"]["provider"],
                ),
                status_line=lambda _provider, _config: "API keys: 1 configured",
                stored_mask=lambda _provider, _config: "***fingerprint",
                store_input=self.store,
            )
        )

    def test_status_never_projects_raw_key_material(self):
        lines, changed = self.controller.handle("status")

        self.assertFalse(changed)
        self.assertIn("Stored: ***fingerprint", lines)
        self.assertFalse(any("masked" in line for line in lines))

    def test_help_is_read_only(self):
        lines, changed = self.controller.handle("help")

        self.assertFalse(changed)
        self.store.assert_not_called()
        self.assertTrue(any("never echoed" in line for line in lines))

    def test_store_refreshes_status_after_mutation(self):
        lines, changed = self.controller.handle("key-one,key-two")

        self.assertTrue(changed)
        self.store.assert_called_once_with("provider", "key-one,key-two")
        self.assertEqual("stored", lines[0])
        self.assertTrue(any("next model request" in line for line in lines))

    def test_invalid_input_is_reported_without_change(self):
        self.store.side_effect = SystemExit("invalid key input")

        lines, changed = self.controller.handle("bad")

        self.assertFalse(changed)
        self.assertEqual("invalid key input", lines[0])
        self.assertIn("Stored: ***fingerprint", lines)


if __name__ == "__main__":
    unittest.main()
