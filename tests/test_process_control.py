import json
import subprocess
import unittest
from unittest import mock

from ciel_runtime_support.process_control import (
    ProcessControlServices,
    ProcessQueryServices,
    ProcessSignalServices,
    terminate_matching_processes,
)


class ProcessControlTests(unittest.TestCase):
    def test_windows_process_query_and_termination_are_separate_effects(self):
        run = mock.Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=json.dumps(
                        [
                            {"ProcessId": 10, "CommandLine": "ciel target"},
                            {"ProcessId": 12, "CommandLine": "ciel target"},
                            {"ProcessId": 13, "CommandLine": "other"},
                        ]
                    ),
                ),
                subprocess.CompletedProcess([], 0),
            ]
        )
        output: list[str] = []
        services = ProcessControlServices(
            query=ProcessQueryServices(run=run, current_pid=lambda: 10, parent_pid=lambda: 11),
            signals=ProcessSignalServices(kill=mock.Mock(), pid_is_running=lambda _pid: False),
            log=mock.Mock(),
            output=output.append,
        )

        stopped = terminate_matching_processes(["ciel", "target"], "target", services, platform_name="nt")

        self.assertTrue(stopped)
        self.assertEqual(["taskkill", "/PID", "12", "/T", "/F"], run.call_args_list[1].args[0])
        self.assertEqual(["Stopped existing target session(s): 12."], output)

    def test_posix_termination_skips_parent_and_zombie_processes(self):
        run = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [],
                0,
                stdout="10 S ciel target\n12 S ciel target\n13 Z ciel target\n",
            )
        )
        kill = mock.Mock()
        services = ProcessControlServices(
            query=ProcessQueryServices(
                run=run,
                username=lambda: "agent",
                current_pid=lambda: 9,
                parent_pid=lambda: 10,
            ),
            signals=ProcessSignalServices(
                kill=kill,
                pid_is_running=lambda _pid: False,
                now=lambda: 0.0,
                sleep=mock.Mock(),
            ),
            log=mock.Mock(),
            output=mock.Mock(),
        )

        stopped = terminate_matching_processes(["ciel", "target"], "target", services, platform_name="posix")

        self.assertTrue(stopped)
        kill.assert_called_once()
        self.assertEqual(12, kill.call_args.args[0])

    def test_query_failure_is_observable(self):
        log = mock.Mock()
        services = ProcessControlServices(
            query=ProcessQueryServices(run=mock.Mock(side_effect=OSError("unavailable"))),
            signals=ProcessSignalServices(kill=mock.Mock(), pid_is_running=lambda _pid: False),
            log=log,
        )

        self.assertFalse(terminate_matching_processes(["target"], "target", services, platform_name="nt"))
        self.assertIn("process_query_failed", log.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
