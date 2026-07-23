import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ciel_runtime_support.agy_mcp_restore import (
    AgyMcpRestorePorts,
    AgyMcpRestoreService,
)
from ciel_runtime_support.mcp_inventory import McpInventoryService


class McpInventoryServiceTests(unittest.TestCase):
    def test_native_inventory_wins_case_insensitively(self):
        merged = McpInventoryService.merge(
            {"GitHub": {"command": "native"}},
            {
                "github": {"command": "managed"},
                "search": {"command": "managed-search"},
            },
        )

        self.assertEqual(("search",), merged.added)
        self.assertEqual(("github",), merged.duplicates)
        self.assertEqual("native", merged.servers["GitHub"]["command"])


class AgyMcpRestoreServiceTests(unittest.TestCase):
    @staticmethod
    def service(managed, logs):
        return AgyMcpRestoreService(
            AgyMcpRestorePorts(lambda _cwd: managed, lambda *entry: logs.append(entry))
        )

    def test_restores_missing_servers_and_preserves_native_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            global_path = root / ".gemini" / "antigravity-cli" / "mcp_config.json"
            global_path.parent.mkdir(parents=True)
            global_path.write_text(
                json.dumps(
                    {
                        "customSetting": True,
                        "mcpServers": {
                            "GitHub": {"command": "native-github", "disabled": True}
                        },
                    }
                ),
                encoding="utf-8",
            )
            logs = []
            restored = self.service(
                {
                    "github": {"command": "managed-github"},
                    "search": {
                        "command": "uvx",
                        "args": ["search-mcp"],
                        "env": {"TOKEN": "secret"},
                    },
                    "remote": {
                        "url": "https://example.test/mcp",
                        "http_headers": {"X-Token": "value"},
                    },
                },
                logs,
            ).restore(env={"HOME": str(root)}, cwd=root)

            data = json.loads(global_path.read_text(encoding="utf-8"))

        self.assertEqual(["search", "remote"], restored)
        self.assertTrue(data["customSetting"])
        self.assertEqual("native-github", data["mcpServers"]["GitHub"]["command"])
        self.assertEqual(["search-mcp"], data["mcpServers"]["search"]["args"])
        self.assertEqual("https://example.test/mcp", data["mcpServers"]["remote"]["serverUrl"])
        self.assertEqual({"X-Token": "value"}, data["mcpServers"]["remote"]["headers"])
        self.assertTrue(any("agy_mcp_config_restored" in message for _, message in logs))

    def test_workspace_config_reserves_names_without_being_modified(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "project"
            workspace_path = workspace / ".agents" / "mcp_config.json"
            workspace_path.parent.mkdir(parents=True)
            original = {"mcpServers": {"Search": {"command": "workspace-search"}}}
            workspace_path.write_text(json.dumps(original), encoding="utf-8")

            restored = self.service(
                {
                    "search": {"command": "managed-search"},
                    "other": {"command": "managed-other"},
                },
                [],
            ).restore(env={"HOME": str(root)}, cwd=workspace)
            global_data = json.loads(
                (root / ".gemini" / "antigravity-cli" / "mcp_config.json").read_text(
                    encoding="utf-8"
                )
            )
            workspace_data = json.loads(workspace_path.read_text(encoding="utf-8"))

        self.assertEqual(["other"], restored)
        self.assertEqual(original, workspace_data)
        self.assertEqual(["other"], list(global_data["mcpServers"]))

    def test_invalid_native_config_aborts_without_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / ".gemini" / "antigravity-cli" / "mcp_config.json"
            path.parent.mkdir(parents=True)
            path.write_text("{invalid", encoding="utf-8")
            logs = []

            restored = self.service(
                {"search": {"command": "managed-search"}}, logs
            ).restore(env={"HOME": str(root)}, cwd=root)

            self.assertEqual([], restored)
            self.assertEqual("{invalid", path.read_text(encoding="utf-8"))
            self.assertIn("agy_mcp_restore_config_read_failed", logs[-1][1])

    def test_proxy_wrapper_is_never_written_to_native_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = []
            restored = self.service(
                {
                    "wrapped": {
                        "command": "python",
                        "args": ["mcp-proxy", "--server-config", "server.json"],
                    }
                },
                logs,
            ).restore(env={"HOME": str(root)}, cwd=root)

        self.assertEqual([], restored)
        self.assertFalse((root / ".gemini").exists())
        self.assertIn("agy_mcp_restore_skipped_unsupported", logs[-1][1])


if __name__ == "__main__":
    unittest.main()
