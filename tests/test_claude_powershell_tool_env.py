import unittest

from ciel_runtime_support.claude_environment import CLAUDE_POWERSHELL_TOOL_ENV, default_powershell_tool


class PowerShellToolDefaultTests(unittest.TestCase):
    def test_windows_launch_enables_the_tool(self):
        env = {"PATH": "x"}
        self.assertIs(default_powershell_tool(env, platform="win32"), env)
        self.assertEqual(env[CLAUDE_POWERSHELL_TOOL_ENV], "1")

    def test_explicit_user_value_is_kept(self):
        for value in ("0", "1", "false"):
            env = {CLAUDE_POWERSHELL_TOOL_ENV: value}
            default_powershell_tool(env, platform="win32")
            self.assertEqual(env[CLAUDE_POWERSHELL_TOOL_ENV], value)

    def test_blank_value_counts_as_unset(self):
        env = {CLAUDE_POWERSHELL_TOOL_ENV: "  "}
        default_powershell_tool(env, platform="win32")
        self.assertEqual(env[CLAUDE_POWERSHELL_TOOL_ENV], "1")

    def test_other_platforms_are_unchanged(self):
        for platform in ("linux", "darwin"):
            env = {}
            default_powershell_tool(env, platform=platform)
            self.assertEqual(env, {})
