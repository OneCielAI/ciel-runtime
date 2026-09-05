import unittest

from ciel_runtime_support.managed_tool_injection import should_inject_tool


class ManagedToolInjectionTests(unittest.TestCase):
    def test_launch_mode_matrix(self):
        for native in (True, False):
            self.assertTrue(should_inject_tool(native=native))
            self.assertEqual(native, should_inject_tool(native=native, mode="native"))
            self.assertEqual(not native, should_inject_tool(native=native, mode="non_native"))

    def test_invalid_mode_is_not_silently_injected(self):
        with self.assertRaises(ValueError):
            should_inject_tool(native=True, mode="typo")
