import json
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support.mcp_config_reader import (
    read_mcp_config_items,
    server_names_from_mapping,
    servers_from_mapping,
)


class McpConfigReaderTests(unittest.TestCase):
    def test_root_and_matching_project_scope_are_projected_and_deduped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            path = root / ".claude.json"
            path.write_text(
                json.dumps(
                    {
                        "mcpServers": {"root": {"command": "root"}},
                        "projects": {
                            str(project): {
                                "mcpServers": {
                                    "root": {"command": "override"},
                                    "project": {"command": "project"},
                                }
                            },
                            str(root / "other"): {
                                "mcpServers": {"other": {"command": "other"}}
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            names = read_mcp_config_items(
                path,
                project,
                server_names_from_mapping,
                str,
                lambda _level, _message: None,
            )
            servers = read_mcp_config_items(
                path,
                project,
                servers_from_mapping,
                lambda item: item[0],
                lambda _level, _message: None,
            )

            self.assertEqual(["root", "project"], names)
            self.assertEqual(["root", "project"], [name for name, _server in servers])
            self.assertEqual("root", servers[0][1]["command"])

    def test_corrupt_config_is_observable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mcp.json"
            path.write_text("{broken", encoding="utf-8")
            events = []

            items = read_mcp_config_items(
                path,
                Path(tmp),
                server_names_from_mapping,
                str,
                lambda level, message: events.append((level, message)),
            )

            self.assertEqual([], items)
            self.assertEqual("WARN", events[0][0])
            self.assertIn(str(path), events[0][1])


if __name__ == "__main__":
    unittest.main()
