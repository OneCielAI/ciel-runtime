import unittest

from ciel_runtime_support.launch_diagnostics import LaunchCommandDiagnostics


class LaunchCommandDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.events: list[tuple[str, str]] = []
        self.diagnostics = LaunchCommandDiagnostics(
            log=lambda level, message: self.events.append((level, message)),
            mask_secret=lambda value: f"masked:{value[-2:]}",
            codex_api_key_env="CODEX_RUNTIME_KEY",
        )

    def test_claude_projects_mcp_channels_and_masks_credentials(self):
        self.diagnostics.claude(
            [
                "claude",
                "--mcp-config",
                "mcp.json",
                "--dangerously-load-development-channels",
                "server:alpha",
                "server:beta",
                "--verbose",
            ],
            {"ANTHROPIC_API_KEY": "secret-key", "CIEL_RUNTIME_PROVIDER": "anthropic"},
        )

        messages = "\n".join(message for _, message in self.events)
        self.assertIn("mcp_config=mcp.json", messages)
        self.assertIn("channels=server:alpha,server:beta", messages)
        self.assertIn("ANTHROPIC_API_KEY=masked:ey", messages)
        self.assertNotIn("secret-key", messages)

    def test_codex_counts_provider_overrides_and_masks_runtime_key(self):
        self.diagnostics.codex(
            ["codex", "model_provider=ciel", "model_providers.ciel.base_url=http://localhost"],
            {"CODEX_RUNTIME_KEY": "runtime-secret"},
        )

        messages = "\n".join(message for _, message in self.events)
        self.assertIn("provider_overrides=2", messages)
        self.assertIn("CODEX_RUNTIME_KEY=masked:et", messages)
        self.assertNotIn("runtime-secret", messages)

    def test_agy_reports_routed_launch(self):
        self.diagnostics.agy(
            ["agy", "--dangerously-skip-permissions"],
            {"CIEL_RUNTIME_MODEL_ALIAS": "reasoning"},
        )

        messages = "\n".join(message for _, message in self.events)
        self.assertIn("routed_flags=yes", messages)
        self.assertIn("CIEL_RUNTIME_MODEL_ALIAS=reasoning", messages)


if __name__ == "__main__":
    unittest.main()
