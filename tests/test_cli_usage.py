import unittest

from ciel_runtime_support.cli_usage import cli_usage_text


class CliUsageTests(unittest.TestCase):
    def test_usage_covers_runtime_and_headless_entrypoints(self):
        usage = cli_usage_text()

        self.assertIn("ciel-runtime codex [args...]", usage)
        self.assertIn("ciel-runtime agy [args...]", usage)
        self.assertIn("--ca-provider PROVIDER", usage)
        self.assertIn("Provider names:", usage)


if __name__ == "__main__":
    unittest.main()
