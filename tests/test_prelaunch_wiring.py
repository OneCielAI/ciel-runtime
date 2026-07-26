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

    def test_web_config_reload_runs_after_menu_returns_and_cancels_parent(self):
        with (
            mock.patch.object(ciel_runtime.sys.stdin, "isatty", return_value=True),
            mock.patch.object(ciel_runtime.sys.stdout, "isatty", return_value=True),
            mock.patch.object(
                ciel_runtime,
                "portable_prelaunch_menu",
                return_value=ciel_runtime.PRELAUNCH_RELOAD,
            ),
            mock.patch.object(ciel_runtime.subprocess, "call", return_value=0) as call,
        ):
            result = ciel_runtime.run_prelaunch_menu([], force_menu=True)

        self.assertEqual(ciel_runtime.PRELAUNCH_CANCEL, result)
        call.assert_called_once_with([ciel_runtime.sys.executable, *ciel_runtime.sys.argv])


if __name__ == "__main__":
    unittest.main()
