import unittest

import ciel_runtime
from ciel_runtime_support import slash_command_assets


class SlashCommandAssetsTests(unittest.TestCase):
    def test_version_document_and_markers_are_owned_by_asset_module(self):
        self.assertIn("CIEL_RUNTIME_VERSION_STATUS", slash_command_assets.VERSION_SLASH_COMMAND)
        self.assertEqual(
            ("CIEL_RUNTIME_VERSION_STATUS",),
            slash_command_assets.VERSION_REQUEST_MARKERS,
        )
        self.assertIs(
            ciel_runtime.VERSION_SLASH_COMMAND,
            slash_command_assets.VERSION_SLASH_COMMAND,
        )

    def test_command_marker_sets_include_legacy_compatibility_tokens(self):
        self.assertIn(
            slash_command_assets.LEGACY_ADVISOR_CALL_MARKER,
            slash_command_assets.CIEL_RUNTIME_ADVISOR_COMMAND_MARKERS,
        )
        self.assertIn(
            slash_command_assets.LEGACY_LIVE_LLM_OPTIONS_MARKER,
            slash_command_assets.CIEL_RUNTIME_LLM_OPTIONS_COMMAND_MARKERS,
        )
        self.assertEqual(
            slash_command_assets.CIEL_RUNTIME_CHANNEL_CLEAR_COMMAND_MARKERS,
            ciel_runtime.CIEL_RUNTIME_CHANNEL_CLEAR_COMMAND_MARKERS,
        )


if __name__ == "__main__":
    unittest.main()
