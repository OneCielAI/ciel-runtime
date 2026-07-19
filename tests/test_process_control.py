import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from ciel_runtime_support.process_control import (
    ProcessControlServices,
    ProcessInspectionServices,
    ProcessQueryServices,
    ProcessSignalServices,
    posix_process_rows,
    process_command_line,
    process_cwd,
    process_environ_contains,
    terminate_matching_processes,
)


class ProcessControlTests(unittest.TestCase):
    def inspection_services(self, **updates):
        values = {
            "run": mock.Mock(),
            "read_bytes": mock.Mock(),
            "readlink": mock.Mock(),
            "username": lambda: "agent",
            "log": mock.Mock(),
        }
        values.update(updates)
        return ProcessInspectionServices(**values)

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

    def test_process_command_line_query_failure_is_observable(self):
        log = mock.Mock()
        services = self.inspection_services(run=mock.Mock(side_effect=OSError("missing ps")), log=log)

        self.assertEqual("", process_command_line(12, services, platform_name="posix"))
        self.assertIn("process_command_line_query_failed", log.call_args.args[1])

    def test_process_environment_and_cwd_use_injected_filesystem(self):
        services = self.inspection_services(
            read_bytes=mock.Mock(return_value=b"A=1\0CIEL_RUNTIME_CODEX_MANAGED=1\0"),
            readlink=mock.Mock(return_value="/workspace"),
        )

        self.assertTrue(
            process_environ_contains(
                12,
                "CIEL_RUNTIME_CODEX_MANAGED",
                "1",
                services,
                platform_name="posix",
            )
        )
        self.assertEqual(Path("/workspace").resolve(), process_cwd(12, services, platform_name="posix"))

    def test_posix_process_rows_parses_valid_records(self):
        run = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [],
                0,
                stdout="12 S codex --yolo\ninvalid\n13 Z codex app-server\n",
            )
        )
        services = self.inspection_services(run=run)

        self.assertEqual(
            [(12, "S", "codex --yolo"), (13, "Z", "codex app-server")],
            posix_process_rows(services),
        )


if __name__ == "__main__":
    unittest.main()
