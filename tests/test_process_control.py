import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from ciel_runtime_support.process_control import (
    ProcessControlServices,
    ProcessInspectionServices,
    ProcessQueryServices,
    ProcessSignalServices,
    ProcessTreeController,
    posix_process_rows,
    pid_is_running,
    process_command_line,
    process_cwd,
    process_environ_contains,
    terminate_matching_processes,
)


class ProcessControlTests(unittest.TestCase):
    def test_pid_liveness_rejects_invalid_and_finds_current_process(self):
        self.assertFalse(pid_is_running(0))
        self.assertTrue(pid_is_running(os.getpid()))

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

    def test_process_tree_discovers_nested_descendants(self):
        run = mock.Mock(
            return_value=subprocess.CompletedProcess([], 0, stdout="2 1\n3 2\n4 1\n")
        )
        services = ProcessControlServices(
            query=ProcessQueryServices(run=run),
            signals=ProcessSignalServices(kill=mock.Mock(), pid_is_running=lambda _pid: False),
            log=mock.Mock(),
        )
        self.assertEqual([4, 2, 3], ProcessTreeController(services, platform_name="posix").descendant_pids(1))

    def test_process_tree_follows_only_ciel_runtime_wrapper_parents(self):
        run = mock.Mock(
            side_effect=[
                subprocess.CompletedProcess([], 0, stdout="20 python ciel_runtime.py claude\n"),
                subprocess.CompletedProcess([], 0, stdout="30 shell wrapper\n"),
            ]
        )
        services = ProcessControlServices(
            query=ProcessQueryServices(run=run, current_pid=lambda: 90, parent_pid=lambda: 91),
            signals=ProcessSignalServices(kill=mock.Mock(), pid_is_running=lambda _pid: False),
            log=mock.Mock(),
        )
        self.assertEqual([20], ProcessTreeController(services, platform_name="posix").client_wrapper_parent_pids(10))

    def test_process_tree_terminates_children_through_signal_port(self):
        alive = {10, 11}
        kill = mock.Mock(side_effect=lambda pid, _signal: alive.discard(pid))
        run = mock.Mock(return_value=subprocess.CompletedProcess([], 0, stdout="11 10\n"))
        services = ProcessControlServices(
            query=ProcessQueryServices(run=run, current_pid=lambda: 90, parent_pid=lambda: 91),
            signals=ProcessSignalServices(
                kill=kill,
                pid_is_running=lambda pid: pid in alive,
                now=lambda: 0,
                sleep=mock.Mock(),
            ),
            log=mock.Mock(),
        )
        self.assertTrue(ProcessTreeController(services, platform_name="posix").terminate_tree(10, "test", quiet=True))
        self.assertEqual({10, 11}, {call.args[0] for call in kill.call_args_list})

    def test_process_tree_terminates_port_listeners_and_skips_protected_pids(self):
        alive = {10, 12}
        kill = mock.Mock(side_effect=lambda pid, _signal: alive.discard(pid))
        output = []
        services = ProcessControlServices(
            query=ProcessQueryServices(current_pid=lambda: 10, parent_pid=lambda: 11),
            signals=ProcessSignalServices(
                kill=kill,
                pid_is_running=lambda pid: pid in alive,
                now=lambda: 0,
                sleep=mock.Mock(),
            ),
            log=mock.Mock(),
            output=output.append,
        )
        controller = ProcessTreeController(services, platform_name="posix")

        self.assertTrue(
            controller.terminate_port(
                8788,
                "proxy",
                pids_on_port=lambda _port: [10, 12],
            )
        )

        kill.assert_called_once_with(12, mock.ANY)
        self.assertEqual(["Stopped existing proxy listener(s): 10, 12."], output)

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
