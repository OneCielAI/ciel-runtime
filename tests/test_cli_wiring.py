import unittest
from dataclasses import fields
from unittest import mock

import ciel_runtime
from ciel_runtime_support.cli_dispatch import CliServices


class CliWiringTests(unittest.TestCase):
    def test_run_cli_builds_bounded_command_ports(self):
        with mock.patch.object(ciel_runtime, "dispatch_cli", return_value=23) as dispatch:
            result = ciel_runtime.run_cli(["version"])

        self.assertEqual(23, result)
        services = dispatch.call_args.args[1]
        self.assertIsInstance(services, CliServices)
        self.assertEqual(7, len(fields(services)))


if __name__ == "__main__":
    unittest.main()
