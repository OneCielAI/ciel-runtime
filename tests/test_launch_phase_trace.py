"""Launch phase trace events and the launcher's own startup lease lifecycle."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ciel_runtime_support import runtime_launch
from ciel_runtime_support import workspace_router_selection as selection
from ciel_runtime_support.remote_instructions import RemoteInstructionSynchronizer, SynchronizedLaunch
from ciel_runtime_support.remote_memory import RemoteMemorySynchronizer
from ciel_runtime_support.remote_provision import RemoteProvisioner


END = re.compile(r"launch_phase_end runtime=(?P<runtime>\S+) phase=(?P<phase>\S+)(?: \S+=\S+)* elapsed_ms=\d+ result=(?P<result>\S+)")


def _clock(*values: float) -> SimpleNamespace:
    ticks = iter(values)
    return SimpleNamespace(monotonic=lambda: next(ticks))


class LaunchPhaseEventTests(unittest.TestCase):
    def test_phase_logs_start_and_end_with_elapsed_and_result(self):
        lines = []
        value = runtime_launch._launch_phase(
            "claude", "mcp_projection", lambda: "value", lambda level, text: lines.append((level, text)),
            result=lambda _value: "prepared",
        )
        self.assertEqual("value", value)
        self.assertEqual(("INFO", "launch_phase_start runtime=claude phase=mcp_projection"), lines[0])
        self.assertRegex(lines[1][1], r"^launch_phase_end runtime=claude phase=mcp_projection elapsed_ms=\d+ result=prepared$")

    def test_failed_phase_records_only_the_error_type(self):
        lines = []

        def fail():
            raise RuntimeError("Authorization: Bearer sk-live-secret")

        with self.assertRaises(RuntimeError):
            runtime_launch._launch_phase("codex", "router_start", fail, lambda _level, text: lines.append(text))
        self.assertRegex(lines[-1], r"result=error error_type=RuntimeError$")
        self.assertNotIn("sk-live-secret", "\n".join(lines))
        self.assertNotIn("Bearer", "\n".join(lines))

    def test_console_progress_only_for_slow_phases(self):
        for ticks, expected in (((0.0, 0.2), ""), ((0.0, 2.5), "Ciel Runtime startup: router_start completed in 2.5s.\n")):
            with self.subTest(ticks=ticks), mock.patch.object(runtime_launch, "time", _clock(*ticks)):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    runtime_launch._launch_phase("claude", "router_start", lambda: None, lambda *_args: None)
                self.assertEqual(expected, output.getvalue())

    def test_child_session_records_transport_and_exit(self):
        lines = []
        rc = runtime_launch._child_session("agy", "direct", lambda: 3, lambda _level, text: lines.append(text))
        self.assertEqual(3, rc)
        self.assertEqual("launch_phase_start runtime=agy phase=child_spawn transport=direct", lines[0])
        self.assertRegex(lines[1], r"^launch_phase_end runtime=agy phase=child_spawn transport=direct elapsed_ms=\d+ result=exit_3$")

    def test_import_time_port_selection_is_replayed_once_per_launch_attempt(self):
        lines = []
        log = lambda _level, text: lines.append(text)  # noqa: E731
        with mock.patch.object(runtime_launch, "ROUTER_PORT_SELECTION_ELAPSED_MS", 7518), \
                mock.patch.object(runtime_launch, "ROUTER_PORT_SELECTION_RESULT", "selected"):
            def launch():
                runtime_launch._log_router_port_selection("claude", log)
                # A runtime the menu dispatches to shares the same attempt.
                runtime_launch._log_router_port_selection("codex", log)
                return 0

            runtime_launch._guard_launch_lease("claude", launch, log)
        self.assertEqual(
            ["launch_phase_end runtime=claude phase=runtime_paths.router_port_select elapsed_ms=7518 result=selected"],
            lines,
        )

    def test_agy_production_assembly_passes_the_router_log(self):
        import ciel_runtime

        self.assertIs(ciel_runtime.router_log, ciel_runtime.agy_launch_services().routing.router_log)

    def test_remote_components_trace_without_urls_or_credentials(self):
        secret_url = "https://config.example/private?token=abc123"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lines = []
            log = lambda _level, text: lines.append(text)  # noqa: E731
            RemoteInstructionSynchronizer(lambda: {"remote_instructions": {"enabled": False, "claude_url": secret_url}},
                                          lambda: root, root, log).sync("claude")
            RemoteMemorySynchronizer(lambda: {"remote_memory": {"enabled": True, "manifest_url": "ftp://bad"}},
                                     lambda: root, root, log).sync("claude")
            RemoteProvisioner(lambda: {"remote_provision": {"enabled": False, "manifest_url": secret_url}},
                              lambda: root, root, log).provision()
        phases = [match.groupdict() for match in map(END.match, lines) if match]
        self.assertEqual(
            [("claude", "remote_instructions", "disabled"), ("claude", "remote_memory", "failed"),
             ("all", "remote_provision", "disabled")],
            [(item["runtime"], item["phase"], item["result"]) for item in phases],
        )
        phase_lines = "\n".join(line for line in lines if line.startswith("launch_phase_"))
        self.assertNotIn("token", phase_lines)
        self.assertNotIn("config.example", phase_lines)


class LaunchLeaseLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.released = []
        patcher = mock.patch.object(
            runtime_launch, "release_current_launch_lease", lambda: self.released.append(1) or True
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def guarded(self, operation):
        return runtime_launch._guard_launch_lease("claude", operation, lambda *_args: None)

    def test_pre_child_failure_releases_once_across_menu_dispatch(self):
        def inner():
            raise RuntimeError("codex executable was not found")

        with self.assertRaises(RuntimeError):
            self.guarded(lambda: runtime_launch._guard_launch_lease("codex", inner, lambda *_args: None))
        self.assertEqual([1], self.released)

    def test_failure_after_child_spawn_keeps_the_lease(self):
        def spawned_then_failed():
            runtime_launch._child_session("claude", "direct", lambda: 0, lambda *_args: None)
            raise RuntimeError("restart bookkeeping failed")

        with self.assertRaises(RuntimeError):
            self.guarded(spawned_then_failed)
        self.assertEqual([], self.released)

    def test_spawn_call_failure_is_still_a_pre_child_failure(self):
        def spawn_failed():
            raise FileNotFoundError("executable disappeared")

        with self.assertRaises(FileNotFoundError):
            self.guarded(lambda: runtime_launch._child_session("claude", "direct", spawn_failed, lambda *_args: None))
        self.assertEqual([1], self.released)

    def test_successful_launch_keeps_the_lease_for_the_launcher_lifetime(self):
        self.assertEqual(0, self.guarded(lambda: 0))
        self.assertEqual([], self.released)

    def test_release_failure_does_not_mask_the_launch_error(self):
        lines = []

        def broken_release():
            raise OSError("registry locked")

        def fail():
            raise ValueError("launch failed")

        with mock.patch.object(runtime_launch, "release_current_launch_lease", broken_release):
            with self.assertRaisesRegex(ValueError, "launch failed"):
                runtime_launch._guard_launch_lease("claude", fail, lambda level, text: lines.append((level, text)))
        self.assertEqual([("WARN", "launch_lease_release_failed runtime=claude error_type=OSError")], lines)

    def test_every_runtime_entry_point_releases_on_a_pre_child_failure(self):
        def services(missing_executable: str):
            services = mock.MagicMock()
            services.dispatch.run_prelaunch_menu.return_value = 0
            setattr(services.installation, missing_executable, mock.Mock(return_value=None))
            return services

        claude = mock.MagicMock()
        claude.dispatch.run_prelaunch_menu.return_value = 0
        claude.config.get_current_provider.side_effect = OSError("config unreadable")
        cases = (
            (runtime_launch.run_claude, claude, OSError),
            (runtime_launch.run_codex, services("install_codex_if_missing"), RuntimeError),
            (runtime_launch.run_codex_app_server, services("install_codex_if_missing"), RuntimeError),
            (runtime_launch.run_agy, services("install_agy_if_missing"), RuntimeError),
        )
        for function, stub, error in cases:
            with self.subTest(function=function.__name__):
                self.released.clear()
                with self.assertRaises(error):
                    function([], services=stub)
                self.assertEqual([1], self.released)
                logged = [call.args[1] for call in stub.routing.router_log.call_args_list]
                self.assertTrue(any(text.startswith("launch_lease_release ") and "result=released" in text
                                    for text in logged), logged)

    def test_synchronized_launch_releases_when_remote_assets_block_the_launch(self):
        released = []

        def provisioning_failed(_runtime, **_kwargs):
            raise SystemExit(2)

        launch = SynchronizedLaunch(lambda: self.fail("delegate must not run"), provisioning_failed, "claude",
                                    release=lambda: released.append(1))
        with self.assertRaises(SystemExit):
            launch()
        self.assertEqual([1], released)

    def test_synchronized_launch_success_keeps_lease_and_reports_slow_assets(self):
        released, output = [], []
        launch = SynchronizedLaunch(lambda: 5, lambda *_args, **_kwargs: None, "codex",
                                    release=lambda: released.append(1), output=output.append)
        from ciel_runtime_support import remote_instructions

        with mock.patch.object(remote_instructions, "time", _clock(0.0, 1.5)):
            self.assertEqual(5, launch())
        self.assertEqual([], released)
        self.assertEqual(["Ciel Runtime startup: remote assets completed in 1.5s."], output)


# A launcher that took its startup lease and is still alive. With "release" it
# then drops the lease the way a pre-child launch failure does.
_HOLDER = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import ciel_runtime_support.workspace_router_selection as s
registry, mode = Path(sys.argv[2]), sys.argv[3]
port = s.select_workspace_router_port(9464, Path("C:/work/held"), {}, registry_path=registry,
                                      health=lambda _p: None, available=lambda _p: True)
released = s.release_workspace_router_launch_lease(Path("C:/work/held"), {}, registry_path=registry) if mode == "release" else False
print(f"{port} {released}", flush=True)
time.sleep(60)
"""


class SameWorkspaceSecondLaunchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.registry = Path(temporary.name) / "workspace-router-ports.json"

    def holder(self, mode: str) -> tuple[subprocess.Popen, str]:
        root = str(Path(selection.__file__).resolve().parents[1])
        environ = {key: value for key, value in os.environ.items() if not key.startswith("CIEL_RUNTIME_")}
        process = subprocess.Popen([sys.executable, "-c", _HOLDER, root, str(self.registry), mode],
                                   stdout=subprocess.PIPE, text=True, env=environ)
        self.addCleanup(process.stdout.close)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        line = process.stdout.readline().strip()
        self.assertTrue(line, "holder did not report its port")
        return process, line

    def select(self):
        return selection.select_workspace_router_port(
            9464, Path("C:/work/held"), {}, registry_path=self.registry,
            health=lambda _port: None, available=lambda _port: True,
        )

    def test_second_launch_fails_fast_with_a_clear_message_and_stops_nothing(self):
        holder, line = self.holder("hold")
        self.assertEqual("9464 False", line)
        before = self.registry.read_bytes()
        started = time.monotonic()
        with self.assertRaises(RuntimeError) as raised:
            self.select()
        self.assertLess(time.monotonic() - started, 5.0)
        message = str(raised.exception)
        self.assertIn("launch already reserved", message)
        self.assertIn(f"pid {holder.pid}", message)
        self.assertIn("nothing was stopped", message)
        self.assertIsNone(holder.poll())
        self.assertEqual(before, self.registry.read_bytes())

    def test_unknown_holder_identity_stays_fail_closed(self):
        holder, _line = self.holder("hold")
        before = self.registry.read_bytes()
        own = selection._process_identity(os.getpid())
        with mock.patch.object(selection, "_process_identity",
                               side_effect=lambda pid: own if pid == os.getpid() else None):
            with self.assertRaisesRegex(RuntimeError, "launch already reserved"):
                self.select()
        self.assertIsNone(holder.poll())
        self.assertEqual(before, self.registry.read_bytes())

    def test_released_pre_child_lease_lets_the_next_launch_take_the_same_port(self):
        holder, line = self.holder("release")
        self.assertEqual("9464 True", line)
        record = json.loads(self.registry.read_text())["workspaces"][selection.workspace_digest("C:/work/held")]
        self.assertNotIn("launch_lease", record)
        self.assertEqual(9464, self.select())
        self.assertIsNone(holder.poll())
        lease = json.loads(self.registry.read_text())["workspaces"][selection.workspace_digest("C:/work/held")]["launch_lease"]
        self.assertEqual(os.getpid(), lease["pid"])


if __name__ == "__main__":
    unittest.main()
