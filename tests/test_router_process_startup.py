from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ciel_runtime_support.router_process_lifecycle import (
    RouterProcessConfig,
    RouterSpawnPorts,
    RouterStartupIdentity,
    RouterStartupStatePorts,
    schedule_router_restart,
    start_router_if_needed,
)


class RouterProcessStartupTests(unittest.TestCase):
    def test_scheduled_restart_executes_router_entrypoint_and_logs_failure(self):
        calls = []
        logs = []

        class ImmediateTimer:
            daemon = False

            def __init__(self, delay, callback):
                calls.append(("delay", delay))
                self.callback = callback

            def start(self):
                self.callback()

        def fail_exec(executable, argv):
            calls.append((executable, argv))
            raise OSError("blocked")

        schedule_router_restart(
            0.5,
            Path("runtime.py"),
            lambda level, message: logs.append((level, message)),
            timer_factory=ImmediateTimer,
            exec_process=fail_exec,
            executable="python",
        )

        self.assertEqual(("delay", 0.5), calls[0])
        self.assertEqual(
            ("python", ["python", "runtime.py", "serve"]), calls[1]
        )
        self.assertEqual("INFO", logs[0][0])
        self.assertEqual("ERROR", logs[-1][0])

    def test_matching_router_is_reused_when_policy_allows(self):
        popen = mock.Mock()
        result = start_router_if_needed(
            replace_active_clients=True,
            config=self._config(Path(".")),
            identity=self._identity(),
            state=self._state(health={"version": "1"}, matches=True, reuse=True),
            spawn=self._spawn(popen),
            executable="python",
            entrypoint=Path("runtime.py"),
            log_path=Path("router.log"),
            platform_name="posix",
        )
        self.assertTrue(result)
        popen.assert_not_called()

    def test_version_mismatch_with_active_clients_requires_replacement(self):
        with self.assertRaisesRegex(RuntimeError, "active clients"):
            start_router_if_needed(
                replace_active_clients=False,
                config=self._config(Path(".")),
                identity=self._identity(),
                state=self._state(health={"version": "old"}, active=[7], config_matches=True),
                spawn=self._spawn(mock.Mock()),
                executable="python",
                entrypoint=Path("runtime.py"),
                log_path=Path("router.log"),
                platform_name="posix",
            )

    def test_missing_router_spawns_managed_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            popen = mock.Mock()
            ensure = mock.Mock()
            state = self._state(health=None, ensure=ensure)
            times = iter((0.0, 0.0, 0.1))
            result = start_router_if_needed(
                replace_active_clients=True,
                config=self._config(root),
                identity=self._identity(),
                state=state,
                spawn=self._spawn(popen, now=lambda: next(times), router_up=lambda: True),
                executable="python",
                entrypoint=Path("runtime.py"),
                log_path=root / "router.log",
                platform_name="posix",
            )
            self.assertTrue(result)
            ensure.assert_called_once_with("pre_spawn", None)
            self.assertEqual("1", popen.call_args.kwargs["env"]["CIEL_RUNTIME_MANAGED_ROUTER"])
            self.assertTrue(popen.call_args.kwargs["start_new_session"])

    @staticmethod
    def _config(root):
        return RouterProcessConfig(
            pid_path=root / "router.pid",
            router_port=4141,
            router_base="http://router",
            config_dir=root,
        )

    @staticmethod
    def _identity():
        return RouterStartupIdentity(version="1", source_fingerprint="source")

    @staticmethod
    def _state(*, health, active=None, matches=False, config_matches=False, reuse=False, ensure=None):
        return RouterStartupStatePorts(
            health=lambda: health,
            active_client_pids=lambda: list(active or []),
            health_matches_current=lambda _health: matches,
            health_config_matches_current=lambda _health: config_matches,
            terminate_active_clients=mock.Mock(),
            ensure_port_available=ensure or mock.Mock(),
            reuse_enabled=lambda: reuse,
            log=mock.Mock(),
        )

    @staticmethod
    def _spawn(popen, *, now=lambda: 0.0, router_up=lambda: False):
        return RouterSpawnPorts(
            popen=popen,
            router_up=router_up,
            now=now,
            sleep=mock.Mock(),
            process_id=lambda: 77,
            environment=lambda: {},
        )


if __name__ == "__main__":
    unittest.main()
