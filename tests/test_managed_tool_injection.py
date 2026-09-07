import subprocess
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.managed_tool_injection import should_inject_tool, codex_native_web_tool_overrides


class ManagedToolInjectionTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("ciel_runtime_support.managed_tool_injection.run_codex_config_probe")
        self.probe = patcher.start()
        self.addCleanup(patcher.stop)
        self.probe.return_value = mock.Mock(returncode=0, stdout="[]")

    def test_codex_native_disables_only_replacement_web_servers(self):
        self.probe.return_value.stdout = '[{"name":"duckduckgo"},{"name":"web_fetch"},{"name":"other"}]'
        self.assertEqual([
            "-c", "mcp_servers.duckduckgo.enabled=false",
            "-c", "mcp_servers.web_fetch.enabled=false",
        ], codex_native_web_tool_overrides(native=True))

    def test_nonnative_does_not_probe_or_override(self):
        self.assertEqual([], codex_native_web_tool_overrides(native=False))
        self.probe.assert_not_called()

    def test_empty_effective_configuration_does_not_create_servers(self):
        self.assertEqual([], codex_native_web_tool_overrides(native=True))

    def test_only_registered_server_is_disabled(self):
        self.probe.return_value.stdout = '[{"name":"duckduckgo"}]'
        self.assertEqual(
            ["-c", "mcp_servers.duckduckgo.enabled=false"],
            codex_native_web_tool_overrides(native=True),
        )

    def test_probe_uses_same_executable_env_cwd_and_config_not_prompt(self):
        arguments = ['--yolo', '-p', 'work', '--config=model="test"', '-C', 'workspace',
                     '-c', 'mcp_servers.web_fetch.command="fetch"', 'resume', 'session-id',
                     '--', 'private prompt']
        env = {"CODEX_HOME": "isolated"}
        cwd = Path("workspace")
        codex_native_web_tool_overrides(native=True, codex="selected-codex", passthrough=arguments, env=env, cwd=cwd)
        self.probe.assert_called_once_with(
            ['selected-codex', '-p', 'work', '--config=model="test"', '-C', 'workspace',
             '-c', 'mcp_servers.web_fetch.command="fetch"', 'mcp', 'list', '--json'],
            env=env, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=5, check=False,
        )

    def test_failed_probe_never_injects_or_logs_config_secrets(self):
        self.probe.return_value = mock.Mock(returncode=1, stdout="private-secret", stderr="private-secret")
        with self.assertLogs("ciel_runtime_support.managed_tool_injection", level="WARNING") as logs:
            self.assertEqual([], codex_native_web_tool_overrides(native=True))
        self.assertNotIn("private-secret", str(logs.output))

    def test_probe_timeout_and_bad_output_are_safe(self):
        for error in (OSError("unavailable"), subprocess.TimeoutExpired("codex", 5)):
            self.probe.side_effect = error
            with self.assertLogs(level="WARNING"):
                self.assertEqual([], codex_native_web_tool_overrides(native=True))
        self.probe.side_effect = None
        for output in ("invalid", "{}"):
            self.probe.return_value.stdout = output
            with self.assertLogs(level="WARNING"):
                self.assertEqual([], codex_native_web_tool_overrides(native=True))

    def test_launch_mode_matrix(self):
        for native in (True, False):
            self.assertTrue(should_inject_tool(native=native))
            self.assertEqual(native, should_inject_tool(native=native, mode="native"))
            self.assertEqual(not native, should_inject_tool(native=native, mode="non_native"))

    def test_invalid_mode_is_not_silently_injected(self):
        with self.assertRaises(ValueError):
            should_inject_tool(native=True, mode="typo")
