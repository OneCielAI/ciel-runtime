"""The un-isolated test guard must fail closed on destructive cleanup.

A sweep that discovers tests outside the supported entrypoint used to reach
the real instance directory and terminate the router and the client the
running session was using (observed 2026-09-18: the CLI exiting mid-sweep).
"""

import os
import unittest
from unittest import mock

import ciel_runtime


class UnisolatedTestGuardTests(unittest.TestCase):
    def is_test_invocation(self, arguments):
        # The argv shape is judged on its own: a test process always carries
        # unittest in sys.modules, so the overall guard cannot tell them apart.
        return ciel_runtime._test_runner_arguments(arguments)

    def test_discovery_and_module_shapes_are_recognized(self):
        for argv in (
            ["ciel_runtime.py", "discover", "-s", "tests", "-p", "test_a.py"],
            ["ciel_runtime.py", "tests/test_a.py"],
            ["ciel_runtime.py", "tests.test_a"],
            ["ciel_runtime.py", "test_a.py"],
        ):
            with self.subTest(argv=argv):
                self.assertTrue(self.is_test_invocation(argv[1:]))

    def test_ordinary_launches_are_not_mistaken_for_tests(self):
        for argv in (
            ["ciel_runtime.py", "serve"],
            ["ciel_runtime.py", "cli", "--continue"],
            ["ciel_runtime.py", "codex", "tests"],
            ["ciel_runtime.py", "codex", "fix the tests folder"],
        ):
            with self.subTest(argv=argv):
                self.assertFalse(self.is_test_invocation(argv[1:]))

    def test_active_client_termination_fails_closed(self):
        registry = mock.MagicMock()
        with (
            mock.patch.object(ciel_runtime, "_unisolated_test_process", return_value=True),
            mock.patch.object(ciel_runtime, "router_client_registry", return_value=registry),
            mock.patch.object(ciel_runtime, "router_log") as log,
        ):
            result = ciel_runtime.terminate_active_router_clients("prelaunch", [1234])

        self.assertFalse(result)
        registry.terminate_active.assert_not_called()
        self.assertIn("unisolated_test", log.call_args.args[1])

    def test_pid_tree_termination_fails_closed_but_keeps_self(self):
        controller = mock.MagicMock()
        with (
            mock.patch.object(ciel_runtime, "_unisolated_test_process", return_value=True),
            mock.patch.object(ciel_runtime, "process_tree_controller", return_value=controller),
            mock.patch.object(ciel_runtime, "router_log"),
            mock.patch.object(ciel_runtime.os, "getpid", return_value=os.getpid()),
            mock.patch.object(ciel_runtime.os, "getppid", return_value=4242),
        ):
            self.assertFalse(ciel_runtime.terminate_pid_tree(2468, "router"))
            self.assertTrue(ciel_runtime.terminate_pid_tree(4242, "own child"))

        controller.terminate_tree.assert_called_once_with(4242, "own child", quiet=False)


if __name__ == "__main__":
    unittest.main()
