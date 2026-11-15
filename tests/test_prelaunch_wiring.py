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
        self.assertEqual(8, len(fields(services)))
        self.assertEqual(["--verbose"], execute.call_args.args[0])

    def test_continue_session_selector_skips_menu(self):
        with (
            mock.patch.object(ciel_runtime.sys.stdin, "isatty", return_value=True),
            mock.patch.object(ciel_runtime.sys.stdout, "isatty", return_value=True),
            mock.patch.object(ciel_runtime, "portable_prelaunch_menu") as menu,
        ):
            result = ciel_runtime.run_prelaunch_menu(["--continue"])

        self.assertEqual(0, result)
        menu.assert_not_called()

    def test_resume_equals_session_selector_skips_menu(self):
        with (
            mock.patch.object(ciel_runtime.sys.stdin, "isatty", return_value=True),
            mock.patch.object(ciel_runtime.sys.stdout, "isatty", return_value=True),
            mock.patch.object(ciel_runtime, "portable_prelaunch_menu") as menu,
        ):
            result = ciel_runtime.run_prelaunch_menu(["--resume=session-id"])

        self.assertEqual(0, result)
        menu.assert_not_called()

    def test_force_menu_overrides_continue_session_selector(self):
        with (
            mock.patch.object(ciel_runtime.sys.stdin, "isatty", return_value=True),
            mock.patch.object(ciel_runtime.sys.stdout, "isatty", return_value=True),
            mock.patch.object(ciel_runtime, "portable_prelaunch_menu", return_value=19) as menu,
        ):
            result = ciel_runtime.run_prelaunch_menu(["--continue"], force_menu=True)

        self.assertEqual(19, result)
        menu.assert_called_once_with(["--continue"])

    def test_show_config_flag_opens_the_menu_with_continue_and_is_not_passed_on(self):
        for flag in ("--show_config", "--show-config"):
            with self.subTest(flag=flag), (
                mock.patch.object(ciel_runtime, "apply_headless_env_config",
                                  return_value=(False, None, None, None, False))
            ), mock.patch.object(ciel_runtime, "launch_claude", return_value=0) as launch:
                self.assertEqual(0, ciel_runtime.run_cli(["--continue", flag]))
            launch.assert_called_once()
            self.assertEqual(["--continue"], launch.call_args.args[0])
            self.assertTrue(launch.call_args.kwargs["force_menu"])

    def test_continue_without_show_config_still_skips_the_menu(self):
        with (
            mock.patch.object(ciel_runtime, "apply_headless_env_config",
                              return_value=(False, None, None, None, False)),
            mock.patch.object(ciel_runtime, "launch_claude", return_value=0) as launch,
        ):
            self.assertEqual(0, ciel_runtime.run_cli(["--continue"]))
        self.assertEqual(["--continue"], launch.call_args.args[0])
        self.assertFalse(launch.call_args.kwargs["force_menu"])

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
