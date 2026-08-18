from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support.workspace_mcp import (
    WorkspaceMcpLaunchPorts,
    WorkspaceMcpLaunchService,
    WorkspaceMcpMenuService,
    project_claude_mcp,
    project_codex_mcp_args,
    workspace_mcp_servers,
)


def sample_config() -> dict:
    return {
        "workspace_mcp": {
            "servers": {
                "local-tools": {
                    "enabled": True,
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@example/tools"],
                    "cwd": "G:/ncc",
                    "env": {"TOKEN": "%TOOLS_TOKEN%"},
                    "runtimes": ["claude", "codex"],
                    "protocol": "auto",
                },
                "remote-tools": {
                    "enabled": True,
                    "transport": "streamable_http",
                    "url": "https://example.test/mcp",
                    "env_http_headers": {"Authorization": "TOOLS_AUTH"},
                    "runtimes": ["codex"],
                    "required": True,
                },
                "disabled": {
                    "enabled": False,
                    "transport": "stdio",
                    "command": "ignored",
                },
            }
        }
    }


class WorkspaceMcpProjectionTests(unittest.TestCase):
    def test_runtime_filter_and_claude_projection(self) -> None:
        servers = workspace_mcp_servers(sample_config(), "claude")
        self.assertEqual(["local-tools"], sorted(servers))
        projected = project_claude_mcp(servers)["mcpServers"]["local-tools"]
        self.assertEqual("stdio", projected["type"])
        self.assertEqual("npx", projected["command"])
        self.assertEqual(["-y", "@example/tools"], projected["args"])
        self.assertEqual("G:/ncc", projected["cwd"])

    def test_codex_projection_preserves_stdio_and_http_semantics(self) -> None:
        servers = workspace_mcp_servers(sample_config(), "codex")
        args = project_codex_mcp_args(servers)
        joined = "\n".join(args)
        self.assertIn('mcp_servers.local-tools.command="npx"', joined)
        self.assertIn('mcp_servers.local-tools.args=["-y","@example/tools"]', joined)
        self.assertIn('mcp_servers.remote-tools.url="https://example.test/mcp"', joined)
        self.assertIn(
            'mcp_servers.remote-tools.env_http_headers.Authorization="TOOLS_AUTH"',
            joined,
        )
        self.assertIn("mcp_servers.remote-tools.required=true", joined)

    def test_invalid_and_incomplete_servers_are_not_projected(self) -> None:
        config = {
            "workspace_mcp": {
                "servers": {
                    "bad id": {"transport": "stdio", "command": "x"},
                    "missing-command": {"transport": "stdio"},
                    "missing-url": {"transport": "streamable_http"},
                }
            }
        }
        self.assertEqual({}, workspace_mcp_servers(config, "claude"))


class WorkspaceMcpLeaseTests(unittest.TestCase):
    def service(
        self,
        root: Path,
        running: set[int],
        terminated: list[int],
        command_lines: dict[int, str] | None = None,
    ) -> WorkspaceMcpLaunchService:
        command_lines = command_lines or {}
        return WorkspaceMcpLaunchService(
            root,
            "G:/ncc",
            WorkspaceMcpLaunchPorts(
                pid_running=lambda pid: pid in running,
                command_line=lambda pid: command_lines.get(pid, ""),
                terminate_tree=lambda pid, _label, quiet=True: terminated.append(pid) or True,
                log=lambda _level, _message: None,
                current_pid=lambda: 11,
            ),
        )

    def test_prepare_and_normal_finish_remove_ephemeral_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "mcp-launches"
            service = self.service(root, {11}, [])
            launch = service.prepare("claude", sample_config())
            self.assertTrue(launch.active)
            self.assertTrue(launch.manifest_path and launch.manifest_path.exists())
            self.assertTrue(launch.claude_config_paths[0].exists())
            manifest = json.loads(launch.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual("G:/ncc", manifest["workspace"])
            self.assertEqual(["local-tools"], manifest["server_names"])
            service.finish(launch)
            self.assertFalse(launch.directory and launch.directory.exists())

    def test_active_owner_lease_is_not_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "mcp-launches"
            service = self.service(root, {11}, [])
            launch = service.prepare("codex", sample_config())
            service.recover_stale()
            self.assertTrue(launch.directory and launch.directory.exists())

    def test_stale_owner_recovers_verified_runtime_child_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "mcp-launches"
            terminated: list[int] = []
            creator = self.service(root, {11}, terminated)
            launch = creator.prepare("codex", sample_config())
            assert launch.child_record_path is not None
            command = ["C:/tools/codex.exe", "--yolo", *launch.codex_args]
            launch.child_record_path.write_text(
                json.dumps({"pid": 22, "cmd": command}),
                encoding="utf-8",
            )
            recovery = self.service(root, {22}, terminated, {22: " ".join(command)})
            recovery.recover_stale()
            self.assertEqual([22], terminated)
            self.assertFalse(launch.directory and launch.directory.exists())

    def test_pid_identity_mismatch_is_never_terminated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "mcp-launches"
            terminated: list[int] = []
            creator = self.service(root, {11}, terminated)
            launch = creator.prepare("codex", sample_config())
            assert launch.child_record_path is not None
            launch.child_record_path.write_text(
                json.dumps({"pid": 23, "cmd": ["C:/tools/codex.exe", "--yolo"]}),
                encoding="utf-8",
            )
            recovery = WorkspaceMcpLaunchService(
                root,
                "G:/ncc",
                WorkspaceMcpLaunchPorts(
                    pid_running=lambda pid: pid == 23,
                    command_line=lambda _pid: "C:/Windows/notepad.exe",
                    terminate_tree=lambda pid, _label, quiet=True: terminated.append(pid) or True,
                    log=lambda _level, _message: None,
                    current_pid=lambda: 99,
                ),
            )
            recovery.recover_stale()
            self.assertEqual([], terminated)

    def test_reused_codex_pid_without_launch_projection_is_never_terminated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "mcp-launches"
            terminated: list[int] = []
            creator = self.service(root, {11}, terminated)
            launch = creator.prepare("codex", sample_config())
            assert launch.child_record_path is not None
            command = ["C:/tools/codex.exe", "--yolo", *launch.codex_args]
            launch.child_record_path.write_text(
                json.dumps({"pid": 22, "cmd": command}),
                encoding="utf-8",
            )
            recovery = self.service(
                root,
                {22},
                terminated,
                {22: "C:/tools/codex.exe --yolo resume another-session"},
            )
            recovery.recover_stale()
            self.assertEqual([], terminated)


class WorkspaceMcpMenuTests(unittest.TestCase):
    def test_menu_updates_only_supplied_workspace_config(self) -> None:
        state = {"workspace_mcp": {"servers": {}}}
        service = WorkspaceMcpMenuService(
            lambda: json.loads(json.dumps(state)),
            lambda config: state.clear() or state.update(config),
            "G:/ncc",
        )
        service.update(
            "add-stdio",
            {
                "name": "repo-tools",
                "command": "uvx",
                "args": ["repo-mcp"],
                "runtimes": ["claude"],
            },
        )
        self.assertIn("repo-tools", state["workspace_mcp"]["servers"])
        service.update("toggle:repo-tools")
        self.assertFalse(state["workspace_mcp"]["servers"]["repo-tools"]["enabled"])
        service.update("remove", "repo-tools")
        self.assertEqual({}, state["workspace_mcp"]["servers"])


if __name__ == "__main__":
    unittest.main()
