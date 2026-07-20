import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.ollama_catalog_cli import (
    OllamaCatalogCliController,
)


class OllamaCatalogCliControllerTests(unittest.TestCase):
    def test_refresh_command_reports_catalog_summary(self):
        output: list[str] = []
        refresh = mock.Mock(
            return_value={
                "model_count": 3,
                "models": {
                    "one": {"context_windows": {"latest": 32_768}},
                    "two": {"context_windows": {}},
                },
            }
        )
        controller = OllamaCatalogCliController(
            refresh=refresh,
            catalog_path=Path("catalog.json"),
            output=output.append,
        )

        controller.refresh_command(include_contexts=False, timeout=4.5)

        refresh.assert_called_once_with(
            include_contexts=False,
            timeout=4.5,
        )
        self.assertIn("API models: 3", output)
        self.assertIn("Base models: 2", output)
        self.assertIn("Context windows: 1/2", output)


if __name__ == "__main__":
    unittest.main()
