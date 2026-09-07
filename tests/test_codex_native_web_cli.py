"""Opt-in real Codex config-loader regression checks; no LLM/MCP connections."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ciel_runtime_support.managed_tool_injection import codex_native_web_tool_overrides


@unittest.skipUnless(os.environ.get("CIEL_TEST_CODEX_EXE"), "set CIEL_TEST_CODEX_EXE for real CLI verification")
class CodexNativeWebCliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="ciel-codex-web-cli-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.workspaces = [self.root / "native", self.root / "nonnative"]
        for workspace in self.workspaces:
            workspace.mkdir()
        self.env = dict(os.environ, CODEX_HOME=str(self.home))
        self.exe = os.environ["CIEL_TEST_CODEX_EXE"]

    def listing(self, workspace, arguments):
        result = subprocess.run(
            [self.exe, *arguments, "mcp", "list", "--json"], cwd=workspace,
            env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return {entry["name"]: entry["enabled"] for entry in json.loads(result.stdout)}

    def overrides(self, workspace, native=True, arguments=None):
        return codex_native_web_tool_overrides(
            native=native, codex=self.exe, env=self.env, cwd=workspace, passthrough=arguments,
        )

    def test_empty_home_starts_without_invalid_transport(self):
        flags = self.overrides(self.workspaces[0])
        self.assertEqual([], flags)
        self.assertEqual({}, self.listing(self.workspaces[0], flags))

    def test_same_home_concurrent_native_and_nonnative_do_not_change_toml(self):
        config = self.home / "config.toml"
        config.write_text(
            '[mcp_servers.duckduckgo]\ncommand="ddg-not-executed"\n'
            '[mcp_servers.web_fetch]\nurl="https://example.invalid/mcp"\n', encoding="utf-8",
        )
        before = hashlib.sha256(config.read_bytes()).hexdigest()

        def run_case(index):
            workspace = self.workspaces[index]
            return self.listing(workspace, self.overrides(workspace, native=index == 0))

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            native, nonnative = list(pool.map(run_case, (0, 1)))
        self.assertEqual({"duckduckgo": False, "web_fetch": False}, native)
        self.assertEqual({"duckduckgo": True, "web_fetch": True}, nonnative)
        self.assertEqual(before, hashlib.sha256(config.read_bytes()).hexdigest())
        self.assertFalse(any(workspace.joinpath(".codex", "config.toml").exists() for workspace in self.workspaces))

        # Workspace-local files alone cannot isolate two provider modes in the
        # same workspace. Process overrides must handle that case as well.
        self.workspaces[1] = self.workspaces[0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            native, nonnative = list(pool.map(run_case, (0, 1)))
        self.assertEqual({"duckduckgo": False, "web_fetch": False}, native)
        self.assertEqual({"duckduckgo": True, "web_fetch": True}, nonnative)
        self.assertEqual(before, hashlib.sha256(config.read_bytes()).hexdigest())

    def test_workspace_transport_obeys_codex_trust_resolution(self):
        workspace = self.workspaces[0]
        (workspace / ".codex").mkdir()
        project_config = workspace / ".codex" / "config.toml"
        project_config.write_text('[mcp_servers.duckduckgo]\ncommand="ddg-not-executed"\n', encoding="utf-8")
        original = project_config.read_bytes()
        for trust in ("untrusted", "trusted"):
            (self.home / "config.toml").write_text(
                f'[projects.{json.dumps(str(workspace))}]\ntrust_level="{trust}"\n', encoding="utf-8",
            )
            baseline = self.listing(workspace, [])
            effective = self.listing(workspace, self.overrides(workspace))
            self.assertEqual({name: False if name == "duckduckgo" else value for name, value in baseline.items()}, effective)
            self.assertEqual(original, project_config.read_bytes())

    def test_generated_transport_disabled_only_for_native_process(self):
        workspace = self.workspaces[0]
        arguments = ['-c', 'mcp_servers.duckduckgo.command="ddg-not-executed"']
        self.assertEqual({"duckduckgo": False}, self.listing(workspace, arguments + self.overrides(workspace, arguments=arguments)))
        self.assertEqual({"duckduckgo": True}, self.listing(workspace, arguments + self.overrides(workspace, native=False, arguments=arguments)))
