import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from ciel_runtime_support.router_client_lifecycle import (
    ManagedRouterLifetime,
    ManagedRouterLifetimePorts,
    RoutedLaunchDiagnosticPorts,
    RoutedLaunchDiagnostics,
    RouterClientRegistry,
    RouterClientRegistryPorts,
    RouterLifetimeRunner,
    RouterLifetimeRunnerPorts,
)


class RouterClientLifecycleTests(unittest.TestCase):
    def test_registry_records_active_client_and_removes_stale_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = RouterClientRegistry(
                root,
                4141,
                RouterClientRegistryPorts(pid_is_running=lambda pid: pid == 7, log=lambda *_args: None),
            )
            active_path = registry.register(7)
            stale_path = registry.register(8)
            self.assertEqual([7], registry.active_pids())
            self.assertTrue(active_path.exists())
            self.assertFalse(stale_path.exists())
            registry.release(active_path)
            self.assertFalse(active_path.exists())

    def test_managed_router_reason_prioritizes_owner_death(self):
        lifetime = ManagedRouterLifetime(
            ManagedRouterLifetimePorts(
                active_client_pids=lambda: [],
                pid_is_running=lambda _pid: False,
                stop_router=lambda *_args, **_kwargs: True,
                log=lambda *_args: None,
            )
        )
        with mock.patch.dict(os.environ, {"CIEL_RUNTIME_MANAGED_ROUTER": "1"}):
            self.assertEqual("owner_dead_no_clients", lifetime.stop_reason(0, 22, 90))

    def test_managed_router_keeps_active_clients(self):
        lifetime = ManagedRouterLifetime(
            ManagedRouterLifetimePorts(
                active_client_pids=lambda: [7],
                pid_is_running=lambda _pid: False,
                stop_router=lambda *_args, **_kwargs: True,
                log=lambda *_args: None,
            )
        )
        with mock.patch.dict(os.environ, {"CIEL_RUNTIME_MANAGED_ROUTER": "1"}):
            self.assertIsNone(lifetime.stop_reason(0, 22, 1))
        self.assertFalse(lifetime.stop_if_idle("test"))

    def test_runner_releases_lease_and_requests_idle_stop(self):
        calls = []
        stop_event = None

        def start_supervisor(event):
            nonlocal stop_event
            stop_event = event
            return threading.Thread()

        runner = RouterLifetimeRunner(
            RouterLifetimeRunnerPorts(
                register_client=lambda: Path("7.json"),
                release_client=lambda path: calls.append(("release", path)),
                start_supervisor=start_supervisor,
                stop_if_idle=lambda reason, quiet=True: calls.append((reason, quiet)) or True,
                log=lambda *_args: None,
            )
        )
        self.assertEqual(3, runner.run(lambda: 3, True))
        self.assertTrue(stop_event.is_set())
        self.assertEqual([("release", Path("7.json")), ("claude_exit", True)], calls)

    def test_diagnostics_filter_reads_only_relevant_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "router.log"
            log_path.write_text("noise\n[WARN] retry\nupstream_failed\n", encoding="utf-8")
            diagnostics = RoutedLaunchDiagnostics(
                "http://router",
                log_path,
                RoutedLaunchDiagnosticPorts(
                    router_health=lambda: None,
                    health_summary=lambda _health: "down",
                    provider_summary=lambda _provider, _config: "upstream=test",
                    log=lambda *_args: None,
                ),
            )
            self.assertEqual(["[WARN] retry", "upstream_failed"], diagnostics.recent_lines())
            self.assertTrue(diagnostics.should_print(1, []))
            self.assertFalse(diagnostics.should_print(0, ["router_spawned"]))


if __name__ == "__main__":
    unittest.main()
