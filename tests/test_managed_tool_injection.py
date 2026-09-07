import unittest

from ciel_runtime_support.managed_tool_injection import should_inject_tool, codex_native_web_tool_overrides


class ManagedToolInjectionTests(unittest.TestCase):
    def test_codex_native_disables_only_replacement_web_servers(self):
        self.assertEqual([
            "-c", "mcp_servers.duckduckgo.enabled=false",
            "-c", "mcp_servers.web_fetch.enabled=false",
        ], codex_native_web_tool_overrides(native=True))
        self.assertEqual([], codex_native_web_tool_overrides(native=False))

    def test_launch_mode_matrix(self):
        for native in (True, False):
            self.assertTrue(should_inject_tool(native=native))
            self.assertEqual(native, should_inject_tool(native=native, mode="native"))
            self.assertEqual(not native, should_inject_tool(native=native, mode="non_native"))

    def test_invalid_mode_is_not_silently_injected(self):
        with self.assertRaises(ValueError):
            should_inject_tool(native=True, mode="typo")
