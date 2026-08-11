from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ciel_runtime_support.router_process_lifecycle import (
    RouterProcessConfig,
    RouterSpawnPorts,
    RouterStartupIdentity,
    RouterStartupStatePorts,
    runtime_version_is_newer,
    schedule_router_restart,
    start_router_if_needed,
)


class RouterProcessStartupTests(unittest.TestCase):
    def test_runtime_version_comparison_handles_nightly_and_stable_versions(self):
        self.assertTrue(
            runtime_version_is_newer(
                "0.2.21-nightly.20260811-105440.381a3de",
                "0.2.20-nightly.20260811-013950.69657a2",
            )
        )
        self.assertTrue(runtime_version_is_newer("0.2.21", "0.2.21-nightly.20260811-105440.381a3de"))
        self.assertFalse(runtime_version_is_newer("development", "0.2.21"))

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

    def test_stale_client_reuses_newer_router_from_same_config(self):
        popen = mock.Mock()
        ensure = mock.Mock()
        state = self._state(
            health={
                "version": "0.2.21-nightly.20260811-105440.381a3de",
                "source_fingerprint": "new-source",
            },
            config_matches=True,
            ensure=ensure,
        )

        result = start_router_if_needed(
            replace_active_clients=True,
            config=self._config(Path(".")),
            identity=RouterStartupIdentity(
                version="0.2.20-nightly.20260811-013950.69657a2",
                source_fingerprint="old-source",
            ),
            state=state,
            spawn=self._spawn(popen),
            executable="python",
            entrypoint=Path("runtime.py"),
            log_path=Path("router.log"),
            platform_name="posix",
        )

        self.assertTrue(result)
        ensure.assert_not_called()
        popen.assert_not_called()
        self.assertIn("router_newer_version_reused", state.log.call_args.args[1])

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
