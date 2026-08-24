import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.codex_config import repair_codex_mcp_header_collisions

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    tomllib = None


@unittest.skipIf(tomllib is None, "tomllib is unavailable on Python 3.10")
class CodexConfigRepairTests(unittest.TestCase):
    def test_repairs_only_identical_legacy_and_inline_mcp_headers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(
                '''model = "gpt-test"

[mcp_servers.ai-net.http_headers]
Authorization = "Bearer shared"
X-AI-Net-Push = "off"

[mcp_servers.ai-net]
url = "https://example.test/mcp"
http_headers = { Authorization = "Bearer shared", X-AI-Net-Push = "off" }
required = true
''',
                encoding="utf-8",
            )
            reports = []

            repaired = repair_codex_mcp_header_collisions([path], report=reports.append)

            self.assertEqual([path], repaired)
            parsed = tomllib.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                {"Authorization": "Bearer shared", "X-AI-Net-Push": "off"},
                parsed["mcp_servers"]["ai-net"]["http_headers"],
            )
            self.assertTrue(parsed["mcp_servers"]["ai-net"]["required"])
            self.assertEqual(1, len(list(path.parent.glob("config.toml.ciel-mcp-repair-*.bak"))))
            self.assertIn("ai-net", reports[0])

    def test_does_not_repair_when_header_values_differ(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            original = '''[mcp_servers.ai-net.http_headers]
Authorization = "Bearer old"

[mcp_servers.ai-net]
url = "https://example.test/mcp"
http_headers = { Authorization = "Bearer new" }
'''
            path.write_text(original, encoding="utf-8")

            self.assertEqual([], repair_codex_mcp_header_collisions([path]))
            self.assertEqual(original, path.read_text(encoding="utf-8"))
            self.assertEqual([], list(path.parent.glob("*.bak")))

    def test_does_not_modify_an_unrelated_invalid_toml_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            original = 'model = "unterminated\n'
            path.write_text(original, encoding="utf-8")

            self.assertEqual([], repair_codex_mcp_header_collisions([path]))
            self.assertEqual(original, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
