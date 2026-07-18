import unittest
from dataclasses import fields
from unittest import mock

import ciel_runtime
from ciel_runtime_support.prelaunch import PrelaunchServices


class PrelaunchWiringTests(unittest.TestCase):
    def test_portable_menu_builds_bounded_prelaunch_ports(self):
        with mock.patch.object(ciel_runtime, "execute_prelaunch_menu", return_value=17) as execute:
            result = ciel_runtime.portable_prelaunch_menu(["--verbose"])

        self.assertEqual(17, result)
        services = execute.call_args.kwargs["services"]
        self.assertIsInstance(services, PrelaunchServices)
        self.assertEqual(10, len(fields(services)))
        self.assertEqual(["--verbose"], execute.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
