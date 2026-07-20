import json
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support.mcp_config_reader import (
    ClaudeMcpConfigPathPolicy,
    discover_channel_specs,
    read_mcp_config_items,
    server_names_from_mapping,
    servers_from_mapping,
)


class McpConfigReaderTests(unittest.TestCase):
    def test_channel_discovery_tags_names_and_filters_invalid_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mcp.json"
            path.write_text("{}", encoding="utf-8")

            specs = discover_channel_specs(
                [path, Path(tmp) / "missing.json"],
                Path(tmp),
                lambda _path, _cwd: [
                    "plain",
                    "server:tagged",
                    "has whitespace",
                    "plain",
                ],
                lambda name: name.startswith(("server:", "plugin:")),
            )

        self.assertEqual(["server:plain", "server:tagged"], specs)
    def test_claude_path_policy_parses_and_strips_passthrough(self):
        args = [
            "--mcp-config",
            "a.json",
            "b.json",
            "--verbose",
            "--mcp-config=c.json",
        ]
        self.assertEqual(
            ["a.json", "b.json", "c.json"],
            ClaudeMcpConfigPathPolicy.passthrough_values(args),
        )
        self.assertEqual(
            ["--verbose"],
            ClaudeMcpConfigPathPolicy.strip_passthrough(args),
        )

    def test_claude_path_policy_discovers_ancestors_and_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            project = home / "work" / "nested"
            project.mkdir(parents=True)
            project_config = project / ".mcp.json"
            home_config = home / ".mcp.json"
            project_config.write_text("{}", encoding="utf-8")
            home_config.write_text("{}", encoding="utf-8")

            paths = ClaudeMcpConfigPathPolicy.paths([], project, home)
            existing = ClaudeMcpConfigPathPolicy.existing_paths(
                [],
                project,
                home,
            )

        self.assertIn(project_config, paths)
        self.assertIn(home_config, paths)
        self.assertEqual(
            [project_config, home_config],
            [
                path
                for path in existing
                if path in {project_config, home_config}
            ],
        )

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
